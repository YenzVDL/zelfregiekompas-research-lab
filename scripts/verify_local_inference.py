"""Live HTTP acceptance check; never mocks inference or downloads implicitly.

Run: .venv/Scripts/python.exe -m scripts.verify_local_inference --model qwen3:0.6b
Optional --install explicitly pulls that exact tag first (may update its digest).
Uses an isolated temporary database and its own backend process on port 8001.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = {"evidence": "Real local Ollama and actual HTTP application endpoints; no fixtures", "model": args.model}
    log_path = root / "docs" / "local-inference-server.log"
    with tempfile.TemporaryDirectory() as directory, log_path.open("w", encoding="utf-8") as logfile:
        env = {**os.environ, "RESEARCH_DB": str(Path(directory) / "live-check.db")}
        # Explicit INFO logging records the fixed test payload without research prompts.
        code = "import logging; logging.basicConfig(level=logging.INFO); import uvicorn; uvicorn.run('backend.main:app', host='127.0.0.1', port=8001)"
        process = subprocess.Popen([sys.executable, "-c", code], cwd=root, env=env, stdout=logfile, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url="http://127.0.0.1:8001", trust_env=False, timeout=75) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Backend failed to start; inspect " + str(log_path))
                    try:
                        client.get("/api/health").raise_for_status()
                        break
                    except httpx.ConnectError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Backend readiness timed out")
                evidence["connection"] = client.post("/api/models/connection/test").json()
                evidence["discovery_before"] = client.get("/api/models").json()
                if args.install:
                    with client.stream("POST", "/api/models/install", json={"model": args.model}, timeout=180) as response:
                        response.raise_for_status()
                        evidence["explicit_install_events"] = [json.loads(line) for line in response.iter_lines() if line]
                response = client.post("/api/models/test", json={"model": args.model})
                evidence["inference"] = {"http_status": response.status_code, **response.json()}
                evidence["discovery_after"] = client.get("/api/models").json()
                evidence["catalog"] = {k: v for k, v in client.get("/api/model-catalog").json().items() if k in ["scope", "total", "last_refresh_attempt"]}
        finally:
            process.terminate()
            process.wait(timeout=15)
    destination = root / "docs" / "local-inference-live.json"
    destination.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence.get("inference"), indent=2))
    print("Live evidence: " + str(destination))
    if not evidence.get("inference", {}).get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
