"""Curated exact-tag catalog. Only explicit refresh fetches public Ollama HTML.

There is no dependency on an undocumented search/catalog API. The manifest defines
scope, the bundled snapshot supports first-launch offline browsing, and SQLite
retains refreshed metadata. A markup change fails closed and preserves the cache.
"""
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
import httpx
from .provider import Ollama
from .store import now

ROOT = Path(__file__).parent


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())


def parse_variant(html, entry):
    parser = PageText()
    parser.feed(html)
    parts = parser.parts
    if f"ollama run {entry['name']}" not in parts or "Details" not in parts or "Readme" not in parts:
        raise ValueError("Exact tag or model Details section missing; public page markup may have changed")
    details = parts[parts.index("Details") + 1:parts.index("Readme")]

    def following(label):
        if label not in details:
            return None
        index = details.index(label) + 1
        return details[index] if index < len(details) else None

    parameter = following("parameters")
    quantization = following("quantization")
    if parameter is not None and not re.fullmatch(r"[\d.]+[KMBT]", parameter):
        raise ValueError("Unrecognized reported parameter metadata; preserving cached entry")
    artifact = next((re.search(r"\b([a-f0-9]{12,64})\b.*?([\d.]+)([KMGT]?B)\b", p) for p in details if re.search(r"\b[a-f0-9]{12,64}\b", p)), None)
    if not artifact:
        raise ValueError("No reported artifact size/digest in model Details; preserving cached entry")
    size = int(float(artifact[2]) * 1000 ** ["B", "KB", "MB", "GB", "TB"].index(artifact[3]))
    return {**entry, "parameter_size": parameter, "quantization": quantization,
            "size_bytes": size, "reported_size": artifact[2] + artifact[3],
            "source_digest_prefix": artifact[1], "retrieved_at": now(),
            "metadata_source": "Ollama public exact-tag page / Details (reported rounded values)", "last_error": None}


class Catalog:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.manifest = json.loads((ROOT / "catalog_manifest.json").read_text(encoding="utf-8"))

    def cached(self):
        bundled = json.loads((ROOT / "catalog_snapshot.json").read_text(encoding="utf-8"))
        cached = next((r for r in self.store.all("settings") if r["id"] == "model_catalog"), bundled)
        # Only reviewed manifest entries remain in scope, even after manifest changes.
        known = {e["name"]: e for e in bundled.get("entries", [])}
        known.update({e['name']:e for e in cached.get('entries', [])})
        entries = []
        for entry in self.manifest["entries"]:
            if entry.get("cloud_only") or "cloud" in entry["name"].lower():
                continue
            Ollama.model_name(entry["name"])
            entries.append({**known.get(entry["name"], {}), **entry})
        return {**cached, "entries": entries, "scope": self.manifest["scope"], "source": "https://ollama.com/library", "offline_browsing": True}

    def browse(self, search="", family="", generation="", parameter_size="", quantization=""):
        catalog = self.cached()
        rows = catalog["entries"]
        fields = {"family": family, "generation": generation, "parameter_size": parameter_size, "quantization": quantization}
        facets = {key: sorted({str(e.get(key) or "Unknown") for e in rows}) for key in fields}
        terms = search.casefold().split()
        selected = [e for e in rows if all(term in " ".join(str(e.get(k) or "Unknown") for k in ["name", *fields]).casefold() for term in terms)
                    and all(not value or str(e.get(key) or "Unknown").casefold() == value.casefold() for key, value in fields.items())]
        return {**catalog, "entries": selected, "total": len(rows), "facets": facets}

    def refresh(self):
        if not self.lock.acquire(blocking=False):
            raise ValueError("Catalog refresh is already running")
        try:
            cached = self.cached()

            def fetch(entry):
                # Fixed origin and manifest-controlled tags; no caller-supplied URL or research data.
                source = "https://ollama.com/library/" + entry["name"]
                try:
                    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                        response = client.get(source, headers={"User-Agent": "Zelfregiekompas-catalog/1.0"})
                        response.raise_for_status()
                        if len(response.content) > 2_000_000:
                            raise ValueError("Catalog source page exceeds size limit")
                        return parse_variant(response.text, {**entry, "source_url": source})
                except (httpx.HTTPError, ValueError) as e:
                    return {**entry, "last_error": str(e) or type(e).__name__, "last_attempt_at": now()}

            with ThreadPoolExecutor(max_workers=4) as pool:
                entries = list(pool.map(fetch, cached["entries"]))
            result = {"id": "model_catalog", "entries": entries, "last_refresh_attempt": now(),
                      "refresh_errors": sum(bool(e.get("last_error")) for e in entries)}
            self.store.put("settings", result)
            return self.browse()
        finally:
            self.lock.release()
