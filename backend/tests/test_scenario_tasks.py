import json
from backend.schema import Inputs
from .conftest import wait_job


def test_ai_revision_is_a_candidate_with_feedback_provenance_and_original_split(lab):
    s,r,p,jobs=lab
    d=r.dataset("Revision test fixture")
    original=r.scenario(d["id"],{"inputs":{"learning_goal":"Original goal"},"split":"test","family_id":"fixture-family"})
    r.review("scenarios",original["id"],"approved","human-1","Reviewed")
    def generate(model,system,prompt,settings,cancel,json_mode=False,response_schema=None):
        assert response_schema["additionalProperties"] is False and '"reviewer_feedback": "Clarify the goal"' in prompt
        return {"text":json.dumps({"learning_goal":"Clarified Python programming goal"}),"latency_seconds":.01,"runtime":{}}
    p.generate=generate
    j=jobs.scenario_task("revision",{"version_id":original["id"],"feedback":"Clarify the goal","model":"test-fixture-model"})
    final=wait_job(s,j["id"])
    assert final["status"]=="completed",final
    candidate=s.get("scenarios",final["results"][0])
    assert candidate["parent_id"]==original["id"] and candidate["review_status"]=="awaiting_review"
    assert candidate["split"]=="test" and candidate["family_id"]=="fixture-family"
    assert candidate["generation"]["model"]=="test-fixture-model"
    assert s.get("scenarios",original["id"])["inputs"]["learning_goal"]=="Original goal"


def test_targeted_generation_enforces_unknown_fields_and_counts(lab):
    s,r,p,jobs=lab
    d=r.dataset("Generation fixture")
    goals = iter(['Understand SQL joins', 'Understand SQL query filtering'])
    p.generate=lambda *a,**kw:{"text":json.dumps({"learning_goal":next(goals), "progress":None}),"latency_seconds":.01,"runtime":{}}
    j=jobs.scenario_task("generation",{"dataset_id":d["id"],"model":"test-fixture-model","category":"incomplete","count":2,"missing_fields":["progress"]})
    final=wait_job(s,j["id"])
    assert final["status"]=="completed" and len(final["results"])==2
    for identifier in final["results"]:
        row=s.get("scenarios",identifier)
        assert row["inputs"]["progress"] is None and row["review_status"]=="awaiting_review"
        assert row["tags"]==["incomplete"] and row["generation"]["role"]=="generation"


def test_synthetic_model_role_overlap_is_flagged(lab):
    s,r,p,jobs=lab
    d=r.dataset("Role test")
    v=r.scenario(d["id"],{"inputs":{},"tags":["incomplete"]},generation={"model":"test-fixture-model"})
    r.review("scenarios",v["id"],"approved","reviewer-1","")
    snapshot=r.snapshot("dataset","Role snapshot",d["id"])
    j=jobs.create_experiment({"name":"Roles", "dataset_snapshot_id":snapshot["id"], "methods":["constrained"],"models":["test-fixture-model"]})
    final=wait_job(s,j["id"])
    assert "scenario generation/revision" in final["bias_flags"][0]
