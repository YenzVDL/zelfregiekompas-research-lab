import csv
import io
import json
import os
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .store import Store, now, uid
from .research import Research
from .provider import Ollama, ProviderError
from .catalog import Catalog
from .jobs import Jobs, Thresholds, ScenarioSettings
from .schema import Judgment
from .metrics import report, latest_labels
from . import methods, demo, scenario_generation, bank_questions, bank_diagnostics, experiment_preflight

KINDS = ["datasets", "banks", "scenarios", "questions", "reviews", "snapshots", "jobs", "outputs", "labels", "influences", "model_metadata", "settings", "assessment_attempts", "preflights", "calibrations"]
log = logging.getLogger(__name__)


def create_app(store=None, provider=None):
    s = store or Store()
    research = Research(s)
    if provider is None:
        saved = next((r for r in s.all("settings") if r["id"] == "ollama_connection"), {})
        provider = Ollama(os.getenv("OLLAMA_URL") or saved.get("address"))
    jobs = Jobs(s, research, provider)
    catalog = Catalog(s)

    @asynccontextmanager
    async def lifespan(app):
        jobs.recover()
        yield
        jobs.close()

    app = FastAPI(title="Zelfregiekompas Research Lab", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.jobs, app.state.research = s, jobs, research

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.hostname not in ["127.0.0.1", "localhost", "::1", "testserver"]:
            return JSONResponse({"detail": "Local loopback hosts only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in ["http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"]:
            return JSONResponse({"detail": "External origins cannot access research records"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(ValueError)
    async def bad_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ProviderError)
    async def provider_failure(request, exc):
        return JSONResponse({"detail": str(exc), "kind": exc.kind}, status_code=503)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "inference": "local only", "database": s.path, "version": "1.0.0"}

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def local_reference():
        from html import escape
        routes = "".join(f"<tr><td>{escape(', '.join(sorted(r.methods or [])))}</td><td><code>{escape(r.path)}</code></td><td>{escape(r.name)}</td></tr>"
                         for r in app.routes if getattr(r, "path", "").startswith("/api/"))
        return HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><title>Local API reference</title><style>body{font:15px system-ui;max-width:1100px;margin:40px auto;color:#203943}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}table{width:100%}a{color:#15766d}</style><h1>Zelfregiekompas local API</h1><p>All endpoints use JSON unless an export format is selected. Full machine-readable request/response definitions: <a href="/openapi.json">OpenAPI schema</a>. Import examples and workflow details are in README.md.</p><p>This reference uses local assets and makes no external network requests.</p><table><thead><tr><th>Method</th><th>Path</th><th>Operation</th></tr></thead><tbody>' + routes + '</tbody></table></html>')

    @app.get("/api/models")
    def models():
        try:
            return {"available": True, "models": provider.models(), "manual_metadata": s.all("model_metadata")}
        except ProviderError as e:
            return {"available": False, "models": [], "error": str(e), "manual_metadata": s.all("model_metadata")}

    @app.post('/api/experiments/preflight')
    def preflight(body: dict):
        return experiment_preflight.run(jobs, body)

    @app.post('/api/evaluation/eligible')
    def eligible_evaluation(body: dict):
        return jobs.evaluation_preview(body)

    @app.post('/api/evaluation/jobs')
    def evaluate_saved(body: dict):
        return jobs.evaluation_task(body)

    @app.post('/api/evaluation/calibrate')
    def calibrate_evaluator(body: dict):
        return jobs.calibrate(body)

    @app.get('/api/evaluation/attempts')
    def evaluation_attempts(job_id: str | None = None, output_id: str | None = None):
        return [a for a in s.all('assessment_attempts') if (not job_id or a.get('evaluation_job_id') == job_id) and (not output_id or a.get('output_id') == output_id)]

    @app.get('/api/banks/{identifier}/diagnostics')
    def bank_diagnostic(identifier: str, dataset_snapshot_id: str | None = None, bank_snapshot_id: str | None = None):
        s.get('banks', identifier)
        if bank_snapshot_id:
            snapshot = s.get('snapshots', bank_snapshot_id)
            if research.bank_snapshot_identity(snapshot)['bank_id'] != identifier:
                raise ValueError('Snapshot belongs to a different bank')
            bank = snapshot['records']
        else:
            bank = research.latest('questions', bank_id=identifier)
        cases = []
        if dataset_snapshot_id:
            snapshot = s.get('snapshots', dataset_snapshot_id)
            if snapshot['kind'] != 'dataset':
                raise ValueError('Select a development dataset snapshot')
            cases = [row for row in snapshot['records'] if row['split'] == 'development']
        return bank_diagnostics.inspect(bank, cases)

    @app.get("/api/model-catalog")
    def model_catalog(search: str = "", family: str = "", generation: str = "", parameter_size: str = "", quantization: str = ""):
        return catalog.browse(search, family, generation, parameter_size, quantization)

    @app.post("/api/model-catalog/refresh")
    def refresh_catalog():
        return catalog.refresh()

    def managed_provider():
        if not isinstance(provider, Ollama):
            raise HTTPException(503, "Ollama management is unavailable for this provider")
        return provider

    def management_lock():
        p = managed_provider()
        if not p.operation_lock.acquire(blocking=False):
            raise HTTPException(409, "Ollama is busy installing or running inference; try again when it finishes")
        if any(j["status"] in ["queued", "running", "cancelling"] for j in s.all("jobs")):
            p.operation_lock.release()
            raise HTTPException(409, "Finish or cancel active jobs before changing the connection or installing a model")
        return p

    @app.get("/api/models/connection")
    def model_connection():
        return {"address": managed_provider().url}

    @app.put("/api/models/connection")
    def configure_connection(body: dict):
        p = management_lock()
        try:
            p.configure(body.get("address", ""))
            s.put("settings", {"id": "ollama_connection", "address": p.url, "updated_at": now()})
            return {"address": p.url}
        finally:
            p.operation_lock.release()

    @app.post("/api/models/connection/test")
    def test_connection():
        p = managed_provider()
        started = time.perf_counter()
        try:
            return {**p.connection_test(), "elapsed_seconds": time.perf_counter() - started}
        except ProviderError as e:
            return JSONResponse({"detail": str(e), "elapsed_seconds": time.perf_counter() - started}, status_code=503)

    @app.post("/api/models/install")
    def install_model(body: dict):
        p = managed_provider()
        name = p.model_name(body.get("model"))
        p = management_lock()

        def stream():
            try:
                for event in p.pull(name):
                    yield json.dumps(event) + "\n"
            except ProviderError as e:
                yield json.dumps({"error": str(e), "done": False}) + "\n"
            finally:
                p.operation_lock.release()
        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/models/test")
    def test_inference(body: dict):
        p = managed_provider()
        started = time.perf_counter()
        try:
            # generate verifies exact installation/locality; it never pulls missing weights.
            result = p.generate(body.get("model", ""), "", "Reply with only: OK",
                                {"temperature": 0, "num_predict": 16, "num_ctx": 2048, "timeout_seconds": 60, "_fail_if_busy": True, "_short_test": True, "_stream": False}, lambda: False)
            if not result["text"]:
                raise ProviderError("Inference completed with an empty response", "empty_output")
            return {"success": True, "elapsed_seconds": time.perf_counter() - started, "response": result["text"], "model_metadata": result["model_metadata"],
                    "runtime": result["runtime"], "effective_settings": result["effective_settings"], "diagnostics": result["diagnostics"]}
        except ProviderError as e:
            log.exception("Short inference test failed model=%s address=%s kind=%s http_status=%s details=%s", body.get("model"), p.url, e.kind, e.http_status, e.diagnostics)
            return JSONResponse({"success": False, "detail": str(e), "kind": e.kind, "partial_text": e.partial,
                                 "http_status": e.http_status, "diagnostics": e.diagnostics,
                                 "elapsed_seconds": time.perf_counter() - started}, status_code=503)

    @app.post("/api/models/metadata")
    def metadata(body: dict):
        if not all(str(body.get(k, "")).strip() for k in ["model", "parameter_count", "provenance"]):
            raise ValueError("Model, manually entered parameter count, and provenance are required")
        return s.put("model_metadata", {"id": uid("meta"), "model": body["model"], "parameter_count": body["parameter_count"],
            "provenance": body["provenance"], "created_at": now()})

    @app.get("/api/protocol")
    def protocol():
        return {"question_system_prompt": methods.SYSTEM, "evaluator_system_prompt": methods.EVALUATOR_SYSTEM,
            "scenario_system_prompt": scenario_generation.SYSTEM, "scenario_prompt_version": scenario_generation.VERSION,
            "scenario_response_schema": scenario_generation.output_schema(), "scenario_attempt_limit": scenario_generation.MAX_ATTEMPTS,
            "scenario_generation_defaults": ScenarioSettings().model_dump(),
            "scenario_topics": scenario_generation.TOPICS, "scenario_variations": scenario_generation.VARIATIONS,
            "bank_question_system_prompt": bank_questions.SYSTEM, "bank_question_prompt_version": bank_questions.VERSION,
            "bank_question_output_schema": bank_questions.output_schema(), "question_condition_paths": bank_questions.PATHS,
            "prompt_version": methods.PROMPT_VERSION, "rule_version": methods.RULE_VERSION, "retrieval_version": methods.RETRIEVAL_VERSION,
            "fixed_rules": "Match present/missing/contains, numeric compare and canonical control_assessment; priority descending, normalized specificity descending, stable ID ascending. Parent/child presence shares one root score; numeric comparisons require units. Otherwise unconditional fallback; otherwise failure. No arbitrary semantic contradiction detection or LLM.",
            "retrieval": "TF-IDF cosine of student text values versus question text only; no JSON keys or tags. Applicability predicates filter eligibility; no fixed-bank priority ranking. Generic stop words removed; score >=0.12 required, stable ID ties. No match is explicit. Lexical relevance is not semantic certification.",
            "constrained": "Same generation constraints and scenario inputs as RAG; no bank or retrieval. Each repetition adds its zero-based index to the seed when provided.",
            "validation": "One line, one question mark, ends in ?, <=40 words, no exact previous-question repetition. English and semantic constraints require human evaluation.",
            "allowed_inputs": list(__import__("backend.schema", fromlist=["Inputs"]).Inputs.model_fields),
            "excluded": "All research metadata, reviewer feedback/labels and evaluation notes. AI-inferred control assessments are also excluded from question prompts."}

    @app.get("/api/datasets")
    def datasets():
        return s.all("datasets")

    @app.post("/api/datasets")
    def dataset(body: dict):
        return research.dataset(body.get("name", ""), body.get("description", ""))

    @app.post("/api/demo")
    def seed_demo():
        with s.connect():
            d = research.dataset("Synthetic demonstration", "Six constructed examples, not representative of real students. All await human review.")
            scenarios = [research.scenario(d["id"], {**row, "synthetic_origin": "DEMONSTRATION: manually constructed synthetic case"}) for row in demo.SCENARIOS]
            questions = [research.question(q) for q in demo.QUESTIONS]
        return {"dataset": d, "scenarios": scenarios, "questions": questions}

    @app.get("/api/datasets/{identifier}/coverage")
    def coverage(identifier: str):
        return research.coverage(identifier)

    @app.post("/api/datasets/{identifier}/import/{action}")
    def import_rows(identifier: str, action: str, body: dict):
        if action not in ["preview", "commit"]:
            raise ValueError("Use preview or commit")
        fn = research.preview if action == "preview" else research.commit_import
        return fn(identifier, body.get("format", "json"), body.get("text", ""), body.get("mapping"))

    @app.get("/api/scenarios")
    def scenarios(dataset_id: str | None = None, search: str = "", status: str = "", tag: str = "", history: bool = False):
        rows = s.all("scenarios") if history else research.latest("scenarios", dataset_id)
        return [r for r in rows if (not dataset_id or r["dataset_id"] == dataset_id) and
                (not status or r["review_status"] == status) and (not tag or tag in r["tags"]) and
                (not search or search.lower() in json.dumps(r).lower())]

    @app.post("/api/scenarios")
    def create_scenario(body: dict):
        return research.scenario(body["dataset_id"], body["row"])

    @app.post("/api/scenarios/{identifier}/revise")
    def revise(identifier: str, body: dict):
        return research.revise(identifier, body)

    @app.get("/api/questions")
    def questions(history: bool = False, bank_id: str | None = None):
        if bank_id is not None:
            s.get("banks", bank_id)
        rows = s.all("questions") if history else research.latest("questions", bank_id=bank_id)
        return [q for q in rows if not bank_id or q["bank_id"] == bank_id]

    @app.get("/api/banks")
    def banks():
        return [research.bank_summary(bank["id"]) for bank in s.all("banks")]

    @app.post("/api/banks")
    def create_bank(body: dict):
        return research.bank(body.get("name", ""), body.get("description", ""))

    @app.post("/api/banks/{identifier}/generate")
    def generate_bank_questions(identifier: str, body: dict):
        if body.get("bank_id", identifier) != identifier:
            raise ValueError("Destination bank does not match request")
        return jobs.question_task({**body, "bank_id": identifier})

    @app.post("/api/questions")
    def question(body: dict):
        return research.question(body)

    @app.post("/api/questions/import")
    def question_import(body: dict):
        raw = body.get("questions")
        if not isinstance(raw, list):
            raise ValueError("questions must be an array of question objects")
        from .schema import Question
        validated = [Question.model_validate(q).model_dump() for q in raw]
        with s.connect():
            return [research.question(q, bank_id=body.get("bank_id")) for q in validated]

    @app.post("/api/questions/{identifier}/revise")
    def question_revise(identifier: str, body: dict):
        return research.question(body, identifier)

    @app.post("/api/{kind}/{identifier}/review")
    def review(kind: str, identifier: str, body: dict):
        return research.review(kind, identifier, body.get("decision"), body.get("reviewer", ""), body.get("feedback", ""))

    @app.get("/api/reviews")
    def reviews(version_id: str | None = None):
        return [r for r in s.all("reviews") if not version_id or r["version_id"] == version_id]

    @app.get("/api/snapshots")
    def snapshots():
        return [{**snapshot, **({"bank_id": research.bank_snapshot_identity(snapshot)["bank_id"],
                  "bank_name": research.bank_snapshot_identity(snapshot)["bank_name"], "review_mode": snapshot.get("review_mode", "approved")}
                 if snapshot["kind"] == "bank" else {})} for snapshot in s.all("snapshots")]

    @app.post("/api/snapshots")
    def snapshot(body: dict):
        return research.snapshot(body.get("kind"), body.get("name", ""), body.get("dataset_id"), body.get("bank_id"), body.get("review_mode", "approved"))

    @app.post("/api/influences")
    def influence(body: dict):
        s.get("snapshots", body["snapshot_id"])
        if body.get("decision_type") not in ["prompt", "rule", "model"] or not body.get("notes", "").strip() or not body.get("researcher", "").strip():
            raise ValueError("Record prompt/rule/model decision, researcher, and notes")
        return s.put("influences", {**body, "id": uid("influence"), "created_at": now()}, insert=True)

    @app.get("/api/influences")
    def influences():
        return s.all("influences")

    @app.post("/api/experiments")
    def experiment(body: dict):
        return jobs.create_experiment(body)

    @app.post("/api/scenario-tasks/{kind}")
    def scenario_task(kind: str, body: dict):
        return jobs.scenario_task(kind, body)

    @app.get("/api/jobs")
    def all_jobs():
        return [jobs.describe(job) for job in s.all("jobs")]

    @app.get("/api/jobs/{identifier}")
    def get_job(identifier: str):
        return jobs.describe(s.get("jobs", identifier))

    @app.post("/api/jobs/{identifier}/{action}")
    def job_action(identifier: str, action: str):
        if action == "cancel":
            return jobs.cancel(identifier)
        if action == "resume":
            return jobs.resume(identifier)
        if action == "terminate":
            return jobs.terminate(identifier)
        if action in ["archive", "unarchive"]:
            return jobs.archive(identifier, archived=action == "archive")
        raise ValueError("Unknown job action")

    @app.put("/api/jobs/{identifier}/thresholds")
    def thresholds(identifier: str, body: dict):
        cfg = Thresholds.model_validate(body.get("thresholds", {})).model_dump()
        if not body.get("reason", "").strip() or not body.get("researcher", "").strip():
            raise ValueError("Retrospective threshold changes require a reason and researcher")
        with s.lock:
            job = s.get("jobs", identifier)
            history = job.get("threshold_history", []) + [{"before": job.get("thresholds"), "after": cfg, "reason": body["reason"], "researcher": body["researcher"], "retrospective": True, "created_at": now()}]
            return s.patch("jobs", identifier, thresholds=cfg, threshold_history=history)

    @app.get("/api/outputs")
    def outputs(job_id: str | None = None):
        return [o for o in s.all("outputs") if not job_id or o["job_id"] == job_id]

    @app.get("/api/evaluation-queue")
    def evaluation_queue(job_id: str | None = None, blind: bool = True, order_seed: int = 42):
        import random
        rows = [o for o in s.all("outputs") if (not job_id or o["job_id"] == job_id) and o.get("question") and o["status"] in ["completed", "invalid"]]
        random.Random(order_seed).shuffle(rows)
        if blind:
            return [{k: o[k] for k in ["id", "version", "inputs", "question", "status"]} for o in rows]
        return rows

    @app.get("/api/labels")
    def labels(output_id: str | None = None):
        records = sorted(s.all('labels'), key=lambda l:l['created_at'])
        active = {}
        for label in records:
            key = (label['output_id'],label['output_version'],label['source'],label['reviewer'] if label['source'] == 'human' else '',label['role'] if label['source'] == 'human' else '')
            active[key] = label['id']
        return [{**l, 'is_active':l['id'] in active.values()} for l in records if not output_id or l['output_id'] == output_id]

    @app.post("/api/labels")
    def label(body: dict):
        o = s.get("outputs", body["output_id"])
        if body.get("output_version") != o["version"]:
            raise ValueError("Output version mismatch; refresh before labeling")
        if not o.get("question") or o["status"] not in ["completed", "invalid"]:
            raise ValueError("Only finalized outputs with a question may be reviewed")
        reviewer, role = body.get("reviewer", "").strip(), body.get("role", "primary")
        if not reviewer or role not in ["primary", "secondary", "adjudication"]:
            raise ValueError("Reviewer and primary/secondary/adjudication role required")
        judgment = Judgment.model_validate(body.get("judgment", {})).model_dump()
        if any(c["value"] is not None and (not c["explanation"].strip() or not c["context"].strip()) for c in judgment.values()):
            raise ValueError("Assessed criteria require a short explanation and relevant context")
        with s.connect():
            existing = [l for l in latest_labels(s.all("labels")) if l["output_id"] == o["id"] and l["output_version"] == o["version"] and l["source"] == "human"]
            if role == "primary" and any(l["role"] == "primary" and l["reviewer"] != reviewer for l in existing):
                raise ValueError("A primary reviewer already exists; select secondary or adjudication")
            if role == "secondary" and any(l["role"] == "primary" and l["reviewer"] == reviewer for l in existing):
                raise ValueError("A second review requires a different reviewer identifier")
            refs = body.get("reference_label_ids", [])
            if role == "adjudication":
                selected = [l for l in existing if l["id"] in refs and l["role"] != "adjudication"]
                if len({l["reviewer"] for l in selected}) < 2:
                    raise ValueError("Adjudication requires labels from at least two reviewers on this exact output version")
            return s.put("labels", {"id": uid("label"), "output_id": o["id"], "output_version": o["version"], "job_id": o["job_id"],
                "source": "human", "reviewer": reviewer, "role": role, "judgment": judgment, "reference_label_ids": refs, "created_at": now()}, insert=True)

    def job_report(identifier):
        job = s.get("jobs", identifier)
        if job["kind"] != "experiment":
            raise ValueError("Only experiments have question-quality metrics")
        if job.get("bank_snapshot_id") and not job.get("bank_snapshot"):
            job = {**job, "bank_snapshot": research.bank_snapshot_identity(s.get("snapshots", job["bank_snapshot_id"]))}
        return report([o for o in s.all("outputs") if o["job_id"] == identifier], s.all("labels"), job,
                      [a for a in s.all('assessment_attempts') if a.get('job_id') == identifier and not a.get('calibration')])

    @app.get("/api/jobs/{identifier}/metrics")
    def metrics(identifier: str):
        return job_report(identifier)

    def download(body, name):
        return Response(json.dumps(body, indent=2, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/export")
    def export_all():
        return download({"schema_version": "zelfregiekompas-export-v1", "exported_at": now(), "records": {k: s.all(k) for k in KINDS},
            "bank_snapshot_identities": {snap["id"]: research.bank_snapshot_identity(snap) for snap in s.all("snapshots") if snap["kind"] == "bank"},
            "metrics": {j["id"]: job_report(j["id"]) for j in s.all("jobs") if j["kind"] == "experiment"}}, "research-export.json")

    @app.get("/api/jobs/{identifier}/export")
    def export_job(identifier: str):
        job = jobs.describe(s.get("jobs", identifier))
        source = s.get('jobs',job['config']['source_job_id']) if job['kind'] == 'evaluation' else job
        targets = {(target['output_id'],target['output_version']) for target in job.get('targets',[])}
        return download({"schema_version": "zelfregiekompas-experiment-v1", "job": job,
            "source_experiment":source if job['kind'] == 'evaluation' else None,
            "source_outputs":[output for output in s.all('outputs') if (output['id'],output['version']) in targets],
            "dataset_snapshot": s.get("snapshots", source["dataset_snapshot_id"]) if source.get("dataset_snapshot_id") else None,
            "bank_snapshot": s.get("snapshots", source["bank_snapshot_id"]) if source.get("bank_snapshot_id") else None,
            "bank_snapshot_identity": research.bank_snapshot_identity(s.get("snapshots", source["bank_snapshot_id"])) if source.get("bank_snapshot_id") else None,
            "generated_questions": [s.get("questions", key) for key in job.get("results", [])] if job["kind"] == "question_generation" else [],
            "assessment_attempts": [a for a in s.all('assessment_attempts') if a.get('evaluation_job_id') == identifier or a.get('job_id') == identifier],
            "outputs": [o for o in s.all("outputs") if o["job_id"] == identifier], "labels": [label for label in labels() if label.get("job_id") == identifier or label.get('evaluation_job_id') == identifier],
            "influences": s.all("influences"), "metrics": job_report(identifier) if job["kind"] == "experiment" else None}, f"{identifier}.json")

    @app.get("/api/datasets/{identifier}/export")
    def export_dataset(identifier: str, format: str = "json", include_provenance: bool = False):
        rows = research.dataset_export(identifier)
        if include_provenance:
            if format != "json":
                raise ValueError("Generation provenance archives use JSON; CSV/JSONL remain importable interchange rows")
            versions = research.latest("scenarios", identifier)
            return download({"schema_version": "zelfregiekompas-scenarios-provenance-v1", "scenarios": rows,
                             "generation_provenance": [{"scenario_version_id": row["id"], "scenario_id": row["scenario_id"],
                                                        "version": row["version"], "generation": row.get("generation")} for row in versions]}, identifier + "-with-prompts.json")
        headers = {"Content-Disposition": f'attachment; filename="{identifier}.{format}"'}
        if format == "json":
            return download(rows, identifier + ".json")
        if format == "jsonl":
            return Response("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), media_type="application/x-ndjson", headers=headers)
        if format == "csv":
            out = io.StringIO(newline="")
            writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else ["inputs"])
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
            return Response(out.getvalue(), media_type="text/csv", headers=headers)
        raise ValueError("Unknown export format")

    @app.get("/api/scenarios/{identifier}/export")
    def export_scenario(identifier: str):
        return download({"schema_version": "zelfregiekompas-scenario-version-v1", "scenario": s.get("scenarios", identifier)}, identifier + ".json")

    @app.get("/api/questions/{identifier}/export")
    def export_question(identifier: str):
        return download({"schema_version": "zelfregiekompas-question-version-v1", "question": s.get("questions", identifier)}, identifier + ".json")

    @app.get("/api/banks/{identifier}/export")
    def export_bank(identifier: str):
        bank = research.bank_summary(identifier)
        return download({"schema_version": "zelfregiekompas-bank-v1", "bank": bank,
                         "questions": [q for q in s.all("questions") if q["bank_id"] == identifier],
                         "reviews": [review for review in s.all("reviews") if review["version_id"] in {q["id"] for q in s.all("questions") if q["bank_id"] == identifier}],
                         "snapshots": [snap for snap in s.all("snapshots") if snap["kind"] == "bank" and research.bank_snapshot_identity(snap)["bank_id"] == identifier]}, identifier + ".json")

    @app.get("/api/delete/{kind}/{identifier}")
    def deletion_preview(kind: str, identifier: str):
        return research.deletion_plan(kind, identifier)

    @app.delete("/api/delete/{kind}/{identifier}")
    def delete(kind: str, identifier: str, cascade: bool = False):
        return research.delete(kind, identifier, cascade)

    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse({"detail": "Build frontend first: cd frontend; npm install; npm run build. API documentation at /docs"}, status_code=503)

    return app


app = create_app()
