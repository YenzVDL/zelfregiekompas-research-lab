import json
import pytest
from fastapi.testclient import TestClient
from backend import bank_questions
from backend.main import create_app
from backend.store import Store
from backend.research import Research
from backend.jobs import Jobs
from .test_question_banks import generated, result


def test_duplicate_reference_feedback_continue_and_resume_slots(lab):
    s, r, p, jobs = lab
    bank = r.bank("Diversity")
    original = r.question(generated(), bank_id=bank["id"])
    calls = []
    def generate(model, system, prompt, settings, cancel, **kw):
        calls.append(prompt)
        sequence = int(prompt.split("This is item ")[1].split(" ")[0])
        q = generated() if sequence == 2 else generated(f"What would you choose to explore in learning step {sequence}?")
        return result(q, system, prompt, kw["response_schema"])
    p.generate = generate
    jobs.schedule = lambda _: None
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model", "count": 3, "themes": ["progress"]})
    jobs._run(job["id"])
    partial = s.get("jobs", job["id"])
    assert partial["status"] == "completed_with_errors"
    assert (partial["requested_count"], partial["saved_count"], partial["failed_count"], partial["pending_count"]) == (3, 2, 1, 0)
    assert set(partial["question_items"]) == {"1", "2", "3"}
    assert original["text"] in calls[0] and "learning step 1?" in calls[1]
    retry = partial["question_attempts"][2]
    assert '"repeated_text": "' + original["text"] in retry["prompt"]
    assert "cosmetic paraphrase" in retry["prompt"]
    assert retry["errors"][0]["type"] == "exact_duplicate"
    assert all(a["system_prompt"] == bank_questions.SYSTEM and a["settings"]["temperature"] == .4 for a in partial["question_attempts"])
    assert len({a["focus"] for a in partial["question_attempts"][:4]}) >= 3
    preserved = [s.get("questions", identifier) for identifier in partial["results"]]
    resume_calls = []
    def resume_generate(model, system, prompt, settings, cancel, **kw):
        resume_calls.append(prompt)
        # First resume attempt still duplicates a previously saved draft.
        q = generated(preserved[0]["text"]) if len(resume_calls) == 1 else generated("What evidence might help you decide when to revisit your learning plan?")
        return result(q, system, prompt, kw["response_schema"])
    p.generate = resume_generate
    jobs.schedule = jobs._run
    jobs.resume(job["id"])
    final = s.get("jobs", job["id"])
    assert final["status"] == "completed" and final["saved_count"] == 3 and final["failed_count"] == 0
    assert final["results"][:2] == partial["results"] and len(set(final["results"])) == 3
    assert all("This is item 2 of 3" in prompt for prompt in resume_calls)
    assert [s.get("questions", q["id"]) for q in preserved] == preserved
    assert len(final["question_attempts"]) == 7
    assert len(r.latest("questions", bank_id=bank["id"])) == 4  # includes pre-existing draft


def test_reference_budget_and_focus_rotation():
    existing = [{"text": f"Which learning decision {i} would you consider " + "x" * 250 + "?", "tags": ["progress"], "review_status": "awaiting_review"} for i in range(200)]
    config = {"themes": ["progress", "time management"], "count": 40, "settings": {"num_ctx": 4096}, "instructions": ""}
    refs, budget = bank_questions.references(config, 9, existing)
    assert 0 < len(refs) < len(existing)
    assert len(json.dumps(refs, ensure_ascii=False).encode()) <= budget <= bank_questions.REFERENCE_BYTE_LIMIT
    assert existing[-1]["text"] in refs
    assert bank_questions.assignment(config, 1) != bank_questions.assignment(config, 3)
    assert bank_questions.assignment(config, 1)[0] == bank_questions.assignment(config, 3)[0] == "progress"
    assert bank_questions.assignment(config, 2)[0] == "time management"
    assert len({bank_questions.assignment({**config, "themes": ["progress"]}, sequence) for sequence in range(1, 41)}) == 40


def test_repeated_failures_stop_bounded_and_preserve_pending(lab):
    s, r, p, jobs = lab
    bank = r.bank("No progress")
    r.question(generated(), bank_id=bank["id"])
    p.generate = lambda model, system, prompt, settings, cancel, **kw: result(generated(), system, prompt, kw["response_schema"])
    jobs.schedule = lambda _: None
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model", "count": 40})
    jobs._run(job["id"])
    final = s.get("jobs", job["id"])
    assert final["status"] == "failed" and "consecutive" in final["error"]
    assert len(final["question_attempts"]) == 9 <= final["run_attempt_limit"]
    assert (final["saved_count"], final["failed_count"], final["pending_count"]) == (0, 3, 37)


def test_legacy_resume_keeps_saved_versions(lab):
    s, r, p, jobs = lab
    bank = r.bank("Legacy resume")
    saved = r.question(generated(), bank_id=bank["id"], generation={"sequence": 1})
    jobs.schedule = lambda _: None
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model", "count": 2})
    s.patch("jobs", job["id"], status="failed", results=[saved["id"]], done=1)
    p.generate = lambda model, system, prompt, settings, cancel, **kw: result(generated("What support might help you decide on a next step?"), system, prompt, kw["response_schema"])
    jobs.schedule = jobs._run
    jobs.resume(job["id"])
    final = s.get("jobs", job["id"])
    assert final["saved_count"] == 2 and len(final["question_attempts"]) == 1
    assert final["question_attempts"][0]["sequence"] == 2
    assert s.get("questions", saved["id"]) == saved


def test_archive_persists_and_keeps_execution_and_exports(lab):
    s, r, p, jobs = lab
    record = {"id": "archive-fixture", "kind": "question_generation", "status": "completed_with_errors", "config": {}, "total": 0, "done": 0, "results": [], "question_attempts": [{"raw_response": "retained"}]}
    s.put("jobs", record)
    with TestClient(create_app(s, p)) as client:
        assert client.post('/api/jobs/archive-fixture/archive').json()["archived"] is True
        assert client.get('/api/jobs/archive-fixture/export').json()["job"]["question_attempts"] == record["question_attempts"]
        assert client.get('/api/jobs').json()[0]["archived"] is True
        assert Store(s.path).get("jobs", record["id"])["archived"] is True
        assert client.post('/api/jobs/archive-fixture/unarchive').json()["archived"] is False
        s.patch("jobs", record["id"], status="running")
        assert client.post('/api/jobs/archive-fixture/archive').status_code == 422
        s.patch("jobs", record["id"], status="terminated")
        assert client.post('/api/jobs/archive-fixture/archive').json()["archived"] is True
