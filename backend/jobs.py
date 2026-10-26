"""Durable single-worker queue. HTTP only enqueues; all inference runs here."""
import json
import copy
import platform
import random
import subprocess
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from pydantic import Field
from pydantic import ValidationError
from . import scenario_generation, bank_questions, model_policy, experiment_preflight
from .evaluation_jobs import EvaluationJobsMixin
from .schema import Strict, Judgment, Inputs, Question
from .store import now, uid
from .provider import ProviderError, Cancelled
from .methods import prepare, output_errors, EVALUATOR_SYSTEM, EVALUATOR_VERSION, PROMPT_VERSION, RULE_VERSION, RETRIEVAL_VERSION
from .schema import prompt_inputs


class Settings(Strict):
    temperature: float = Field(default=0.2, ge=0, le=2)
    seed: int | None = 42
    num_predict: int = Field(default=128, ge=16, le=65536)
    num_ctx: int = Field(default=4096, ge=512, le=131072)
    top_p: float = Field(default=0.9, gt=0, le=1)
    timeout_seconds: float = Field(default=120, ge=1, le=3600)
    think: bool | Literal["low", "medium", "high"] | None = None


class ScenarioSettings(Settings):
    # Dedicated defaults: neither the short inference test nor research methods use these.
    num_predict: int = Field(default=2048, ge=16, le=8192)
    think: bool | Literal["low", "medium", "high"] | None = False


class BankQuestionSettings(Settings):
    num_predict: int = Field(default=768, ge=16, le=8192)
    temperature: float = Field(default=0.4, ge=0, le=2)
    think: bool | Literal["low", "medium", "high"] | None = False


class BankQuestionTask(Strict):
    bank_id: str
    model: str = Field(min_length=1)
    count: int = Field(default=5, ge=1, le=1000, strict=True)
    themes: list[Literal["progress", "learning strategy", "understanding", "time management", "obstacles", "clarification", "next steps", "neutral fallback"]] = Field(default_factory=lambda: list(bank_questions.THEMES), min_length=1)
    instructions: str = Field(default="", max_length=2000)
    settings: BankQuestionSettings = Field(default_factory=BankQuestionSettings)


class Thresholds(Strict):
    min_both_quality: float | None = Field(default=None, ge=0, le=1)
    max_generation_seconds: float | None = Field(default=None, gt=0)
    max_memory_mb: float | None = Field(default=None, gt=0)


class Experiment(Strict):
    name: str = Field(min_length=1)
    dataset_snapshot_id: str
    split: Literal["development", "validation", "test"] = "development"
    bank_snapshot_id: str | None = None
    methods: list[Literal["fixed", "rag", "constrained"]] = Field(min_length=1)
    models: list[str] = Field(default_factory=list)
    repetitions: int = Field(default=1, ge=1, le=100)
    settings: Settings = Field(default_factory=Settings)
    evaluation_settings: Settings = Field(default_factory=lambda: Settings(temperature=0, num_predict=512))
    ai_control: bool = False
    evaluator_model: str | None = None
    top_k: int = Field(default=3, ge=1, le=20)
    order_seed: int = 42
    thresholds: Thresholds = Field(default_factory=Thresholds)
    hardware_notes: str = ""
    mode: Literal['short', 'thinking'] = 'short'
    evaluation_mode: Literal['short', 'thinking'] = 'short'
    preflight_id: str | None = None


def code_version():
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=2).decode().strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], stderr=subprocess.DEVNULL, timeout=2).strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "note": "No git commit available; source_hash identifies implementation"}


class Jobs(EvaluationJobsMixin):
    def __init__(self, store, research, provider):
        self.s, self.research, self.provider = store, research, provider
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-worker")
        self.shutdown_event = threading.Event()
        self.scheduled = set()
        self.schedule_lock = threading.RLock()
        self.futures = {}

    def recover(self):
        for job in self.s.all("jobs"):
            if job["status"] in ["queued", "running", "cancelling"]:
                self.s.patch("jobs", job["id"], status="interrupted", interruption="Process stopped; partial records retained. Resume explicitly.", ended_at=now())
        for o in self.s.all("outputs"):
            if o["status"] in ["running", "evaluating"]:
                self.s.patch("outputs", o["id"], status="interrupted")

    def close(self):
        self.shutdown_event.set()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def cancelled(self, identifier):
        return self.shutdown_event.is_set() or self.s.get("jobs", identifier)["status"] in ["cancelling", "terminated"]

    def _pin_models(self, names):
        if not names:
            return {}
        available = {m["name"]: m for m in self.provider.models()}
        for name in names:
            if name not in available or not available[name].get("digest"):
                raise ValueError(f"Model {name} must be installed locally with a known digest before queueing")
        return {name: available[name] for name in names}

    def _pinned_settings(self, job, name, settings):
        pinned = job.get("model_metadata", {}).get(name)
        if not pinned:
            raise ProviderError("This job has no pinned model digest; create a new job to select the exact installed model")
        current = next((m for m in self.provider.models() if m["name"] == name), None)
        if not current or current.get("digest") != pinned.get("digest"):
            raise ProviderError(f"Model digest changed or model is unavailable: {name}; restore the original model or create a new job")
        return {**settings, "_model_digest": pinned["digest"], "_ollama_url": job.get("ollama_address", getattr(self.provider, "url", None))}

    def create_experiment(self, raw):
        cfg = Experiment.model_validate(raw).model_dump()
        cfg["methods"] = list(dict.fromkeys(cfg["methods"]))
        cfg["models"] = list(dict.fromkeys(cfg["models"]))
        ds = self.s.get("snapshots", cfg["dataset_snapshot_id"])
        if ds["kind"] != "dataset":
            raise ValueError("Select a dataset snapshot")
        scenarios = [r for r in ds["records"] if r["split"] == cfg["split"]]
        if not scenarios:
            raise ValueError("Dataset snapshot contains no scenarios in this split")
        bank_identity = None
        if any(m in cfg["methods"] for m in ["fixed", "rag"]):
            if not cfg["bank_snapshot_id"]:
                raise ValueError("Bank-dependent methods cannot run without a snapshot of approved questions")
            bank = self.s.get("snapshots", cfg["bank_snapshot_id"])
            bank_identity = self.research.bank_snapshot_identity(bank)
            experimental = bank_identity["review_mode"] == "experimental"
            if experimental and cfg["split"] != "development":
                raise ValueError("Experimental question-bank snapshots are restricted to development experiments")
            allowed_status = {"approved", "awaiting_review"} if experimental else {"approved"}
            if not bank["records"] or any(q["review_status"] not in allowed_status for q in bank["records"]):
                raise ValueError("Select an approved question-bank snapshot or a valid experimental development snapshot; rejected questions are excluded")
            for question in bank["records"]:
                bank_questions.validate({key: question[key] for key in Question.model_fields})
        else:
            cfg["bank_snapshot_id"] = None
        if any(m != "fixed" for m in cfg["methods"]) and not cfg["models"]:
            raise ValueError("Select an installed local model for generative methods")
        if cfg["ai_control"] and not cfg["evaluator_model"]:
            raise ValueError("AI control requires a separately selected local evaluator model")
        if not cfg["ai_control"]:
            cfg["evaluator_model"] = None
        conditions = [(m, model) for m in cfg["methods"] for model in ([None] if m == "fixed" else cfg["models"])]
        cases = [(scenario, method, model, rep) for scenario in scenarios for method, model in conditions for rep in range(cfg["repetitions"])]
        if len(cases) > 10000:
            raise ValueError("Maximum 10,000 cases per experiment")
        random.Random(cfg["order_seed"]).shuffle(cases)
        job = self._job("experiment", cfg)
        names = (cfg["models"] if any(m != "fixed" for m in cfg["methods"]) else []) + ([cfg["evaluator_model"]] if cfg["ai_control"] else [])
        job["model_metadata"] = self._pin_models(names)
        for name in cfg['models'] if any(m != 'fixed' for m in cfg['methods']) else []:
            model_policy.validate_research(job['model_metadata'][name], cfg['settings'], cfg['mode'])
        if cfg['ai_control']:
            model_policy.validate_research(job['model_metadata'][cfg['evaluator_model']], cfg['evaluation_settings'], cfg['evaluation_mode'])
        if len(cases) > 10 and names:
            if not cfg['preflight_id']:
                raise ValueError('Run the actual research preflight before queuing more than 10 model-using cases')
            preflight = self.s.get('preflights', cfg['preflight_id'])
            if not preflight['passed'] or preflight['identity'] != experiment_preflight.identity(cfg, job['model_metadata'],getattr(self.provider,'url',None)):
                raise ValueError('Preflight failed or its model/snapshot/settings differ. Run preflight again for this exact configuration.')
        job["ollama_address"] = getattr(self.provider, "url", None)
        job["bank_snapshot"] = bank_identity
        role_models = {"scenario generation/revision": {r["generation"]["model"] for r in scenarios if (r.get("generation") or {}).get("model")},
                       "bank question generation": {(q.get("generation") or {}).get("model") for q in bank["records"] if (q.get("generation") or {}).get("model")} if bank_identity else set(),
                       "question generation": set(cfg["models"]) if any(m != "fixed" for m in cfg["methods"]) else set(),
                       "AI evaluation": {cfg["evaluator_model"]} if cfg["ai_control"] else set()}
        shared = {model: [role for role, models in role_models.items() if model in models]
                  for model in set().union(*role_models.values()) if model and sum(model in models for models in role_models.values()) > 1}
        import hashlib
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        files = list((root / "backend").glob("*.py")) + list((root / "frontend" / "src").rglob("*"))
        files += [root / p for p in ["requirements.lock.txt", "frontend/package-lock.json", "frontend/index.html", "frontend/vite.config.ts", "frontend/tsconfig.json"]]
        source = b"".join(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes() for p in sorted(files) if p.is_file())
        job.update({"dataset_snapshot_id": cfg["dataset_snapshot_id"], "bank_snapshot_id": cfg["bank_snapshot_id"],
                    "code_version": code_version(), "source_hash": hashlib.sha256(source).hexdigest(),
                    "versions": {"prompt": PROMPT_VERSION, "rules": RULE_VERSION, "retrieval": RETRIEVAL_VERSION, "evaluator": EVALUATOR_VERSION},
                    "thresholds": cfg["thresholds"], "threshold_history": [], "total": len(cases),
                    "hardware": {"os": platform.platform(), "processor": platform.processor(), "machine": platform.machine(), "python": platform.python_version(), "notes": cfg["hardware_notes"], "memory": "Not measured"},
                    "role_models": {k: sorted(v) for k, v in role_models.items()},
                    "bias_flags": [f"{model} fills multiple roles ({', '.join(roles)}); correlated bias is possible" for model, roles in shared.items()]})
        with self.s.connect() as c:
            self.s.write(c, "jobs", job, insert=True)
            for scenario, method, model, rep in cases:
                identity = bank_identity if method in ["fixed", "rag"] else None
                condition_id = hashlib.sha256(json.dumps({"method": method, "model": model, "bank_snapshot": identity,
                    "settings": cfg["settings"], "mode":cfg['mode'], "versions":job['versions'],
                    "model_digest":job['model_metadata'].get(model,{}).get('digest'),
                    "dataset_snapshot_id": cfg["dataset_snapshot_id"]}, sort_keys=True).encode()).hexdigest()
                settings = {**cfg["settings"]}
                if settings["seed"] is not None:
                    settings["seed"] += rep
                self.s.write(c, "outputs", {"id": uid("out"), "version": 1, "job_id": job["id"], "scenario_version_id": scenario["id"],
                    "scenario_id": scenario["scenario_id"], "family_id": scenario["family_id"], "inputs": prompt_inputs(scenario["inputs"]),
                    "tags": scenario["tags"], "method": method, "model": model, "repetition": rep + 1,
                    "bank_snapshot": identity, "condition_id": condition_id,
                    "settings": settings, "status": "pending", "question": None, "created_at": now(), "attempt_history": []}, insert=True)
        self.schedule(job["id"])
        return job

    def _job(self, kind, cfg):
        return {"id": uid("job"), "kind": kind, "name": cfg.get("name", kind.title()), "config": cfg,
                "status": "queued", "created_at": now(), "done": 0, "total": cfg.get("count", 1), "active_seconds": 0, "results": []}

    def question_task(self, raw):
        config = BankQuestionTask.model_validate(raw).model_dump()
        bank = self.s.get("banks", config["bank_id"])
        job = self._job("question_generation", {**config, "name": "Generate questions — " + bank["name"]})
        job.update(bank_id=bank["id"], bank_name=bank["name"], model_metadata=self._pin_models([config["model"]]),
                   ollama_address=getattr(self.provider, "url", None), question_attempts=[],
                   question_items={}, requested_count=config["count"], saved_count=0, failed_count=0, pending_count=config["count"],
                   seed_policy="base_seed + zero_based_item * max_attempts + zero_based_attempt; null remains unset")
        model_policy.validate_control(job['model_metadata'][config['model']], config['settings'].get('think'))
        self.s.put("jobs", job, insert=True)
        self.schedule(job["id"])
        return job

    def scenario_task(self, kind, cfg):
        if kind not in ["revision", "generation"]:
            raise ValueError("Unknown task")
        if not cfg.get("model"):
            raise ValueError("Select a local model")
        cfg["settings"] = ScenarioSettings.model_validate(cfg.get("settings", {})).model_dump()
        if kind == "revision":
            p = self.s.get("scenarios", cfg["version_id"])
            if not cfg.get("feedback", "").strip():
                raise ValueError("Reviewer feedback is required")
            cfg["dataset_id"] = p["dataset_id"]
        else:
            self.s.get("datasets", cfg["dataset_id"])
            if type(cfg.get("count", 1)) is not int or not 1 <= cfg.get("count", 1) <= 100:
                raise ValueError("Generate between 1 and 100 scenarios per task")
            if cfg.get("split", "development") not in ["development", "validation", "test"]:
                raise ValueError("Invalid split")
            if any(k not in Inputs.model_fields for k in cfg.get("missing_fields", [])):
                raise ValueError("Unknown requested missing input field")
        job = self._job(kind, cfg)
        job["model_metadata"] = self._pin_models([cfg["model"]])
        model_policy.validate_control(job['model_metadata'][cfg['model']], cfg['settings'].get('think'))
        if kind == 'generation':
            cfg['generation_plan'] = scenario_generation.plan(cfg)
            job['config'] = cfg
        job["ollama_address"] = getattr(self.provider, "url", None)
        job["dataset_id"] = cfg["dataset_id"]
        self.s.put("jobs", job, insert=True)
        self.schedule(job["id"])
        return job

    def schedule(self, identifier):
        with self.schedule_lock:
            if identifier in self.scheduled:
                return
            if self.s.get("jobs", identifier)["status"] != "queued":
                return
            self.scheduled.add(identifier)
            self.futures[identifier] = self.pool.submit(self._run, identifier)

    def _remove_pending(self, identifier):
        future = self.futures.get(identifier)
        if future and future.cancel():
            self.futures.pop(identifier, None)
            self.scheduled.discard(identifier)

    def cancel(self, identifier):
        with self.schedule_lock:
            job = self.s.get("jobs", identifier)
            if job["status"] not in ["running", "queued"]:
                raise ValueError("Only queued or running jobs can be cancelled")
            if job["status"] == "queued":
                result = self.s.patch("jobs", identifier, status="cancelled", ended_at=now())
                self._remove_pending(identifier)
                return result
            return self.s.patch("jobs", identifier, status="cancelling")

    def terminate(self, identifier):
        with self.schedule_lock:
            job = self.s.get("jobs", identifier)
            if job["status"] == "terminated":
                return job
            if job["status"] not in ["queued", "failed", "cancelled", "interrupted"]:
                raise ValueError("Only queued, failed, cancelled or interrupted jobs can be terminated. Cancel a running job and wait for it to stop first.")
            result = self.s.patch("jobs", identifier, status="terminated", terminated_at=now(), ended_at=now(),
                                  status_before_termination=job["status"])
            self._remove_pending(identifier)
            return result

    def archive(self, identifier, archived=True):
        with self.schedule_lock:
            job = self.s.get("jobs", identifier)
            if job["status"] not in ["completed", "completed_with_errors", "failed", "cancelled", "interrupted", "terminated"]:
                raise ValueError("Only finished or stopped jobs can be archived")
            return self.s.patch("jobs", identifier, archived=archived, archived_at=now() if archived else None)

    def describe(self, job):
        if job["kind"] != "question_generation" or "saved_count" in job:
            return job
        # Read-only compatibility for jobs created before per-item accounting.
        saved = set()
        for position, identifier in enumerate(job.get("results", []), 1):
            generation = self.s.get("questions", identifier).get("generation") or {}
            saved.add(generation.get("sequence", position))
        invalid = {}
        for attempt in job.get("question_attempts", []):
            if attempt.get("status") == "invalid":
                sequence = attempt.get("sequence")
                invalid[sequence] = invalid.get(sequence, 0) + 1
        failed = sum(sequence not in saved and count >= bank_questions.MAX_ATTEMPTS for sequence, count in invalid.items())
        return {**job, "requested_count": job["total"], "saved_count": len(saved), "failed_count": failed,
                "pending_count": job["total"] - len(saved) - failed}

    def resume(self, identifier):
        with self.schedule_lock:
            if identifier in self.scheduled:
                raise ValueError("Job is still stopping; try again after it stops")
            job = self.s.get("jobs", identifier)
            if job["status"] not in ["interrupted", "cancelled", "failed", "completed_with_errors"]:
                raise ValueError("Only interrupted, cancelled or failed jobs can be resumed; terminated jobs cannot be resumed")
            if job['kind'] == 'experiment' and job.get('versions') and job['versions'] != {'prompt':PROMPT_VERSION,'rules':RULE_VERSION,'retrieval':RETRIEVAL_VERSION,'evaluator':EVALUATOR_VERSION}:
                raise ValueError('Research implementation versions changed. Preserve this historical run and create a new experiment configuration instead of mixing prompt/rule/retrieval/rubric versions on Resume.')
            if job['kind'] == 'evaluation' and job.get('rubric_version') != EVALUATOR_VERSION:
                raise ValueError('Evaluator rubric changed. Create a new evaluation job; historical assessment provenance will not be relabeled.')
            self.s.patch("jobs", identifier, status="queued", error=None, archived=False, archived_at=None)
            self.schedule(identifier)
            return self.s.get("jobs", identifier)

    def _run(self, identifier):
        started = time.perf_counter()
        claimed = False
        try:
            with self.schedule_lock:
                state = self.s.get("jobs", identifier)["status"]
                if state not in ["queued", "cancelling"]:
                    return  # A removed/cancelled queue entry must never start or change state.
                if self.cancelled(identifier):
                    self.s.patch("jobs", identifier, status="cancelled", ended_at=now())
                    return
                self.s.patch("jobs", identifier, status="running", started_at=now())
                claimed = True
            job = self.s.get("jobs", identifier)
            completion_status = "completed"
            if job["kind"] == "experiment":
                self._experiment(job)
            elif job["kind"] == "question_generation":
                completion_status = self._questions(job)
            elif job['kind'] == 'evaluation':
                completion_status = self._evaluation(job)
            else:
                self._scenarios(job)
            status = "interrupted" if self.shutdown_event.is_set() else "cancelled" if self.cancelled(identifier) else completion_status
            self.s.patch("jobs", identifier, status=status, ended_at=now())
        except Cancelled as e:
            self.s.patch("jobs", identifier, status="interrupted" if self.shutdown_event.is_set() else "cancelled", error=str(e), partial_text=e.partial, ended_at=now())
        except Exception as e:
            self.s.patch("jobs", identifier, status="failed", error=str(e), diagnostics=getattr(e, "diagnostics", {}), ended_at=now())
        finally:
            with self.schedule_lock:
                if claimed:
                    job = self.s.get("jobs", identifier)
                    if job['kind'] in ['generation','revision']:
                        self.s.patch('jobs', identifier, generation_summary=scenario_generation.summary([self.s.get('scenarios', key) for key in job.get('results',[])], job.get('scenario_attempts',[])))
                    self.s.patch("jobs", identifier, active_seconds=job.get("active_seconds", 0) + time.perf_counter() - started)
                self.scheduled.discard(identifier)
                self.futures.pop(identifier, None)

    def _experiment(self, job):
        cfg, identifier = job["config"], job["id"]
        bank = self.s.get("snapshots", cfg["bank_snapshot_id"])["records"] if cfg["bank_snapshot_id"] else []
        metadata = {}
        provider_error = None
        if any(m != "fixed" for m in cfg["methods"]) or cfg["ai_control"]:
            try:
                metadata = {m["name"]: m for m in self.provider.models()}
            except ProviderError as e:
                provider_error = e
        manual = {r["model"]: r for r in self.s.all("model_metadata")}
        old_sessions = job.get("model_sessions", [])
        if old_sessions and old_sessions[-1]["models"] != metadata:
            self.s.patch("jobs", identifier, model_change_warning="Provider model metadata changed after resume; compare model_sessions and digests before pooling results")
        self.s.patch("jobs", identifier, model_metadata=job.get("model_metadata", metadata), manual_model_metadata=job.get("manual_model_metadata", manual),
                     model_sessions=old_sessions + [{"started_at": now(), "models": metadata, "manual_model_metadata": manual}],
                     provider="Ollama local" if metadata else "No model used" if not provider_error else str(provider_error))
        for original in self.s.all("outputs"):
            if original["job_id"] != identifier or original["status"] in ["completed", "invalid", "empty", "empty_final_answer", "output_limit_before_answer", "truncated", "timeout", "failed"]:
                continue
            if self.cancelled(identifier):
                break
            output = original.copy()
            if output["status"] in ["cancelled", "interrupted"] and not output.get("question"):
                output["attempt_history"].append({k: output.get(k) for k in ["version", "status", "partial_text", "error", "attempt_started_at", "generation_seconds"]})
                output["version"] += 1
                output.update(error=None, partial_text="", attempt_started_at=None)
            output.update(status="running", attempt_started_at=output.get("attempt_started_at") or now())
            self.s.put("outputs", output)
            generation_start = time.perf_counter()
            try:
                if not output.get("question"):
                    trace = prepare(output["method"], output["inputs"], bank, cfg["top_k"], bank_review_mode=(job.get("bank_snapshot") or {}).get("review_mode", "approved"))
                    trace["bank_snapshot"] = output.get("bank_snapshot")
                    output["trace"] = trace
                    self.s.put("outputs", output)
                    if output["method"] == "fixed":
                        output["question"] = trace["question"]
                        output["generation_runtime"] = {"load_duration": None, "note": "No LLM used"}
                    else:
                        if provider_error:
                            raise provider_error
                        if output["model"] not in metadata:
                            raise ProviderError("Selected model is not installed locally or could not be verified local")
                        settings = self._pinned_settings(job, output["model"], output["settings"])
                        output["model_metadata"] = job["model_metadata"][output["model"]]
                        result = self.provider.generate(output["model"], trace["system_prompt"], trace["prompt"], settings, lambda: self.cancelled(identifier))
                        output["question"] = result["text"]
                        output["generation_runtime"] = result["runtime"]
                        output["effective_generation_settings"] = result.get("effective_settings")
                        output['actual_request'] = result.get('request')
                        output['generation_diagnostics'] = result.get('diagnostics', {})
                    output["generation_seconds"] = time.perf_counter() - generation_start
                    errors = output_errors(output["question"], output["inputs"].get("previous_question"))
                    output["validation_errors"] = errors
                    status = model_policy.classify(output['question'], output.get('generation_runtime',{}), errors)
                    output['status'] = 'evaluating' if status == 'completed' and cfg['ai_control'] else status
                    self.s.put("outputs", output)
                elif output.get('question') and output['status'] == 'running':
                    errors = output_errors(output['question'], output['inputs'].get('previous_question'))
                    status = model_policy.classify(output['question'], output.get('generation_runtime') or {}, errors)
                    output['status'] = 'evaluating' if status == 'completed' and cfg['ai_control'] else status
                if output["status"] in ["evaluating", "completed"]:
                    if cfg["ai_control"]:
                        self._evaluate(job, output, metadata, provider_error)
                    output["status"] = "completed"
            except Cancelled as e:
                output.update(status="interrupted" if self.shutdown_event.is_set() else "cancelled", partial_text=e.partial, error=str(e))
                output.setdefault('generation_seconds',time.perf_counter() - generation_start)
                output['ended_at'] = now()
                self.s.put('outputs',output)
                raise
            except (ProviderError, ValueError) as e:
                output.update(status="timeout" if getattr(e, "kind", "") == "timeout" else "failed", partial_text=getattr(e, "partial", ""), error=str(e))
                output['failure_kind'] = getattr(e,'kind','validation_error')
                output["generation_diagnostics"] = getattr(e, "diagnostics", {})
                output.setdefault("effective_generation_settings", output["generation_diagnostics"].get("effective_settings"))
            output.setdefault("generation_seconds", time.perf_counter() - generation_start)
            output["ended_at"] = now()
            self.s.put("outputs", output)
            done = sum(o["job_id"] == identifier and o["status"] in ["completed", "invalid", "empty", "empty_final_answer", "output_limit_before_answer", "truncated", "timeout", "failed"] for o in self.s.all("outputs"))
            self.s.patch("jobs", identifier, done=done)

    def _evaluate(self, job, output, metadata, provider_error):
        cfg = job['config']
        started = time.perf_counter()
        try:
            if provider_error:
                raise provider_error
            settings = self._pinned_settings(job, cfg['evaluator_model'], cfg['evaluation_settings'])
            committed = next((label for label in self.s.all('labels') if label.get('evaluation_job_id') == job['id'] and label['output_id'] == output['id'] and label['output_version'] == output['version']),None)
            attempt = self.s.get('assessment_attempts',committed['assessment_id']) if committed else self._assess(job, output, cfg['evaluator_model'], settings)
            output['evaluation_trace'] = attempt
            output['evaluation_raw'] = attempt.get('raw_response')
            output['evaluation_runtime'] = attempt.get('runtime')
            output['effective_evaluation_settings'] = attempt.get('effective_settings')
            output['evaluation_error'] = attempt.get('error')
            output['evaluation_diagnostics'] = attempt.get('diagnostics', {})
        except ProviderError as exc:
            output['evaluation_error'] = str(exc)
            output['evaluation_diagnostics'] = exc.diagnostics
            if isinstance(exc, Cancelled):
                raise
        finally:
            output['evaluation_seconds'] = time.perf_counter() - started

    def _questions(self, job):
        config = job["config"]
        self.s.get("banks", config["bank_id"])
        settings = self._pinned_settings(job, config["model"], config["settings"])
        schema = bank_questions.output_schema()
        results = list(job.get("results", []))
        attempts = list(job.get("question_attempts", []))
        items = copy.deepcopy(job.get("question_items", {}))
        # Legacy jobs saved sequentially until the first failure. Prefer their
        # captured sequence when available; never restart a successful slot.
        for position, identifier in enumerate(results, 1):
            generation = self.s.get("questions", identifier).get("generation") or {}
            sequence = generation.get("sequence", position)
            items[str(sequence)] = {"status": "saved", "question_version_id": identifier}
        remaining = [sequence for sequence in range(1, job["total"] + 1) if items.get(str(sequence), {}).get("status") != "saved"]
        run_number = job.get("generation_run", 0) + 1
        def checkpoint(**extra):
            failed = sum(item["status"] == "failed" for item in items.values())
            self.s.patch("jobs", job["id"], results=results, done=len(results), requested_count=job["total"],
                         saved_count=len(results), failed_count=failed, pending_count=job["total"] - len(results) - failed,
                         question_items=items, question_attempts=attempts, **extra)
        checkpoint(generation_run=run_number, run_attempt_limit=len(remaining) * bank_questions.MAX_ATTEMPTS,
                   no_progress_limit=bank_questions.MAX_CONSECUTIVE_FAILURES)
        consecutive_failures = 0
        for sequence in remaining:
            index = sequence - 1
            errors = None
            previous = sum(a.get("sequence") == sequence for a in attempts)
            for attempt in range(1, bank_questions.MAX_ATTEMPTS + 1):
                if self.cancelled(job["id"]):
                    raise Cancelled()
                existing = self.research.latest("questions", bank_id=config["bank_id"])
                attempt_settings = dict(settings)
                if attempt_settings.get("seed") is not None:
                    attempt_settings["seed"] += index * bank_questions.MAX_ATTEMPTS + attempt - 1
                prompt = bank_questions.prompt(config, sequence, existing, errors, retry=previous + attempt - 1)
                trace = {"id": uid("attempt"), "job_id": job["id"], "sequence": index + 1, "attempt": attempt,
                         "generation_run": run_number, "item_attempt": previous + attempt,
                         "theme": bank_questions.assignment(config, sequence)[0],
                         "focus": bank_questions.assignment(config, sequence, previous + attempt - 1)[1],
                         "bank_id": config["bank_id"], "model": config["model"], "model_metadata": job["model_metadata"][config["model"]],
                         "settings": {k: v for k, v in attempt_settings.items() if not k.startswith("_")}, "system_prompt": bank_questions.SYSTEM, "prompt": prompt,
                         "response_schema": schema, "version": bank_questions.VERSION, "role": "bank question generation",
                         "created_at": now(), "status": "running"}
                attempts.append(trace)
                self.s.patch("jobs", job["id"], current_trace=trace, question_attempts=attempts)
                try:
                    result = self.provider.generate(config["model"], bank_questions.SYSTEM, prompt, attempt_settings,
                        lambda: self.cancelled(job["id"]), response_schema=schema)
                    trace.update(raw_response=result["text"], request=result.get("request"), runtime=result["runtime"],
                                 effective_settings=result.get("effective_settings"), latency_seconds=result["latency_seconds"], diagnostics=result.get("diagnostics", {}))
                    self.s.patch("jobs", job["id"], current_trace=trace, question_attempts=attempts)
                    if not result["text"].strip():
                        raise ValueError("No answer text was returned; thinking-only output is not a question. " +
                                         f"done_reason={result['runtime'].get('done_reason', 'unknown')}, thinking_characters={trace['diagnostics'].get('thinking_characters', 'unknown')}")
                    question = bank_questions.parse(result["text"])
                    trace.update(status="valid", ended_at=now())
                    provenance = {**trace, "saved_attempt_id": trace["id"], "response_id": trace["id"], "response_item_index": 0,
                                  "attempts": copy.deepcopy([a for a in attempts if a.get("sequence") == index + 1])}
                    with self.s.connect():
                        saved = self.research.question(question, bank_id=config["bank_id"], generation=provenance)
                        results.append(saved["id"])
                        items[str(sequence)] = {"status": "saved", "question_version_id": saved["id"], "attempt_id": trace["id"]}
                        checkpoint(current_trace=None)
                    consecutive_failures = 0
                    break
                except (ValueError, ProviderError) as exc:
                    if isinstance(exc, ValidationError):
                        errors = [{"path": ".".join(map(str, error["loc"])), "type": error["type"], "message": error["msg"]}
                                  for error in exc.errors(include_input=False, include_url=False)][:20]
                    else:
                        errors = [{"path": "question", "type": getattr(exc, "kind", "invalid_question"), "message": str(exc)[:1500]}]
                    if isinstance(exc, bank_questions.ExactDuplicate):
                        errors = [{"path": "text", "type": "exact_duplicate", "message": str(exc), "repeated_text": exc.text,
                                   "instruction": "Choose a substantively different reflection decision, evidence or action within the assigned theme. Do not make a cosmetic paraphrase or change only conditions/tags/priority."}]
                    if getattr(exc, "request", None):
                        trace["request"] = exc.request
                    trace.update(status="cancelled" if isinstance(exc, Cancelled) else "invalid", errors=errors, ended_at=now(),
                                 partial_text=getattr(exc, "partial", ""))
                    self.s.patch("jobs", job["id"], current_trace=trace, question_attempts=attempts)
                    if isinstance(exc, ProviderError) and exc.kind not in ["malformed_response", "empty_output"]:
                        raise
                    if attempt == bank_questions.MAX_ATTEMPTS:
                        items[str(sequence)] = {"status": "failed", "attempt_id": trace["id"], "errors": errors, "generation_run": run_number}
                        checkpoint(current_trace=None)
                        consecutive_failures += 1
            if consecutive_failures >= bank_questions.MAX_CONSECUTIVE_FAILURES:
                raise ProviderError(f"Stopped after {consecutive_failures} consecutive items exhausted their retries without a saved question. "
                                    f"Requested {job['total']}; saved {len(results)}; failed {sum(item['status'] == 'failed' for item in items.values())}. "
                                    "All drafts and attempts retained. Resume remaining work explicitly to retry unsaved items. " +
                                    "; ".join(e["message"] for e in errors), "bank_no_progress")
        failed = sum(item["status"] == "failed" for item in items.values())
        summary = (f"Incomplete generation: requested {job['total']}; saved {len(results)}; failed {failed}. "
                   "Resume remaining work to retry failed items. " + "; ".join(e["message"] for item in items.values() if item["status"] == "failed" for e in item["errors"]))
        checkpoint(error=summary[:2000] if failed else None)
        return "completed_with_errors" if failed else "completed"

    def _scenarios(self, job):
        cfg = job["config"]
        available = {m["name"]: m for m in self.provider.models()}
        if cfg["model"] not in available:
            raise ProviderError("Select an installed verified local model")
        settings = self._pinned_settings(job, cfg["model"], cfg["settings"])
        missing = cfg.get("missing_fields", []) if job["kind"] == "generation" else []
        schema = scenario_generation.output_schema(missing)
        results = job.get("results", [])
        attempts = job.get("scenario_attempts", [])
        # Retain the old trace when explicitly resuming a pre-fix job.
        legacy = job.get("current_trace")
        if legacy and legacy.get("version") != scenario_generation.VERSION:
            attempts.append({**legacy, "status": "legacy_failed", "error": job.get("error"), "sequence": len(results) + 1})
            self.s.patch("jobs", job["id"], scenario_attempts=attempts)
        for index in range(len(results), job["total"]):
            if self.cancelled(job["id"]):
                break
            parent = self.s.get("scenarios", cfg["version_id"]) if job["kind"] == "revision" else None
            plan_item = scenario_generation.assigned(cfg, index + 1) if job['kind'] == 'generation' else None
            missing = list(cfg.get('missing_fields', [])) if job['kind'] == 'generation' else []
            if plan_item and plan_item['variation'] == 'missing information' and not missing:
                missing = ['progress']
            schema = scenario_generation.output_schema(missing, plan_item)
            errors = None
            for attempt in range(1, scenario_generation.MAX_ATTEMPTS + 1):
                if self.cancelled(job["id"]):
                    raise Cancelled()
                existing = self.research.latest('scenarios', dataset_id=cfg['dataset_id'])
                prompt = scenario_generation.prompt_for(job["kind"], cfg, index + 1, parent, errors, existing)
                trace = {"id": uid("attempt"), "model": cfg["model"], "model_metadata": job["model_metadata"][cfg["model"]], "settings": cfg["settings"],
                         "system_prompt": scenario_generation.SYSTEM, "prompt": prompt, "response_schema": schema,
                         "version": scenario_generation.VERSION, "role": job["kind"], "job_id": job["id"], "sequence": index + 1,
                         "attempt": attempt, "created_at": now(), "status": "running"}
                if plan_item:
                    trace['plan_item'] = plan_item
                attempts.append(trace)
                self.s.patch("jobs", job["id"], current_trace=trace, scenario_attempts=attempts)
                try:
                    result = self.provider.generate(cfg["model"], scenario_generation.SYSTEM, prompt, settings,
                                                    lambda: self.cancelled(job["id"]), response_schema=schema)
                    trace.update(raw_response=result["text"], runtime=result["runtime"], latency_seconds=result["latency_seconds"], effective_settings=result.get("effective_settings"), request=result.get("request"))
                    # Save the exact model text before parsing; never select a request/context object.
                    self.s.patch("jobs", job["id"], current_trace=trace, scenario_attempts=attempts)
                    inputs = scenario_generation.parse_response(result["text"], missing)
                    trace["scope_check"] = scenario_generation.check_scope(inputs)
                    trace['semantic_warnings'] = []
                    if job['kind'] == 'generation':
                        from .research import fingerprint
                        from difflib import SequenceMatcher
                        if any(fingerprint(row['inputs']) == fingerprint(inputs) for row in self.s.all('scenarios') if row['dataset_id'] == cfg['dataset_id']):
                            raise ValueError('Exact duplicate Inputs already exists in this destination dataset. Generate a coherent different activity within the assigned topic/variation.')
                        text = (inputs.get('learning_goal') or '') + ' ' + (inputs.get('planned_activity') or '')
                        if any(SequenceMatcher(None, text.casefold(), ((row['inputs'].get('learning_goal') or '') + ' ' + (row['inputs'].get('planned_activity') or '')).casefold()).ratio() >= .9 for row in existing):
                            trace['semantic_warnings'].append('Likely near-duplicate goal/activity in this dataset; inspect before approval')
                    trace['semantic_warnings'].extend(scenario_generation.validate_plan(inputs, plan_item, missing))
                except (ValueError, ProviderError) as exc:
                    if isinstance(exc, ValidationError):
                        errors = [{"path": ".".join(map(str, e["loc"])), "type": e["type"], "message": e["msg"]} for e in exc.errors(include_input=False, include_url=False)]
                    else:
                        errors = [{"path": "response", "type": getattr(exc, "kind", "invalid_json"), "message": str(exc)[:1000]}]
                    if getattr(exc, "request", None) is not None:
                        trace["request"] = exc.request
                    errors = errors[:20]
                    trace.update(status="cancelled" if isinstance(exc, Cancelled) else "invalid", errors=errors,
                                 diagnostics=getattr(exc, "diagnostics", {}), partial_text=getattr(exc, "partial", ""), ended_at=now())
                    self.s.patch("jobs", job["id"], current_trace=trace, scenario_attempts=attempts)
                    retryable = not isinstance(exc, ProviderError) or exc.kind in ["malformed_response", "empty_output"]
                    if not retryable:
                        raise
                    if attempt == scenario_generation.MAX_ATTEMPTS:
                        paths = "; ".join(f"{e['path']}: {e['message']}" for e in errors)
                        raise ProviderError(f"Scenario {index + 1}/{job['total']} rejected after {attempt} attempts. {len(results)} draft(s) retained. {paths}",
                                            "invalid_scenario_output", diagnostics={"sequence": index + 1, "attempts": attempt, "validation_errors": errors}) from exc
                    continue
                trace.update(status="valid", ended_at=now())
                break
            provenance = {**trace, "saved_attempt_id": trace["id"],
                          "attempts": copy.deepcopy([a for a in attempts if a.get("sequence") == index + 1])}
            with self.s.connect():
                if job["kind"] == "revision":
                    scenario = self.research.revise(cfg["version_id"], {"inputs": inputs}, provenance)
                else:
                    scenario = self.research.scenario(cfg["dataset_id"], {"inputs": inputs, "tags": [plan_item['variation'] if plan_item else cfg.get("category", "normal")],
                        "family_id":plan_item.get('family_id') if plan_item else None,
                        "research_labels": {'assigned_topic': plan_item['topic'], 'assigned_variation': plan_item['variation'], 'intentional_contradiction': plan_item['variation'] == 'contradictory information'} if plan_item else {},
                        "split": cfg.get("split", "development"), "synthetic_origin": "local AI-generated; unvalidated"}, generation=provenance)
                results.append(scenario["id"])
                self.s.patch("jobs", job["id"], results=results, done=len(results), current_trace=None, scenario_attempts=attempts)
                self.s.patch('jobs', job['id'], generation_summary=scenario_generation.summary([self.s.get('scenarios', identifier) for identifier in results], attempts))
