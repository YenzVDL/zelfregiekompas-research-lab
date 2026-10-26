"""Create one NEW unapproved scenario through the running port-8000 application.

Unlike the isolated acceptance script, this deliberately adds a clearly named
verification dataset/job to the current workspace. Never rewrites older records.
Run after restarting the backend: python -m scripts.verify_active_ict_runtime
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import httpx
from backend.scenario_generation import ICT_REQUIREMENT, VERSION, check_scope


def history_hashes(database):
    with sqlite3.connect(database) as connection:
        return {identifier: hashlib.sha256(body.encode()).hexdigest() for identifier, body in connection.execute(
            "SELECT id,body FROM records WHERE kind IN ('scenarios','snapshots')")}


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = {"base_url": "http://127.0.0.1:8000", "evidence": "NEW generation job on the active application, real local Ollama; no fixtures",
                "browser_screenshot": "browser-active-ict-runtime.png", "previous_runtime_version": "synthetic-inputs-v2"}
    with httpx.Client(base_url=evidence["base_url"], trust_env=False, timeout=20) as client:
        protocol_response = client.get("/api/protocol")
        protocol_response.raise_for_status()
        protocol = protocol_response.json()
        assert protocol["scenario_prompt_version"] == VERSION, "Restart the backend before verification"
        assert protocol["scenario_system_prompt"].startswith(ICT_REQUIREMENT)
        database = client.get("/api/health").json()["database"]
        before = history_hashes(database)
        evidence["active_protocol"] = protocol
        dataset_response = client.post("/api/datasets", json={"name": "LIVE active-runtime ICT prompt verification", "description": "One requested runtime verification scenario, requiring human review."})
        dataset_response.raise_for_status()
        dataset = dataset_response.json()
        evidence["dataset_id"] = dataset["id"]
        response = client.post("/api/scenario-tasks/generation", json={"dataset_id": dataset["id"], "model": "qwen3:0.6b", "count": 1, "category": "normal"})
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = client.get("/api/jobs/" + job["id"]).json()
            if job["status"] not in ["queued", "running", "cancelling"]:
                break
            time.sleep(0.25)
        evidence["job"] = job
        evidence["drafts"] = client.get("/api/scenarios", params={"dataset_id": dataset["id"]}).json()
        after = history_hashes(database)
        evidence["historical_scenarios_and_snapshots_checked"] = len(before)
        evidence["historical_records_unchanged"] = all(after.get(identifier) == value for identifier, value in before.items())
    (root / "docs" / "active-ict-runtime-live.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    assert job["status"] == "completed" and job["done"] == 1, job.get("error")
    scenario = evidence["drafts"][0]
    payload = scenario["generation"]["request"]["payload"]
    assert payload["system"].startswith(ICT_REQUIREMENT)
    assert scenario["generation"]["request"]["capture_source"] == "httpx request immediately before send"
    assert scenario["review_status"] == "awaiting_review"
    check_scope(scenario["inputs"])
    assert evidence["historical_records_unchanged"]
    print(json.dumps({"job": job["id"], "version": protocol["scenario_prompt_version"], "status": job["status"],
                      "scenario_version_id": scenario["id"], "learning_goal": scenario["inputs"]["learning_goal"], "planned_activity": scenario["inputs"]["planned_activity"],
                      "actual_system_prefix": payload["system"][:len(ICT_REQUIREMENT)], "historical_records_unchanged": True}, indent=2))


if __name__ == "__main__":
    main()
