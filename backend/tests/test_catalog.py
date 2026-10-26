import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.catalog import Catalog, parse_variant
from backend.main import create_app
from backend.provider import Ollama
from backend.store import Store


def page(name="qwen3:0.6b", parameter="752M", quantization="Q4_K_M"):
    return f'<code>ollama run {name}</code><h2>Details</h2><p>7df6b6e09427 · 523MB</p><span>parameters</span><span>{parameter}</span><span>quantization</span><span>{quantization}</span><h2>Readme</h2>'


def test_catalog_filters_existing_complete_entries_and_unknowns_without_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Browsing must not fetch public metadata or call local Ollama")
    monkeypatch.setattr(httpx, "Client", forbidden)
    catalog = Catalog(Store(tmp_path / "catalog.db"))
    rows = catalog.browse()
    assert rows["total"] == 13 and all(e["retrieved_at"] for e in rows["entries"])
    selected = catalog.browse(search="qwen", family="Qwen", generation="3", parameter_size="752M", quantization="Q8_0")["entries"]
    assert [e["name"] for e in selected] == ["qwen3:0.6b-q8_0"]
    assert catalog.browse(family="Llama", generation="2.5")["entries"] == []
    assert catalog.browse(search="qwen3 0.6b fp16")["entries"][0]["quantization"] == "F16"
    catalog.manifest["entries"].append({"name": "new:tag", "family": "New", "generation": "1"})
    assert catalog.browse(parameter_size="Unknown")["entries"][0]["name"] == "new:tag"


def test_parser_uses_authoritative_details_not_parameter_name_and_rejects_layout_changes():
    entry = {"name": "qwen3:0.6b", "family": "Qwen", "generation": "3"}
    parsed = parse_variant(page(), entry)
    assert parsed["parameter_size"] == "752M"  # Not 0.6B from the tag.
    assert parsed["quantization"] == "Q4_K_M" and parsed["size_bytes"] == 523000000
    with pytest.raises(ValueError, match="Exact tag"):
        parse_variant(page(name="qwen3:other"), entry)
    with pytest.raises(ValueError):
        parse_variant("<code>ollama run qwen3:0.6b</code><h2>Details</h2><h2>Readme</h2>", entry)


def test_refresh_failure_preserves_cached_entries_and_freshness(tmp_path, monkeypatch):
    store = Store(tmp_path / "catalog.db")
    catalog = Catalog(store)
    catalog.manifest["entries"] = catalog.manifest["entries"][:1]
    initial = catalog.browse()["entries"][0]
    real = httpx.Client
    calls = []
    def success(request):
        calls.append(request)
        assert request.method == "GET" and not request.content
        assert str(request.url) == "https://ollama.com/library/qwen3:0.6b"
        return httpx.Response(200, text=page(parameter="753M"))
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(success), **kw))
    refreshed = catalog.refresh()
    assert refreshed["refresh_errors"] == 0 and len(calls) == 1
    assert refreshed["entries"][0]["parameter_size"] == "753M"
    stamp = refreshed["entries"][0]["retrieved_at"]
    def offline(request):
        raise httpx.ConnectError("Public metadata unavailable", request=request)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(offline), **kw))
    failed = catalog.refresh()
    assert failed["refresh_errors"] == 1
    assert failed["entries"][0]["parameter_size"] == "753M" and failed["entries"][0]["retrieved_at"] == stamp
    assert "unavailable" in failed["entries"][0]["last_error"]
    # A fresh process/store instance can browse the persisted cache without network.
    again = Catalog(Store(store.path)).browse(search="qwen3:0.6b")
    assert again["entries"][0]["parameter_size"] == "753M"
    assert initial["parameter_size"] == "752M"


def test_cloud_entries_excluded_and_catalog_endpoint_does_not_contact_ollama(tmp_path):
    store = Store(tmp_path / "lab.db")
    catalog = Catalog(store)
    catalog.manifest["entries"] += [{"name": "hidden:cloud", "family": "Qwen"}, {"name": "hidden:local", "cloud_only": True}]
    assert not any(e["name"].startswith("hidden") for e in catalog.browse()["entries"])
    with TestClient(create_app(store, Ollama("http://127.0.0.1:1"))) as client:
        data = client.get("/api/model-catalog", params={"search": "qwen", "generation": "3", "quantization": "Q8_0"}).json()
        assert [e["name"] for e in data["entries"]] == ["qwen3:0.6b-q8_0"]
