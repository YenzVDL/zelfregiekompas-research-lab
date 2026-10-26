import copy
import json
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend import scenario_generation, methods
from backend.schema import prompt_inputs
from .conftest import wait_job


@pytest.mark.parametrize("goal,activity", [
    ("Learn addition and subtraction", "Use colored blocks"),
    ("ICT addition and subtraction", "ICT counting colored blocks"),
    ("ICT programming: learn addition and subtraction", "Use colored blocks"),
    ("Understand SQL joins", "Learn subtraction using colored blocks"),
    ("Learn spelling", "Complete grammar exercises on a computer"),
    ("Learn general study skills", "Use a computer"),
])
def test_reject_unrelated_subjects_and_generic_ict_labels(goal, activity):
    with pytest.raises(scenario_generation.ScopeViolation):
        scenario_generation.check_scope({"learning_goal": goal, "planned_activity": activity})


@pytest.mark.parametrize("goal,activity", [
    ("Understand Python functions", "Complete five exercises"),
    ("Understand algorithms", "Implement a search algorithm"),
    ("Understand SQL joins", "Query two database tables"),
    ("Build web APIs", "Implement an HTTP endpoint"),
    ("Understand software testing", "Write unit tests"),
    ("Understand version control", "Merge a Git branch"),
    ("Understand networking", "Configure a router"),
    ("Understand software requirements", "Document software requirements"),
    ("Implement arithmetic in Python", "Write Python code to simulate addition and subtraction using colored blocks"),
    (None, "Debug a Python loop"),
])
def test_accept_ict_context_without_restricting_variations(goal, activity):
    inputs = {"learning_goal": goal, "planned_activity": activity, "progress": {"planned_units": 2, "completed_units": 4},
              "student_note": "I did not finish", "planned_time": {"value": 10, "unit": "minutes"}, "reported_time": {"value": 25, "unit": "minutes"}}
    before = copy.deepcopy(inputs)
    assert scenario_generation.check_scope(inputs)["human_review_required"]
    assert inputs == before


def test_missing_goal_and_activity_use_remaining_ict_context():
    assert scenario_generation.check_scope({"student_note": "The SQL query returned duplicate rows"})["evidence_fields"] == ["remaining_context"]
    with pytest.raises(scenario_generation.ScopeViolation):
        scenario_generation.check_scope({"previous_question": "What about Python?"})


def model_response(text, system, prompt, schema):
    payload = {"model": "test-fixture-model", "system": system, "prompt": prompt, "format": copy.deepcopy(schema), "options": {"temperature": 0.2}, "stream": True, "think": False}
    return {"text": json.dumps(text), "runtime": {}, "latency_seconds": 0.01,
            "request": {"address": "http://127.0.0.1:11434/api/generate", "payload": payload, "capture_source": "TEST FIXTURE"},
            "effective_settings": {"options": payload["options"], "think": False, "stream": True}}


def test_scope_retries_keep_actual_attempts_per_version_and_export_without_prompt_leakage(lab):
    s, r, p, jobs = lab
    dataset = r.dataset("ICT fixture")
    old = r.scenario(dataset["id"], {"inputs": {"learning_goal": "Learn arithmetic"}})
    r.review("scenarios", old["id"], "approved", "fixture-reviewer", "Historical fixture")
    frozen = r.snapshot("dataset", "Historical approved snapshot", dataset["id"])
    calls = []
    def generate(model, system, prompt, settings, cancel, **kwargs):
        calls.append(prompt)
        data = {"learning_goal": "ICT addition and subtraction", "planned_activity": "Use colored blocks"} if len(calls) == 1 else {"learning_goal": "Understand SQL joins", "planned_activity": "Query two tables", "previous_question": "What would you like to try next?"}
        return model_response(data, system, prompt, kwargs["response_schema"])
    p.generate = generate
    job = jobs.scenario_task("generation", {"dataset_id": dataset["id"], "model": "test-fixture-model", "category": "contradictory"})
    final = wait_job(s, job["id"])
    assert final["status"] == "completed" and len(calls) == 2
    scenario = s.get("scenarios", final["results"][0])
    generation = scenario["generation"]
    assert generation["attempts"][0]["errors"][0]["type"] == "ict_scope_violation"
    assert "ict_scope_violation" in calls[1] and "contradictory" in calls[1]
    assert generation["saved_attempt_id"] == generation["attempts"][1]["id"]
    assert generation["request"] == generation["attempts"][1]["request"]
    saved_prompt = generation["request"]["payload"]["prompt"]
    from backend import scenario_generation as generator
    original_system = generator.SYSTEM
    try:
        generator.SYSTEM = "TEMPLATE CHANGED AFTER GENERATION"
        assert s.get("scenarios", scenario["id"])["generation"]["request"]["payload"]["prompt"] == saved_prompt
    finally:
        generator.SYSTEM = original_system
    with TestClient(create_app(s, p)) as client:
        exported = client.get(f'/api/scenarios/{scenario["id"]}/export').json()["scenario"]
        assert exported["generation"] == generation
        archive = client.get(f'/api/datasets/{dataset["id"]}/export?include_provenance=true').json()
        assert any(v["generation"] == generation for v in archive["generation_provenance"])
        assert all("generation" not in row["inputs"] for row in archive["scenarios"])
    context = json.dumps(prompt_inputs(scenario["inputs"]))
    assert "Request context" not in context and "response_schema" not in context
    trace = methods.prepare("constrained", scenario["inputs"], [])
    assert "Request context" not in trace["prompt"] and "TEMPLATE CHANGED" not in trace["prompt"]
    assert s.get("snapshots", frozen["id"]) == frozen
    assert s.get("scenarios", old["id"])["review_status"] == "approved"
    assert scenario["review_status"] == "awaiting_review"


def test_repeated_scope_violation_stops_with_useful_message(lab):
    s, r, p, jobs = lab
    dataset = r.dataset("Rejected ICT fixture")
    p.generate = lambda model, system, prompt, settings, cancel, **kw: model_response({"learning_goal": "ICT arithmetic", "planned_activity": "Use colored blocks"}, system, prompt, kw["response_schema"])
    job = jobs.scenario_task("generation", {"dataset_id": dataset["id"], "model": "test-fixture-model"})
    final = wait_job(s, job["id"])
    assert final["status"] == "failed" and final["done"] == 0
    assert "after 3 attempts" in final["error"] and "ICT" in final["error"]
    assert len(final["scenario_attempts"]) == 3 and not s.all("scenarios")


def test_revision_uses_ict_scope_and_its_own_recorded_request(lab):
    s, r, p, jobs = lab
    dataset = r.dataset("Revision ICT fixture")
    parent = r.scenario(dataset["id"], {"inputs": {"learning_goal": "Learn addition with colored blocks"}})
    def generate(model, system, prompt, settings, cancel, **kw):
        assert "ICT education" in system and "ICT education" in prompt
        assert "replacing unrelated" in system.lower() and "reviewer_feedback" in prompt
        return model_response({"learning_goal": "Understand Git branches", "planned_activity": "Merge a Git branch"}, system, prompt, kw["response_schema"])
    p.generate = generate
    job = jobs.scenario_task("revision", {"version_id": parent["id"], "model": "test-fixture-model", "feedback": "Make the activity more specific"})
    final = wait_job(s, job["id"])
    assert final["status"] == "completed"
    revised = s.get("scenarios", final["results"][0])
    assert revised["parent_id"] == parent["id"]
    assert "Make the activity more specific" in revised["generation"]["request"]["payload"]["prompt"]
    assert s.get("scenarios", parent["id"])["generation"] is None
