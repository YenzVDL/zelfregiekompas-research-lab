"""Live scenario endpoint acceptance, isolated from the user's research workspace.

Run: .venv/Scripts/python.exe -m scripts.verify_scenario_generation --model qwen3:0.6b
Starts backend port 8001, creates five drafts, and archives actual outgoing requests
and raw model responses. No model download, research approval or provider fixture.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--ict", action="store_true", help="Also verify ICT scope, actual-request provenance, exports and a revision")
    parser.add_argument("--browser", action="store_true", help="Check the prompt section in an isolated real browser (requires --ict)")
    args = parser.parse_args()
    if args.browser and not args.ict:
        parser.error("--browser requires --ict")
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8001)) == 0:
            raise SystemExit("Port 8001 is occupied; refusing to use another backend")
    prefix = "ict-scenario" if args.ict else "scenario"
    evidence_path = root / "docs" / f"{prefix}-generation-live.json"
    request_path = root / "docs" / f"{prefix}-live-requests.jsonl"
    request_path.write_text("", encoding="utf-8")
    evidence = {"evidence": "Actual HTTP generation endpoint and real local Ollama; no fixtures", "original_failed_job": "job_b84e140cb11f4e03"}
    # This request hook observes real HTTP requests without modifying them or responses.
    code = '''import json, logging, os, httpx
logging.basicConfig(level=logging.INFO)
original = httpx.AsyncClient
async def capture(request):
    if request.url.path == "/api/generate":
        with open(os.environ["SCENARIO_REQUEST_EVIDENCE"], "a", encoding="utf-8") as output:
            output.write(json.dumps({"address": str(request.url), "payload": json.loads(request.content)}) + "\\n")
def client(**kwargs):
    hooks = kwargs.pop("event_hooks", {})
    hooks.setdefault("request", []).append(capture)
    return original(event_hooks=hooks, **kwargs)
httpx.AsyncClient = client
import uvicorn
uvicorn.run("backend.main:app", host="127.0.0.1", port=8001)
'''
    log_path = root / "docs" / f"{prefix}-generation-server.log"
    with tempfile.TemporaryDirectory() as directory, log_path.open("w", encoding="utf-8") as logfile:
        env = {**os.environ, "RESEARCH_DB": str(Path(directory) / "live-scenarios.db"), "SCENARIO_REQUEST_EVIDENCE": str(request_path)}
        process = subprocess.Popen([sys.executable, "-c", code], cwd=root, env=env, stdout=logfile, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url="http://127.0.0.1:8001", trust_env=False, timeout=20) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Backend failed to start: " + str(log_path))
                    try:
                        client.get("/api/health").raise_for_status()
                        break
                    except httpx.ConnectError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Backend readiness timed out")
                dataset = client.post("/api/datasets", json={"name": "LIVE VERIFICATION: synthetic generation, unapproved drafts"}).json()
                response = client.post("/api/scenario-tasks/generation", json={"dataset_id": dataset["id"], "model": args.model, "category": "normal", "count": 5, "split": "development", "missing_fields": []})
                response.raise_for_status()
                job = response.json()
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    job = client.get("/api/jobs/" + job["id"]).json()
                    if job["status"] not in ["running", "queued", "cancelling"]:
                        break
                    time.sleep(0.5)
                evidence["job"] = job
                evidence["drafts"] = client.get("/api/scenarios", params={"dataset_id": dataset["id"]}).json()
                snapshot = client.post("/api/snapshots", json={"kind": "dataset", "dataset_id": dataset["id"], "name": "Must require approval"})
                evidence["unapproved_snapshot_attempt"] = {"http_status": snapshot.status_code, "response": snapshot.json()}
                if args.ict:
                    evidence["dataset_id"] = dataset["id"]
                    parent_response = client.post("/api/scenarios", json={"dataset_id": dataset["id"], "row": {"inputs": {
                        "learning_goal": "Learn addition and subtraction", "planned_activity": "Use colored blocks", "previous_question": "What would you try next?"
                    }, "synthetic_origin": "LIVE VERIFICATION: manually authored out-of-scope revision input"}})
                    parent_response.raise_for_status()
                    evidence["revision_parent"] = parent_response.json()
                    revision_response = client.post("/api/scenario-tasks/revision", json={"version_id": evidence["revision_parent"]["id"], "model": args.model,
                        "feedback": "Replace the colored-block arithmetic with a genuine ICT learning goal and a concrete programming or SQL activity. Keep the general reflection question if appropriate."})
                    revision_response.raise_for_status()
                    revision = revision_response.json()
                    deadline = time.monotonic() + 300
                    while time.monotonic() < deadline:
                        revision = client.get("/api/jobs/" + revision["id"]).json()
                        if revision["status"] not in ["running", "queued", "cancelling"]:
                            break
                        time.sleep(0.5)
                    evidence["revision_job"] = revision
                    records = client.get("/api/scenarios", params={"dataset_id": dataset["id"], "history": "true"}).json()
                    evidence["revised_scenario"] = next((r for r in records if r["id"] in revision.get("results", [])), None)
                    first = evidence["drafts"][0]
                    evidence["version_export"] = client.get(f'/api/scenarios/{first["id"]}/export').json()
                    evidence["dataset_export"] = client.get(f'/api/datasets/{dataset["id"]}/export?include_provenance=true').json()
                    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
                    if args.browser:
                        checked = subprocess.run(["node", str(root / "scripts" / "verify_ict_prompt_ui.mjs"), str(evidence_path)], cwd=root, check=False)
                        evidence["browser_check"] = {"exit_code": checked.returncode, "responses_mocked": False, "origin_header_mapped_for_isolated_port": True}
        finally:
            process.terminate()
            process.wait(timeout=15)
    evidence["observed_ollama_requests"] = [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()]
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    job = evidence["job"]
    summary = {"job_id": job["id"], "status": job["status"], "done": job["done"], "total": job["total"], "attempts": len(job.get("scenario_attempts", [])), "snapshot_status": evidence["unapproved_snapshot_attempt"]["http_status"], "error": job.get("error")}
    print(json.dumps(summary, indent=2))
    from backend.schema import Inputs
    for draft in evidence["drafts"]:
        Inputs.model_validate(draft["inputs"])
        assert set(draft["inputs"]) == set(Inputs.model_fields) and draft["review_status"] == "awaiting_review"
    assert job["status"] == "completed" and job["done"] == 5
    assert evidence["unapproved_snapshot_attempt"]["http_status"] == 422
    assert all(isinstance(r["payload"]["format"], dict) for r in evidence["observed_ollama_requests"])
    if args.ict:
        from backend.scenario_generation import check_scope
        assert evidence["revision_job"]["status"] == "completed", evidence["revision_job"].get("error")
        for scenario in [*evidence["drafts"], evidence["revised_scenario"]]:
            check_scope(scenario["inputs"])
            generation = scenario["generation"]
            assert generation["request"]["payload"] in [r["payload"] for r in evidence["observed_ollama_requests"]]
            assert generation["saved_attempt_id"] in [a["id"] for a in generation["attempts"]]
            assert "ICT education" in generation["request"]["payload"]["system"]
        assert evidence["version_export"]["scenario"]["generation"] == evidence["drafts"][0]["generation"]
        print("Real ICT scope, generation/revision request capture and exports verified.")
        if args.browser:
            assert evidence["browser_check"]["exit_code"] == 0, "Live browser prompt check failed; endpoint evidence retained"


if __name__ == "__main__":
    main()
