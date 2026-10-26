import json
import time
import threading
import pytest
from backend.methods import prepare, output_errors
from backend.provider import Ollama, ProviderError, Cancelled
from backend.jobs import Jobs
from .conftest import approved_inputs, wait_job


def test_three_methods_are_distinct_and_no_metadata_in_prompts(lab):
    s,r,provider,jobs=lab
    _,_,_,ds,bank=approved_inputs(r)
    j=jobs.create_experiment({"name":"Fixture comparison", "dataset_snapshot_id":ds["id"],"bank_snapshot_id":bank["id"],"methods":["fixed","rag","constrained"],"models":["test-fixture-model"]})
    final=wait_job(s,j["id"])
    assert final["status"] == "completed" and final["done"] == 3
    assert len(provider.calls) == 2  # fixed never uses a provider
    results={o["method"]:o for o in s.all("outputs")}
    assert results["fixed"]["model"] is None and results["fixed"]["trace"]["selected_question_id"]
    assert results['rag']['trace']['retrieved'] == [] and results['rag']['trace']['retrieval_status'] == 'no_relevant_match'
    assert results["constrained"]["trace"]["retrieved"] == []
    assert "approved_question_suggestions" not in results["constrained"]["trace"]["prompt"]
    for call in provider.calls:
        assert "review_status" not in call["prompt"] and "research_labels" not in call["prompt"]


def test_fixed_fallback_and_no_match():
    generic={"id":"v1","question_id":"q1","text":"What would you consider?","priority":0,"conditions":{},"tags":[]}
    assert prepare("fixed",{},[generic])["fallback"]
    specific={**generic,"conditions":{"present":["student_note"]}}
    with pytest.raises(ValueError,match="No matching"):
        prepare("fixed",{},[specific])
    assert not prepare("fixed",{"student_note":"Data"},[specific])["fallback"]


def test_invalid_empty_timeout_and_provider_errors_are_persisted(lab):
    s,r,provider,jobs=lab
    _,_,_,ds,_=approved_inputs(r)
    for response,error,status in [("",None,"empty_final_answer"),("One? Two?",None,"invalid"),("x",ProviderError("fixture timeout","timeout","partial"),"timeout"),("x",ProviderError("offline"),"failed")]:
        provider.response,provider.error=response,error
        j=jobs.create_experiment({"name":"Failure fixture", "dataset_snapshot_id":ds["id"],"methods":["constrained"],"models":["test-fixture-model"]})
        wait_job(s,j["id"])
        o=[o for o in s.all("outputs") if o["job_id"]==j["id"]][0]
        assert o["status"]==status and o["attempt_started_at"]
        if error:
            assert o["partial_text"]==error.partial


def test_ai_control_off_does_not_disable_generation_and_on_keeps_labels_separate(lab):
    s,r,p,jobs=lab
    _,_,_,ds,_=approved_inputs(r)
    j=jobs.create_experiment({"name":"No control", "dataset_snapshot_id":ds["id"],"methods":["constrained"],"models":["test-fixture-model"],"ai_control":False})
    wait_job(s,j["id"])
    assert len(p.calls)==1 and not s.all("labels")
    j2=jobs.create_experiment({"name":"AI fixture", "dataset_snapshot_id":ds["id"],"methods":["constrained"],"models":["test-fixture-model"],"ai_control":True,"evaluator_model":"test-fixture-model"})
    wait_job(s,j2["id"])
    assert s.all("labels")[0]["source"]=="ai"
    assert s.get("jobs",j2["id"])["bias_flags"]


def test_interruption_recovery_retains_finalized_outputs_and_versions_pending_attempts(lab):
    s,r,p,jobs=lab
    _,_,_,ds,bank=approved_inputs(r)
    jobs.schedule=lambda _:None
    j=jobs.create_experiment({"name":"Interrupted fixture", "dataset_snapshot_id":ds["id"],"bank_snapshot_id":bank["id"],"methods":["fixed"],"repetitions":2})
    outputs=s.all("outputs")
    s.patch("jobs",j["id"],status="running")
    s.patch("outputs",outputs[0]["id"],status="completed",question="What would you consider?",attempt_started_at="fixture")
    s.patch("outputs",outputs[1]["id"],status="running",partial_text="Partial",attempt_started_at="fixture")
    jobs.recover()
    assert s.get("jobs",j["id"])["status"]=="interrupted"
    jobs.schedule=lambda ident:jobs._run(ident)
    jobs.resume(j["id"])
    assert s.get("outputs",outputs[0]["id"])["question"]=="What would you consider?"
    second=s.get("outputs",outputs[1]["id"])
    assert second["status"]=="completed" and second["version"]==2
    assert second["attempt_history"][0]["partial_text"]=="Partial"
    assert not p.calls


def test_cancellation_saves_partial_and_resume_completes(lab):
    s,r,p,jobs=lab
    _,_,_,ds,_=approved_inputs(r)
    entered=threading.Event()
    original=p.generate
    def slow(*args,**kwargs):
        entered.set()
        while not args[4]():
            time.sleep(.01)
        raise Cancelled("fixture partial tokens")
    p.generate=slow
    j=jobs.create_experiment({"name":"Cancellation", "dataset_snapshot_id":ds["id"],"methods":["constrained"],"models":["test-fixture-model"]})
    assert entered.wait(3)
    jobs.cancel(j["id"])
    assert wait_job(s,j["id"])["status"]=="cancelled"
    assert s.all("outputs")[0]["partial_text"]=="fixture partial tokens"
    # _run's final bookkeeping releases schedule membership just after status.
    deadline=time.monotonic()+2
    while j["id"] in jobs.scheduled and time.monotonic()<deadline:
        time.sleep(.01)
    p.generate=original
    jobs.resume(j["id"])
    assert wait_job(s,j["id"])["status"]=="completed"
    assert s.all("outputs")[0]["version"]==2


def test_output_format_checks_do_not_claim_semantic_verification():
    assert output_errors("What could you try next?", "What could you try next?")
    assert output_errors("A statement")
    assert not output_errors("What would you like to consider?")


@pytest.mark.parametrize("url",["https://api.example.com", "http://192.168.0.4:11434", "http://127.0.0.1.evil.com", "http://localhost/path", "http://user:password@localhost"])
def test_provider_refuses_non_loopback_and_non_origin_urls(url):
    with pytest.raises(ValueError):
        Ollama(url)


def test_provider_discovery_excludes_cloud_and_unverified_models(monkeypatch):
    import httpx
    real=httpx.Client
    def handler(request):
        if request.url.path=="/api/tags":
            return httpx.Response(200,json={"models":[{"name":"installed-local"},{"name":"remote"},{"name":"unverified"}]})
        model=json.loads(request.content)["model"]
        if model=="unverified":
            return httpx.Response(500)
        return httpx.Response(200,json={"remote_host":"https://example.com"} if model=="remote" else {"model_info":{}})
    monkeypatch.setattr(httpx,"Client",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    assert [m["name"] for m in Ollama().models()]==["installed-local"]
