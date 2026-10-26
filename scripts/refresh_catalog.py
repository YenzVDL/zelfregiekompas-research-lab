"""Refresh the distributable offline snapshot from reviewed exact-tag sources.

Run from the repository root: .venv/Scripts/python.exe -m scripts.refresh_catalog
Does not contact Ollama, install weights or read research records.
"""
import json
import tempfile
from pathlib import Path
from backend.catalog import Catalog, ROOT
from backend.store import Store


def main():
    with tempfile.TemporaryDirectory() as directory:
        result = Catalog(Store(Path(directory) / "catalog.db")).refresh()
    if result["refresh_errors"]:
        errors = {e["name"]: e["last_error"] for e in result["entries"] if e.get("last_error")}
        raise SystemExit("Snapshot not replaced because some sources failed: " + json.dumps(errors))
    (ROOT / "catalog_snapshot.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Verified and cached {len(result['entries'])} exact tags. No model weights downloaded.")


if __name__ == "__main__":
    main()
