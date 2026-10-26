"""Ollama protocol doubles are restricted to tests; no installed weights are implied."""
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.provider import Ollama, ProviderError
from backend.store import Store
from .conftest import approved_inputs, wait_job


@pytest.fixture
def ollama_protocol(monkeypatch):
    state = {"installed": False, "digest": "sha256:original", "calls": [], "pull_error": None, "generation_error": None, "remote": False}
    def handler(request):
        state["calls"].append((request.url.path, json.loads(request.content) if request.content else None))
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "test-protocol"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": ([{"name": "fixture:tiny", "digest": state["digest"], "size": 123456,
                "details": {"parameter_size": "1B", "quantization_level": "Q4_K_M"}}] if state["installed"] else [])})
        if path == "/api/show":
            return httpx.Response(200, json={"remote_model": "fixture:cloud"} if state["remote"] else {"capabilities": ["completion"], "model_info": {"fixture.parameter_count": 1000000000}})
        if path == "/api/pull":
            if state.get("pull_text") is not None:
                return httpx.Response(200, text=state["pull_text"])
            if state["pull_error"]:
                return httpx.Response(200, text=json.dumps({"error": state["pull_error"]}) + "\n")
            state["installed"] = True
            return httpx.Response(200, text='{"status":"pulling manifest"}\n{"status":"pulling layer","digest":"layer1","completed":50,"total":100}\n{"status":"success"}\n')
        if path == "/api/generate":
            if state.get("change_during_inference"):
                state["digest"] = "sha256:changed"
            if state["generation_error"]:
                return httpx.Response(500, json={"error": state["generation_error"]})
            if json.loads(request.content).get("stream") is False:
                return httpx.Response(200, json={"response": "ready", "done": True, "done_reason": "stop", "total_duration": 1000})
            return httpx.Response(200, text='{"response":"ready","done":false}\n{"done":true,"total_duration":1000}\n')
        raise AssertionError(path)
    sync, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kw: sync(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=httpx.MockTransport(handler), **kw))
    return state


def test_explicit_install_stream_discovery_metadata_and_inference(tmp_path, ollama_protocol):
    state = ollama_protocol
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        assert c.post("/api/models/connection/test").json()["available"]
        assert c.get("/api/models").json()["models"] == []
        failed = c.post("/api/models/test", json={"model": "fixture:tiny"})
        assert failed.status_code == 503 and failed.json()["elapsed_seconds"] >= 0
        assert not any(path == "/api/pull" for path, _ in state["calls"])
        response = c.post("/api/models/install", json={"model": "fixture:tiny"})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert events[1]["completed"] == 50 and events[-1]["done"] is True
        installed = c.get("/api/models").json()["models"][0]
        assert installed["name"] == "fixture:tiny" and installed["digest"] == "sha256:original"
        assert installed["size"] == 123456 and installed["details"]["quantization_level"] == "Q4_K_M"
        assert installed["details"]["parameter_size"] == "1B"
        result = c.post("/api/models/test", json={"model": "fixture:tiny"}).json()
        assert result["success"] and result["response"] == "ready" and result["elapsed_seconds"] >= 0
        assert result["model_metadata"]["digest"] == "sha256:original"
        assert len([path for path, _ in state["calls"] if path == "/api/pull"]) == 1


@pytest.mark.parametrize("name", ["fixture", "fixture:cloud", "https://example.com/a:tag", "bad:tag extra", "../bad:tag", ""])
def test_install_requires_exact_safe_local_tag(tmp_path, name, ollama_protocol):
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        assert c.post("/api/models/install", json={"model": name}).status_code == 422
        assert not any(path == "/api/pull" for path, _ in ollama_protocol["calls"])


def test_stream_errors_cloud_models_and_http_inference_details(tmp_path, ollama_protocol):
    state = ollama_protocol
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        state["pull_error"] = "model manifest not found"
        events = c.post("/api/models/install", json={"model": "fixture:tiny"}).text
        assert "manifest not found" in events and '"done": false' in events
        state.update(pull_error=None, installed=True, remote=True)
        assert c.get("/api/models").json()["models"] == []
        assert c.post("/api/models/test", json={"model": "fixture:tiny"}).status_code == 503
        assert not any(path == "/api/generate" for path, _ in state["calls"])
        state.update(remote=False, generation_error="insufficient memory to load model")
        result = c.post("/api/models/test", json={"model": "fixture:tiny"})
        assert result.status_code == 503
        assert "HTTP 500" in result.json()["detail"] and "insufficient memory" in result.json()["detail"]


def test_connection_persistence_allowed_docker_address_and_active_job_guard(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    store = Store(tmp_path / "lab.db")
    provider = Ollama()
    with TestClient(create_app(store, provider)) as c:
        assert c.put("/api/models/connection", json={"address": "http://public.example:11434"}).status_code == 422
        assert c.put("/api/models/connection", json={"address": "http://host.docker.internal:11434"}).status_code == 200
        store.put("jobs", {"id": "active", "status": "queued"})
        assert c.put("/api/models/connection", json={"address": "http://localhost:11434"}).status_code == 409
        assert c.post("/api/models/install", json={"model": "fixture:tiny"}).status_code == 409
        store.patch("jobs", "active", status="completed")
        provider.operation_lock.acquire()
        try:
            assert c.put("/api/models/connection", json={"address": "http://localhost:11434"}).status_code == 409
            inference = c.post("/api/models/test", json={"model": "fixture:tiny"})
            assert inference.status_code == 503 and inference.json()["kind"] == "busy"
        finally:
            provider.operation_lock.release()
    with TestClient(create_app(store)) as c:
        assert c.get("/api/models/connection").json()["address"] == "http://host.docker.internal:11434"
    monkeypatch.setenv("OLLAMA_ALLOWED_HOSTS", "ollama")
    assert Ollama("http://ollama:11434").url == "http://ollama:11434"


def test_unavailable_ollama_error_handling(tmp_path, monkeypatch):
    real_client = httpx.Client
    def unavailable(request):
        raise httpx.ConnectError("test-only connection refused", request=request)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(unavailable), **kw))
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        assert c.get("/api/models").json()["available"] is False
        assert c.post("/api/models/connection/test").status_code == 503
        assert c.post("/api/models/test", json={"model": "fixture:tiny"}).json()["success"] is False
        assert "connection refused" in c.post("/api/models/install", json={"model": "fixture:tiny"}).text
        dataset, _, _, snapshot, _ = approved_inputs(c.app.state.research)
        body = {"name": "Unavailable local generation", "dataset_snapshot_id": snapshot["id"], "methods": ["constrained"], "models": ["fixture:tiny"]}
        queued = c.post("/api/experiments", json=body)
        assert queued.status_code == 503 and "connection refused" in queued.json()["detail"]
        # Failed streaming operations release management lock.
        assert c.put("/api/models/connection", json={"address": "http://localhost:11434"}).status_code == 200


def test_queued_and_resumed_jobs_reject_changed_generator_and_evaluator_digests(lab):
    s, r, p, jobs = lab
    _, _, _, ds, bank = approved_inputs(r)
    jobs.schedule = lambda _: None
    raw = {"name": "Pinned fixture", "dataset_snapshot_id": ds["id"], "bank_snapshot_id": bank["id"],
           "methods": ["fixed", "rag", "constrained"], "models": ["test-fixture-model"], "ai_control": True, "evaluator_model": "test-fixture-model"}
    job = jobs.create_experiment(raw)
    assert job["model_metadata"]["test-fixture-model"]["digest"] == "fixture"
    p.models = lambda: [{"name": "test-fixture-model", "digest": "changed", "details": {}}]
    jobs._run(job["id"])
    outputs = s.all("outputs")
    assert all(o["status"] == "failed" and "digest changed" in o["error"] for o in outputs if o["method"] != "fixed")
    fixed = next(o for o in outputs if o["method"] == "fixed")
    assert fixed["question"] and "digest changed" in fixed["evaluation_error"]
    assert not p.calls
    assert s.get("jobs", job["id"])["model_metadata"]["test-fixture-model"]["digest"] == "fixture"
    p.models = lambda: [{"name": "test-fixture-model", "digest": "fixture", "details": {}}]
    job2 = jobs.create_experiment({**raw, "methods": ["constrained"], "ai_control": False})
    s.patch("jobs", job2["id"], status="interrupted")
    p.models = lambda: [{"name": "test-fixture-model", "digest": "changed", "details": {}}]
    jobs.schedule = lambda ident: jobs._run(ident)
    jobs.resume(job2["id"])
    assert "digest changed" in next(o for o in s.all("outputs") if o["job_id"] == job2["id"])["error"]


def test_scenario_generation_revision_are_pinned_and_fixed_needs_no_provider(lab):
    s, r, p, jobs = lab
    d, row, _, ds, bank = approved_inputs(r)
    jobs.schedule = lambda _: None
    generation = jobs.scenario_task("generation", {"dataset_id": d["id"], "model": "test-fixture-model"})
    revision = jobs.scenario_task("revision", {"version_id": row["id"], "feedback": "Clarify", "model": "test-fixture-model"})
    p.models = lambda: [{"name": "test-fixture-model", "digest": "changed"}]
    for job in [generation, revision]:
        jobs._run(job["id"])
        assert "digest changed" in s.get("jobs", job["id"])["error"]
    def offline():
        raise AssertionError("Fixed bank must not discover or use a model")
    p.models = offline
    fixed = jobs.create_experiment({"name": "Fixed offline", "dataset_snapshot_id": ds["id"], "bank_snapshot_id": bank["id"], "methods": ["fixed"]})
    jobs._run(fixed["id"])
    assert s.get("jobs", fixed["id"])["status"] == "completed" and not p.calls


def test_provider_refuses_digest_change_before_generation(ollama_protocol):
    ollama_protocol["installed"] = True
    with pytest.raises(ProviderError, match="digest changed"):
        Ollama().generate("fixture:tiny", "", "", {"_model_digest": "old"}, lambda: False)
    assert not any(path == "/api/generate" for path, _ in ollama_protocol["calls"])


@pytest.mark.parametrize("content", ['{"status":"pulling manifest"}\n', 'not json\n', '[]\n'])
def test_install_rejects_incomplete_or_malformed_streams(tmp_path, ollama_protocol, content):
    ollama_protocol["pull_text"] = content
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        events = [json.loads(line) for line in c.post("/api/models/install", json={"model": "fixture:tiny"}).text.splitlines()]
        assert events[-1]["error"] and events[-1]["done"] is False
        assert not any(e.get("done") for e in events)


def test_provider_discards_result_if_digest_changes_during_inference(ollama_protocol):
    ollama_protocol.update(installed=True, change_during_inference=True)
    with pytest.raises(ProviderError, match="during inference; result discarded"):
        Ollama().generate("fixture:tiny", "", "", {"_model_digest": "sha256:original"}, lambda: False)
