import copy
import json
import threading
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.research import Research, fingerprint
from backend.store import Store
from backend.jobs import Jobs
from backend import bank_questions
from backend.metrics import report
from .conftest import approved_inputs, wait_job
from backend.provider import Cancelled, Ollama


def generated(text="What would you like to try in your next learning activity?", **changes):
    return {"text": text, "tags": ["next steps"], "conditions": {"present": [], "missing": [], "contains": {}}, "priority": 0, "origin": bank_questions.ORIGIN, **changes}


def result(question, system, prompt, schema):
    return {"text": json.dumps(question), "runtime": {"done_reason": "stop"}, "latency_seconds": .01,
            "request": {"capture_source": "TEST FIXTURE", "payload": {"model": "test-fixture-model", "system": system, "prompt": prompt, "format": schema}},
            "effective_settings": {"think": False, "stream": True}}


def test_legacy_migration_preserves_ids_versions_reviews_and_frozen_records(tmp_path):
    store = Store(tmp_path / "legacy.db")
    old = {**generated(), "id": "old-version", "question_id": "old-question", "version": 1, "review_status": "approved", "parent_id": None}
    newer = {**old, "id": "old-version-2", "version": 2, "parent_id": old["id"], "review_status": "awaiting_review"}
    snapshot = {"id": "old-bank-snapshot", "kind": "bank", "records": [old], "content_hash": fingerprint([old]), "name": "Old frozen bank"}
    experiment = {"id": "old-experiment", "kind": "experiment", "bank_snapshot_id": snapshot["id"], "status": "completed"}
    review = {"id": "old-review", "version_id": old["id"], "decision": "approved"}
    for kind, records in {"questions": [old, newer], "snapshots": [snapshot], "jobs": [experiment], "reviews": [review]}.items():
        for record in records:
            store.put(kind, record)
    research = Research(store)
    for question in [old, newer]:
        assert store.get("questions", question["id"]) == {**question, "bank_id": bank_questions.DEFAULT_BANK_ID}
    assert store.get("snapshots", snapshot["id"]) == snapshot
    assert store.get("jobs", experiment["id"]) == experiment
    assert store.get("reviews", review["id"]) == review
    assert research.bank_snapshot_identity(snapshot)["review_mode"] == "approved"
    Research(store)
    assert len(store.all("banks")) == 1


def test_bank_isolation_revision_duplicate_rules_and_atomic_import(lab):
    s, r, p, _ = lab
    first, second = r.bank("First"), r.bank("Second", "Independent collection")
    q = r.question(generated(), bank_id=first["id"])
    same = r.question(generated(), bank_id=second["id"])
    assert q["question_id"] != same["question_id"]
    with pytest.raises(ValueError, match="Exact duplicate"):
        r.question(generated(), bank_id=first["id"])
    with pytest.raises(ValueError, match="original bank"):
        r.question(generated("What would you like to reflect on next?"), q["id"], second["id"])
    revision = r.question(generated("What would you like to reflect on next?"), q["id"], first["id"])
    assert revision["version"] == 2 and revision["question_id"] == q["question_id"] and revision["bank_id"] == first["id"]
    with TestClient(create_app(s, p)) as client:
        assert len(client.get("/api/questions", params={"bank_id": first["id"]}).json()) == 1
        assert len(client.get("/api/questions", params={"bank_id": second["id"], "history": True}).json()) == 1
        assert client.get("/api/questions", params={"bank_id": ""}).status_code == 422
        before = len(s.all("questions"))
        response = client.post("/api/questions/import", json={"bank_id": first["id"], "questions": [generated("What could you explore about your learning approach?"), generated(revision["text"])]})
        assert response.status_code == 422 and len(s.all("questions")) == before


def test_snapshot_modes_are_bank_specific_immutable_and_keep_statuses(lab):
    s, r, _, _ = lab
    first, second = r.bank("First"), r.bank("Second")
    approved = r.question(generated(), bank_id=first["id"])
    pending = r.question(generated("Which learning approach would you like to explore?"), bank_id=first["id"])
    rejected = r.question(generated("What could you change in your learning plan?"), bank_id=first["id"])
    r.review("questions", approved["id"], "approved", "reviewer", "Reviewed")
    r.review("questions", rejected["id"], "rejected", "reviewer", "Rejected")
    r.question(generated(), bank_id=second["id"])
    regular = r.snapshot("bank", "Approved", bank_id=first["id"])
    experimental = r.snapshot("bank", "Exploratory", bank_id=first["id"], review_mode="experimental")
    assert [q["id"] for q in regular["records"]] == [approved["id"]]
    assert {q["id"] for q in experimental["records"]} == {approved["id"], pending["id"]}
    assert experimental["review_counts"] == {"approved": 1, "awaiting_review": 1}
    assert regular["content_hash"] != experimental["content_hash"] and experimental["usable_fallback"]
    r.review("questions", pending["id"], "rejected", "reviewer", "Later decision")
    assert s.get("snapshots", experimental["id"]) == experimental
    with pytest.raises(ValueError, match="No approved"):
        r.snapshot("bank", "No implicit approval", bank_id=second["id"])


def test_experimental_development_only_fixed_offline_and_condition_identity(lab):
    s, r, p, jobs = lab
    _, _, _, dataset, _ = approved_inputs(r)
    bank = r.bank("Exploratory")
    r.question(generated(), bank_id=bank["id"])
    snapshot = r.snapshot("bank", "Exploratory 1", bank_id=bank["id"], review_mode="experimental")
    snapshot2 = r.snapshot("bank", "Exploratory 2", bank_id=bank["id"], review_mode="experimental")
    # Create split fixtures directly from already-frozen synthetic records.
    for split in ["validation", "test"]:
        other = {**dataset, "id": "dataset-" + split, "records": [{**dataset["records"][0], "split": split}]}
        s.put("snapshots", other)
        with pytest.raises(ValueError, match="restricted to development"):
            jobs.create_experiment({"name": "Blocked", "dataset_snapshot_id": other["id"], "bank_snapshot_id": snapshot["id"], "split": split, "methods": ["fixed"]})
    def offline():
        raise AssertionError("Fixed experiments without AI control must not contact Ollama")
    p.models = offline
    for frozen in [snapshot, snapshot2]:
        job = jobs.create_experiment({"name": "Development fixed", "dataset_snapshot_id": dataset["id"], "bank_snapshot_id": frozen["id"], "methods": ["fixed"]})
        assert wait_job(s, job["id"])["status"] == "completed"
        output = next(o for o in s.all("outputs") if o["job_id"] == job["id"])
        assert output["status"] == "completed" and output["bank_snapshot"]["review_mode"] == "experimental"
        assert output["bank_snapshot"]["content_hash"] == frozen["content_hash"]
    outputs = [o for o in s.all("outputs") if o.get("bank_snapshot", {}).get("bank_id") == bank["id"]]
    assert len({o["condition_id"] for o in outputs}) == 2
    metrics = report(outputs, [], {"active_seconds": 1, "thresholds": {}, "bank_snapshot": job["bank_snapshot"]})
    assert len(metrics["quality"]["human"]["by_condition"]) == 2


def test_question_generation_retries_partial_resume_and_actual_prompt_exports(lab):
    s, r, p, jobs = lab
    bank = r.bank("Generated")
    values = iter([generated(), {**generated(), "conditions": {"unknown_operator": []}}, {"text": ""}, []])
    def generate(model, system, prompt, settings, cancel, **kwargs):
        return result(next(values), system, prompt, kwargs["response_schema"])
    p.generate = generate
    jobs.schedule = lambda _: None
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model", "count": 2})
    jobs._run(job["id"])
    failed = s.get("jobs", job["id"])
    assert failed["status"] == "completed_with_errors" and failed["done"] == 1 and len(failed["question_attempts"]) == 4
    assert failed["saved_count"] == 1 and failed["failed_count"] == 1 and failed["pending_count"] == 0
    saved = s.get("questions", failed["results"][0])
    assert saved["bank_id"] == bank["id"] and saved["review_status"] == "awaiting_review"
    assert saved["generation"]["request"]["payload"]["system"] == bank_questions.SYSTEM
    assert saved["generation"]["model_metadata"]["digest"] == "fixture"
    p.generate = lambda model, system, prompt, settings, cancel, **kwargs: result(generated("What might you try differently in your next learning activity?"), system, prompt, kwargs["response_schema"])
    jobs.schedule = jobs._run
    jobs.resume(job["id"])
    resumed = s.get("jobs", job["id"])
    assert resumed["done"] == 2 and resumed["status"] == "completed" and resumed["results"][0] == saved["id"]
    with TestClient(create_app(s, p)) as client:
        question_export = client.get(f'/api/questions/{saved["id"]}/export').json()
        assert question_export["question"]["generation"] == saved["generation"]
        bank_export = client.get(f'/api/banks/{bank["id"]}/export').json()
        assert len(bank_export["questions"]) == 2
        assert len(client.get(f'/api/jobs/{job["id"]}/export').json()["generated_questions"]) == 2


@pytest.mark.parametrize("change", [
    {"text": "   "}, {"conditions": {"contains": {"review_status": "approved"}}},
    {"conditions": {"present": ["progress"], "missing": ["progress"]}}, {"conditions": {"equals": {}}},
    {"priority": True}, {"priority": "2"}, {"text": "Which concept? Another one?"},
])
def test_generated_question_structure_and_conditions_strict(change):
    with pytest.raises(ValueError):
        bank_questions.parse(json.dumps(generated(**change)))


def test_exact_duplicates_retry_near_duplicates_are_retained_and_flagged(lab):
    s, r, p, jobs = lab
    bank = r.bank("Duplicates")
    r.question(generated(), bank_id=bank["id"])
    p.generate = lambda model, system, prompt, settings, cancel, **kw: result(generated(), system, prompt, kw["response_schema"])
    jobs.schedule = lambda _: None
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model", "count": 1})
    jobs._run(job["id"])
    assert "Exact duplicate" in s.get("jobs", job["id"])["error"]
    near = r.question(generated("What would you like to try in your next learning session?"), bank_id=bank["id"])
    assert any("near-duplicate" in warning for warning in near["warnings"])


def test_generation_does_not_assume_obstacles_and_uses_recorded_attempt_seeds(lab):
    with pytest.raises(ValueError, match="assumes an obstacle"):
        bank_questions.parse(json.dumps(generated("What approach did you use to manage obstacles in your learning process?")))
    assert bank_questions.parse(json.dumps(generated("What might help you manage obstacles if they arise?")))
    s, r, p, jobs = lab
    bank = r.bank("Retry seed fixture")
    observations = []
    def generate(model, system, prompt, settings, cancel, **kw):
        observations.append((settings["seed"], json.loads(prompt.split("THEME: ", 1)[1].split("\n", 1)[0])))
        # Force one invalid request before succeeding, then a second saved item.
        output = [] if len(observations) == 1 else generated("What would you like to reflect on next?" if len(observations) == 2 else "Which learning approach would you like to explore?")
        return result(output, system, prompt, kw["response_schema"])
    p.generate = generate
    jobs.schedule = lambda _: None
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model", "count": 2, "themes": ["progress", "learning strategy"]})
    jobs._run(job["id"])
    final = s.get("jobs", job["id"])
    assert final["status"] == "completed" and observations == [(42, "progress"), (43, "progress"), (45, "learning strategy")]
    assert [a["settings"]["seed"] for a in final["question_attempts"]] == [42, 43, 45]


def test_thinking_only_output_is_rejected_with_bounded_diagnostics(lab):
    s, r, p, jobs = lab
    bank = r.bank("Thinking fixture")
    p.generate = lambda *args, **kwargs: {"text": "", "runtime": {"done_reason": "length"}, "latency_seconds": .01, "diagnostics": {"thinking_characters": 100}}
    jobs.schedule = lambda _: None
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model"})
    jobs._run(job["id"])
    final = s.get("jobs", job["id"])
    assert final["status"] == "failed" and "thinking-only" in final["error"] and "thinking_characters=100" in final["error"]
    assert len(final["question_attempts"]) == 9 and not r.latest("questions", bank_id=bank["id"])
    assert final["failed_count"] == 3 and final["pending_count"] == 2


def test_question_cancellation_preserves_saved_draft_and_resumes_remaining(lab):
    import time
    s, r, p, jobs = lab
    bank = r.bank("Cancellation fixture")
    entered = threading.Event()
    calls = []
    def generate(model, system, prompt, settings, cancel, **kwargs):
        calls.append(prompt)
        if len(calls) > 1:
            entered.set()
            while not cancel():
                time.sleep(.01)
            raise Cancelled('{"text":"partial')
        return result(generated(), system, prompt, kwargs["response_schema"])
    p.generate = generate
    job = jobs.question_task({"bank_id": bank["id"], "model": "test-fixture-model", "count": 2})
    assert entered.wait(3)
    jobs.cancel(job["id"])
    cancelled = wait_job(s, job["id"])
    assert cancelled["status"] == "cancelled" and cancelled["done"] == 1
    assert cancelled["question_attempts"][-1]["partial_text"] == '{"text":"partial'
    # Wait for the existing guarded worker cleanup before explicit resume.
    deadline = time.monotonic() + 2
    while job["id"] in jobs.scheduled and time.monotonic() < deadline:
        time.sleep(.01)
    p.generate = lambda model, system, prompt, settings, cancel, **kw: result(generated("What would you like to reflect on about your approach?"), system, prompt, kw["response_schema"])
    jobs.resume(job["id"])
    assert wait_job(s, job["id"])["done"] == 2
    assert len(r.latest("questions", bank_id=bank["id"])) == 2


def test_rag_marks_experimental_suggestions_and_constrained_ignores_bank(lab):
    s, r, p, jobs = lab
    _, _, _, ds, _ = approved_inputs(r)
    bank = r.bank("Experimental RAG fixture")
    r.question(generated(), bank_id=bank["id"])
    snapshot = r.snapshot("bank", "Experimental", bank_id=bank["id"], review_mode="experimental")
    job = jobs.create_experiment({"name": "RAG fixture", "dataset_snapshot_id": ds["id"], "bank_snapshot_id": snapshot["id"], "methods": ["rag"], "models": ["test-fixture-model"]})
    wait_job(s, job["id"])
    assert "experimental_question_suggestions" in p.calls[0]["prompt"]
    assert "approved_question_suggestions" not in p.calls[0]["prompt"]
    assert "generation" not in p.calls[0]["prompt"]  # No bank-generation provenance is passed as input.
    constrained = jobs.create_experiment({"name": "Constrained fixture", "dataset_snapshot_id": ds["id"], "bank_snapshot_id": snapshot["id"], "methods": ["constrained"], "models": ["test-fixture-model"]})
    wait_job(s, constrained["id"])
    assert constrained["config"]["bank_snapshot_id"] is None and constrained["bank_snapshot"] is None
    assert "question_suggestions" not in p.calls[-1]["prompt"]


def test_bank_generation_endpoint_captures_actual_local_request(tmp_path, monkeypatch):
    import httpx
    requests = []
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:0.6b", "digest": "TEST-DIGEST"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"], "thinking": {"values": [False, True]}})
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={"response": json.dumps(generated()), "done": True, "done_reason": "stop"})
    sync, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kw: sync(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=httpx.MockTransport(handler), **kw))
    store = Store(tmp_path / "local-fixture.db")
    with TestClient(create_app(store, Ollama())) as client:
        bank = client.post("/api/banks", json={"name": "Local request fixture"}).json()
        assert client.get("/api/banks").status_code == 200 and not requests
        assert client.post(f'/api/banks/{bank["id"]}/generate', json={"model": "qwen3:0.6b", "themes": []}).status_code == 422
        job = client.post(f'/api/banks/{bank["id"]}/generate', json={"model": "qwen3:0.6b", "count": 1, "themes": ["next steps"]}).json()
        final = wait_job(store, job["id"])
        question = client.get("/api/questions", params={"bank_id": bank["id"]}).json()[0]
        assert final["status"] == "completed" and question["generation"]["request"]["payload"] == requests[0]
        assert requests[0]["think"] is False and requests[0]["format"]["type"] == "object"
        assert question["generation"]["model_metadata"]["digest"] == "TEST-DIGEST"
