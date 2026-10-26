"""Focused response-contract regressions. All model text here is test-fixture data."""
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.provider import Ollama, ProviderError
from backend.jobs import Settings, ScenarioSettings
from backend.scenario_generation import parse_response
from backend.schema import Inputs
from .conftest import wait_job


def response(text):
    return {"text": text, "runtime": {"done_reason": "stop"}, "latency_seconds": 0.01}


def test_demonstrated_schema_echo_is_rejected_then_corrected_by_schema_request(lab):
    s, r, p, jobs = lab
    dataset = r.dataset("Regression fixture")
    calls = []
    echo = {"category": "normal", "missing_fields": [], "schema": Inputs.model_json_schema()}
    def generate(model, system, prompt, settings, cancel, **kw):
        calls.append((prompt, settings, kw))
        return response(json.dumps(echo if len(calls) == 1 else {"learning_goal": "Understand SQL joins", "progress": {"planned_units": 4, "completed_units": 2}}))
    p.generate = generate
    job = jobs.scenario_task("generation", {"dataset_id": dataset["id"], "model": "test-fixture-model", "category": "normal"})
    final = wait_job(s, job["id"])
    assert final["status"] == "completed" and final["done"] == 1
    attempts = final["scenario_attempts"]
    assert [a["status"] for a in attempts] == ["invalid", "valid"]
    assert json.loads(attempts[0]["raw_response"]) == echo
    assert {e["path"] for e in attempts[0]["errors"]} == {"category", "missing_fields", "schema"}
    assert "validation_problems" in calls[1][0]
    assert all(call[2]["response_schema"]["additionalProperties"] is False for call in calls)
    assert "schema" not in json.loads(calls[0][0].split("Request context (not output fields):\n")[1])
    candidate = s.get("scenarios", final["results"][0])
    assert set(candidate["inputs"]) == set(Inputs.model_fields)
    assert candidate["tags"] == ["normal"] and candidate["review_status"] == "awaiting_review"
    with pytest.raises(ValueError, match="Human approval"):
        r.snapshot("dataset", "Must not approve implicitly", dataset["id"])


@pytest.mark.parametrize("bad,path", [
    ({"progress": {"planned_units": "4"}}, "progress.planned_units"),
    ({"planned_time": {"value": True, "unit": "minutes"}}, "planned_time.value"),
    ({"reported_time": {"value": 2, "unit": "days"}}, "reported_time.unit"),
    ({"control_results": [{"question": "Q", "schema": {}}]}, "control_results.0.schema"),
    ({"progress": {"category": "normal"}}, "progress.category"),
    ({"Inputs": {"learning_goal": "Wrapped"}}, "Inputs"),
])
def test_malformed_nested_or_wrapped_outputs_fail_after_bounded_attempts(lab, bad, path):
    s, r, p, jobs = lab
    dataset = r.dataset("Invalid nested fixture")
    calls = []
    def generate(*args, **kwargs):
        calls.append(kwargs)
        return response(json.dumps(bad))
    p.generate = generate
    job = jobs.scenario_task("generation", {"dataset_id": dataset["id"], "model": "test-fixture-model"})
    final = wait_job(s, job["id"])
    assert final["status"] == "failed" and final["done"] == 0 and len(calls) == 3
    assert path in final["error"] and "after 3 attempts" in final["error"]
    assert final["diagnostics"]["validation_errors"][0]["path"] == path
    assert all(a["raw_response"] for a in final["scenario_attempts"])
    assert s.all("scenarios") == []


def test_later_failure_retains_draft_and_resume_does_not_duplicate_it(lab):
    s, r, p, jobs = lab
    jobs.schedule = lambda _: None
    dataset = r.dataset("Partial fixture")
    values = iter([json.dumps({"learning_goal": "Retained Python draft"}), "invalid JSON", "[]", '{"schema":{}}'])
    p.generate = lambda *a, **kw: response(next(values))
    job = jobs.scenario_task("generation", {"dataset_id": dataset["id"], "model": "test-fixture-model", "count": 2})
    jobs._run(job["id"])
    final = wait_job(s, job["id"])
    assert final["status"] == "failed" and final["done"] == 1 and len(final["results"]) == 1
    assert "1 draft(s) retained" in final["error"]
    original = final["results"][0]
    p.generate = lambda *a, **kw: response('{"learning_goal":"Second SQL draft"}')
    jobs.schedule = jobs._run
    jobs.resume(job["id"])
    resumed = wait_job(s, job["id"])
    assert resumed["status"] == "completed" and resumed["done"] == 2
    assert resumed["results"][0] == original and len(s.all("scenarios")) == 2
    assert len(resumed["scenario_attempts"]) == 5
    assert all(v["review_status"] == "awaiting_review" for v in s.all("scenarios"))


@pytest.mark.parametrize("text", ['{"student_note":"a","student_note":"b"}', '{"progress":{"planned_units":NaN}}', '```json\n{}\n```'])
def test_no_silent_json_repair_or_duplicate_key_discard(text):
    with pytest.raises(ValueError):
        parse_response(text)


def test_requested_missing_values_are_constrained_not_overwritten(lab):
    s, r, p, jobs = lab
    dataset = r.dataset("Missing fixture")
    calls = []
    def generate(*a, **kw):
        calls.append(kw)
        return response('{"progress":{"planned_units":4}}')
    p.generate = generate
    job = jobs.scenario_task("generation", {"dataset_id": dataset["id"], "model": "test-fixture-model", "missing_fields": ["progress"]})
    final = wait_job(s, job["id"])
    assert final["status"] == "failed" and "must be null" in final["error"]
    assert calls[0]["response_schema"]["properties"]["progress"] == {"type": "null"}
    assert not s.all("scenarios")


def test_unavailable_provider_is_not_retried_and_settings_are_separate(lab):
    s, r, p, jobs = lab
    dataset = r.dataset("Provider failure fixture")
    count = []
    def fail(*a, **kw):
        count.append(1)
        raise ProviderError("Connection refused", "connection_failure")
    p.generate = fail
    job = jobs.scenario_task("generation", {"dataset_id": dataset["id"], "model": "test-fixture-model"})
    final = wait_job(s, job["id"])
    assert final["status"] == "failed" and len(count) == 1
    assert job["config"]["settings"]["num_predict"] == 2048
    assert job["config"]["settings"]["think"] is False
    assert Settings().think is None and Settings().num_predict == 128
    assert ScenarioSettings().timeout_seconds == 120


def test_actual_provider_serializes_nested_schema_into_ollama_format(monkeypatch, tmp_path):
    from backend.store import Store
    observed = []
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:0.6b", "digest": "test-only"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        payload = json.loads(request.content)
        observed.append(payload)
        return httpx.Response(200, json={"response": '{"learning_goal":"Understand Python functions"}', "done": True})
    sync, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kw: sync(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=httpx.MockTransport(handler), **kw))
    with TestClient(create_app(Store(tmp_path / "fixture.db"), Ollama())) as client:
        protocol = client.get("/api/protocol").json()
        assert protocol["scenario_prompt_version"] == "synthetic-inputs-v5-planned" and protocol["scenario_attempt_limit"] == 3
        assert protocol["scenario_generation_defaults"]["think"] is False
        dataset = client.post("/api/datasets", json={"name": "Endpoint fixture"}).json()
        job = client.post("/api/scenario-tasks/generation", json={"dataset_id": dataset["id"], "model": "qwen3:0.6b"}).json()
        final = wait_job(client.app.state.store, job["id"])
        assert final["status"] == "completed"
        scenario = client.app.state.store.get("scenarios", final["results"][0])
        assert scenario["generation"]["request"]["payload"] == observed[0]
        assert "ICT education" in observed[0]["system"] and "ICT learning" in observed[0]["prompt"]
        assert scenario["generation"]["attempts"][0]["request"]["payload"] == observed[0]
    payload = observed[0]
    assert payload["format"]["additionalProperties"] is False
    assert set(payload["format"]["properties"]) == set(Inputs.model_fields)
    assert all(d["additionalProperties"] is False for d in payload["format"]["$defs"].values())
    assert payload["think"] is False and payload["options"]["num_predict"] == 2048
    assert '"schema"' not in payload["prompt"]
    assert final["scenario_attempts"][0]["effective_settings"]["format"] == payload["format"]
