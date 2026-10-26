"""Regression fixtures model the observed thinking-only completion; no weights used."""
import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.provider import Ollama, ProviderError, Cancelled
from backend.store import Store


@pytest.fixture
def protocol(monkeypatch):
    state = {"requests": [], "response": None, "error": None, "thinking": {"values": [False, True], "default": True}, "delay": 0}
    def handle(request):
        payload = json.loads(request.content) if request.content else None
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:0.6b", "digest": "fixture-only"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"], "thinking": state["thinking"]})
        assert request.url.path == "/api/generate"
        state["requests"].append(payload)
        if state["error"]:
            return httpx.Response(500, json={"error": state["error"]})
        if state["response"] is not None:
            return httpx.Response(200, text=state["response"])
        if payload.get("think") is False:
            result = {"response": "OK", "done": True, "done_reason": "stop", "load_duration": 10000, "eval_duration": 20000}
        else:
            result = {"response": "", "thinking": "Fixture thinking tokens consume all output budget", "done": True, "done_reason": "length", "eval_count": 32}
        return httpx.Response(200, json=result)
    async def async_handle(request):
        if request.url.path == "/api/generate" and state["delay"]:
            try:
                await asyncio.sleep(state["delay"])
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
        return handle(request)
    sync, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kw: sync(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=httpx.MockTransport(async_handle), **kw))
    return state


def test_demonstrated_thinking_only_failure_and_short_test_fix(tmp_path, protocol):
    provider = Ollama()
    previous = provider.generate("qwen3:0.6b", "Respond briefly.", "Reply with the word ready.", {"num_predict": 32}, lambda: False)
    assert previous["text"] == "" and previous["runtime"]["done_reason"] == "length"
    assert previous["diagnostics"]["thinking_characters"] > 0
    with TestClient(create_app(Store(tmp_path / "lab.db"), provider)) as c:
        response = c.post("/api/models/test", json={"model": "qwen3:0.6b"})
        result = response.json()
        assert response.status_code == 200 and result["success"] and result["response"] == "OK"
        assert result["effective_settings"]["think"] is False and result["effective_settings"]["stream"] is False
        assert result["runtime"]["load_duration"] == 10000 and result["runtime"]["eval_duration"] == 20000
    assert protocol["requests"][-1]["options"]["num_predict"] == 16
    assert "think" not in protocol["requests"][0]  # Research defaults preserved.


@pytest.mark.parametrize("content,kind,explanation", [
    ('{"response":"","done":true}', "empty_output", "no answer text"),
    ('[]', "malformed_response", "JSON object"),
    ('{"response":"OK","done":false}', "malformed_response", "without done"),
    ('not-json', "malformed_response", "Expecting value"),
])
def test_useful_response_categories_and_http_status(tmp_path, protocol, content, kind, explanation):
    protocol["response"] = content
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        result = c.post("/api/models/test", json={"model": "qwen3:0.6b"}).json()
        assert result["success"] is False and result["kind"] == kind and explanation in result["detail"]
        assert result["http_status"] == 200 and result["elapsed_seconds"] >= 0
        assert result["diagnostics"]["address"] == "http://127.0.0.1:11434"


def test_model_load_error_preserves_status_and_backend_traceback(tmp_path, protocol, caplog):
    protocol["error"] = "model requires more system memory than available"
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        result = c.post("/api/models/test", json={"model": "qwen3:0.6b"}).json()
    assert result["kind"] == "model_load_failure" and result["http_status"] == 500
    assert "more system memory" in result["detail"] and "Traceback" in caplog.text


def test_thinking_not_forcibly_disabled_when_model_does_not_support_false(tmp_path, protocol):
    protocol["thinking"] = {"values": ["low", "medium", "high"], "default": "medium"}
    with TestClient(create_app(Store(tmp_path / "lab.db"), Ollama())) as c:
        result = c.post("/api/models/test", json={"model": "qwen3:0.6b"}).json()
    assert "think" not in protocol["requests"][-1]
    assert result["kind"] == "empty_output" and "thinking" in result["detail"]
    assert result["diagnostics"]["done_reason"] == "length"


def test_timeout_and_cancellation_close_inflight_request(protocol):
    protocol["delay"] = 10
    provider = Ollama()
    with pytest.raises(ProviderError) as timeout:
        provider.generate("qwen3:0.6b", "", "", {"timeout_seconds": 0.02}, lambda: False)
    assert timeout.value.kind == "timeout" and protocol["cancelled"]
    protocol["cancelled"] = False
    with pytest.raises(Cancelled):
        provider.generate("qwen3:0.6b", "", "", {}, lambda: True)
    # The cancelled task may be stopped before issuing any HTTP request.
    assert not provider.operation_lock.locked()
