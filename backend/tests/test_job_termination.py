import threading
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.store import Store
from .conftest import wait_job


def queued(s, jobs, name):
    job = jobs._job("experiment", {"name": name})
    s.put("jobs", job, insert=True)
    return job


def test_remove_waiting_job_immediately_cancels_future_and_never_executes(lab):
    s, _, _, jobs = lab
    entered, release = threading.Event(), threading.Event()
    executed = []
    def execute(job):
        executed.append(job["id"])
        entered.set()
        assert release.wait(5)
    jobs._experiment = execute
    first, second = queued(s, jobs, "Blocking fixture"), queued(s, jobs, "Remove fixture")
    jobs.schedule(first["id"])
    try:
        assert entered.wait(2)
        jobs.schedule(second["id"])
        future = jobs.futures[second["id"]]
        terminated = jobs.terminate(second["id"])
        assert terminated["status"] == "terminated" and terminated["status_before_termination"] == "queued"
        assert future.cancelled() and second["id"] not in jobs.scheduled
        jobs._run(second["id"])  # Even an already-dispatched stale callback cannot resurrect it.
        jobs.schedule(second["id"])
        assert executed == [first["id"]] and s.get("jobs", second["id"])["status"] == "terminated"
        with pytest.raises(ValueError, match="terminated jobs cannot"):
            jobs.resume(second["id"])
    finally:
        release.set()
    assert wait_job(s, first["id"])["status"] == "completed"


def test_cancel_waiting_job_is_immediate_and_can_explicitly_resume(lab):
    s, _, _, jobs = lab
    entered, release = threading.Event(), threading.Event()
    executed = []
    first, second = queued(s, jobs, "Blocking fixture"), queued(s, jobs, "Cancel fixture")
    def execute(job):
        executed.append(job["id"])
        if job["id"] == first["id"]:
            entered.set()
            assert release.wait(5)
    jobs._experiment = execute
    jobs.schedule(first["id"])
    try:
        assert entered.wait(2)
        jobs.schedule(second["id"])
        assert jobs.cancel(second["id"])["status"] == "cancelled"
        jobs.resume(second["id"])
        assert s.get("jobs", second["id"])["status"] == "queued"
    finally:
        release.set()
    assert wait_job(s, second["id"])["status"] == "completed"
    assert executed.count(second["id"]) == 1


def test_termination_endpoint_preserves_failed_job_drafts_and_diagnostics(lab):
    s, r, provider, jobs = lab
    dataset = r.dataset("Retained fixture")
    draft = r.scenario(dataset["id"], {"inputs": {"learning_goal": "Retained draft"}})
    job = queued(s, jobs, "Failed fixture")
    s.patch("jobs", job["id"], status="failed", error="Fixture validation failed", done=1, total=5,
            results=[draft["id"]], scenario_attempts=[{"raw_response": '{"bad":true}', "status": "invalid"}])
    with TestClient(create_app(s, provider)) as client:
        result = client.post(f'/api/jobs/{job["id"]}/terminate').json()
        assert result["status"] == "terminated" and result["done"] == 1
        assert result["error"] == "Fixture validation failed" and result["results"] == [draft["id"]]
        assert result["scenario_attempts"][0]["raw_response"] == '{"bad":true}'
        assert client.post(f'/api/jobs/{job["id"]}/terminate').json()["terminated_at"] == result["terminated_at"]
        assert client.post(f'/api/jobs/{job["id"]}/resume').status_code == 422
        assert client.get(f'/api/jobs/{job["id"]}/export').status_code == 200
    assert s.get("scenarios", draft["id"])["review_status"] == "awaiting_review"
    assert Store(s.path).get("jobs", job["id"])["status"] == "terminated"
    jobs.recover()
    assert s.get("jobs", job["id"])["status"] == "terminated"


@pytest.mark.parametrize("status", ["running", "cancelling", "completed"])
def test_terminate_rejects_active_or_completed_jobs_without_changing_them(lab, status):
    s, _, _, jobs = lab
    job = queued(s, jobs, "Guard fixture")
    s.patch("jobs", job["id"], status=status)
    with pytest.raises(ValueError, match="Only queued"):
        jobs.terminate(job["id"])
    assert s.get("jobs", job["id"])["status"] == status
