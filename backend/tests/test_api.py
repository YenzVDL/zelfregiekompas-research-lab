from fastapi.testclient import TestClient
from backend.main import create_app
from .conftest import approved_inputs, wait_job


def test_http_import_review_revision_snapshot_run_blind_evaluation_metrics_export(lab):
    s,r,p,unused_jobs=lab
    app=create_app(s,p)
    with TestClient(app) as c:
        d=c.post('/api/datasets',json={"name":"HTTP synthetic fixture"}).json()
        payload={"format":"json","text":'[{"inputs":{"learning_goal":"Study a graph","student_note":"Secret note"},"research_labels":{"never_leak":"SECRET-LABEL"}}]'}
        assert c.post(f'/api/datasets/{d["id"]}/import/preview',json=payload).json()["valid"]==1
        original=c.post(f'/api/datasets/{d["id"]}/import/commit',json=payload).json()["created"][0]
        revised=c.post(f'/api/scenarios/{original["id"]}/revise',json={"inputs":{"learning_goal":"Understand a graph"}}).json()
        assert revised["parent_id"]==original["id"] and revised["review_status"]=="awaiting_review"
        assert c.post(f'/api/scenarios/{revised["id"]}/review',json={"decision":"approved","reviewer":"human-1"}).status_code==200
        q=c.post('/api/questions',json={"text":"What would you like to explore about the graph?"}).json()
        c.post(f'/api/questions/{q["id"]}/review',json={"decision":"approved","reviewer":"human-1"})
        ds=c.post('/api/snapshots',json={"kind":"dataset","dataset_id":d["id"],"name":"Dataset fixture"}).json()
        bank=c.post('/api/snapshots',json={"kind":"bank","name":"Bank fixture"}).json()
        j=c.post('/api/experiments',json={"name":"HTTP run","dataset_snapshot_id":ds["id"],"bank_snapshot_id":bank["id"],"methods":["fixed"]}).json()
        assert wait_job(s,j["id"])["status"]=="completed"
        queue=c.get('/api/evaluation-queue').json()
        assert len(queue)==1 and 'method' not in queue[0] and 'model' not in queue[0] and 'trace' not in queue[0]
        assert 'SECRET-LABEL' not in str(queue)
        judgment={k:{"value":"Yes","explanation":"Open and answerable","context":"The goal is to understand a graph"} for k in ['usefulness','correctness']}
        body={"output_id":queue[0]['id'],"output_version":queue[0]['version'],"reviewer":"human-1","judgment":judgment}
        assert c.post('/api/labels',json=body).status_code==200
        assert c.post('/api/labels',json={**body,'reviewer':'human-2'}).status_code==422
        assert c.post('/api/labels',json={**body,'reviewer':'human-2','role':'secondary'}).status_code==200
        metrics=c.get(f'/api/jobs/{j["id"]}/metrics').json()
        assert metrics['quality']['human']['overall']['end_to_end_acceptance']==1
        assert metrics['evaluator_accuracy']['usefulness']['paired_count']==0
        exported=c.get('/api/export').json()
        assert exported['schema_version']=='zelfregiekompas-export-v1' and exported['records']['labels']
        assert not p.calls  # All production requests above are actual deterministic rule execution.


def test_local_origin_guard_and_stale_labels(lab):
    s,r,p,_=lab
    with TestClient(create_app(s,p)) as c:
        assert c.get('/api/datasets',headers={'origin':'https://external.example'}).status_code==403
        assert c.get('/api/datasets',headers={'host':'external.example'}).status_code==403
        assert c.get('/api/health').status_code==200
