import time
import pytest
from backend.store import Store
from backend.research import Research
from backend.jobs import Jobs
from backend.provider import ProviderError


class FixtureProvider:
    """Test double only. Never installed in the running application."""
    def __init__(self):
        self.calls = []
        self.response = "What would you like to try in your next learning activity?"
        self.error = None

    def models(self):
        return [{"name": "test-fixture-model", "digest": "fixture", "details": {}}]

    def generate(self, model, system, prompt, settings, cancel, json_mode=False, response_schema=None):
        self.calls.append({"model": model, "system": system, "prompt": prompt, "json_mode": json_mode, "response_schema": response_schema, "settings": settings})
        if self.error:
            raise self.error
        if json_mode or (response_schema and 'usefulness' in response_schema.get('properties', {})):
            text = '{"usefulness":{"value":"Yes","explanation":"Fixture only","context":"Fixture activity"},"correctness":{"value":"No","explanation":"Fixture only","context":"Fixture context"}}'
        else:
            text = self.response
        return {"text": text, "latency_seconds": .01, "runtime": {"load_duration": 1}}


@pytest.fixture
def lab(tmp_path):
    store = Store(tmp_path / "research.sqlite3")
    research = Research(store)
    provider = FixtureProvider()
    jobs = Jobs(store, research, provider)
    yield store, research, provider, jobs
    jobs.close()


def wait_job(s, identifier):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        row = s.get("jobs", identifier)
        if row["status"] not in ["running", "queued", "cancelling"]:
            return row
        time.sleep(.01)
    raise AssertionError("Job did not finish")


def approved_inputs(r):
    d = r.dataset("Synthetic test fixture")
    row = r.scenario(d["id"], {"inputs": {"learning_goal": "Understand fractions", "planned_activity": "Draw fractions"}, "tags": ["normal"]})
    q = r.question({"text": "What would you like to reflect on about this activity?"})
    r.review("scenarios", row["id"], "approved", "reviewer-1", "Fixture")
    r.review("questions", q["id"], "approved", "reviewer-1", "Fixture")
    ds = r.snapshot("dataset", "dataset fixture", d["id"])
    bank = r.snapshot("bank", "bank fixture")
    return d, row, q, ds, bank
