"""Research records, versioned human decisions, imports and immutable snapshots."""
import csv
import hashlib
import io
import json
from collections import Counter
from difflib import SequenceMatcher
from pydantic import ValidationError
from .schema import Inputs, ScenarioRow, Question, warnings, prompt_inputs
from .store import now, uid
from .methods import get_path
from . import bank_questions, scenario_generation


def fingerprint(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def missing_pattern(inputs):
    paths = set()
    def visit(value, prefix):
        if value is None or value == "" or value == []:
            paths.add(prefix)
        elif isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, list):
            for child in value:
                visit(child, prefix + "[]")
    visit(inputs, "")
    return sorted(paths)


class Research:
    def __init__(self, store):
        self.s = store
        self._migrate_banks()

    def _migrate_banks(self):
        # Add collection identity to old question versions only. Frozen snapshots,
        # IDs, reviews, hashes and historical experiment references are untouched.
        with self.s.connect() as c:
            if not any(b["id"] == bank_questions.DEFAULT_BANK_ID for b in self.s.all("banks")):
                self.s.write(c, "banks", {"id": bank_questions.DEFAULT_BANK_ID, "name": "Default question bank", "description": "Existing question collection; preserved during multi-bank migration", "created_at": now(), "legacy_default": True}, insert=True)
            for question in self.s.all("questions"):
                if not question.get("bank_id"):
                    self.s.write(c, "questions", {**question, "bank_id": bank_questions.DEFAULT_BANK_ID})

    def bank(self, name, description=""):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Question bank name is required")
        if not isinstance(description, str):
            raise ValueError("Bank description must be text")
        return self.s.put("banks", {"id": uid("bank"), "name": name.strip(), "description": description, "created_at": now()}, insert=True)

    def dataset(self, name, description=""):
        if not name.strip():
            raise ValueError("Dataset name is required")
        return self.s.put("datasets", {"id": uid("ds"), "name": name, "description": description, "created_at": now()}, insert=True)

    def latest(self, kind, dataset_id=None, bank_id=None):
        rows = self.s.all(kind)
        if dataset_id:
            rows = [r for r in rows if r.get("dataset_id") == dataset_id]
        if bank_id:
            rows = [r for r in rows if r.get("bank_id") == bank_id]
        key = "scenario_id" if kind == "scenarios" else "question_id"
        latest = {}
        for r in rows:
            if r[key] not in latest or r["version"] > latest[r[key]]["version"]:
                latest[r[key]] = r
        return list(latest.values())

    def scenario(self, dataset_id, row, parent=None, generation=None):
        self.s.get("datasets", dataset_id)
        parsed = ScenarioRow.model_validate(row).model_dump()
        with self.s.lock:
            existing = self.s.all("scenarios")
            if parent:
                p = self.s.get("scenarios", parent)
                if p["dataset_id"] != dataset_id:
                    raise ValueError("Parent belongs to another dataset")
                if parsed["split"] != p["split"]:
                    raise ValueError("Revisions must retain their family split")
                sid, family = p["scenario_id"], p["family_id"]
                versions = [r["version"] for r in existing if r["scenario_id"] == sid]
                version = max(versions) + 1
            else:
                sid = parsed["scenario_id"] or uid("scenario")
                if any(r["scenario_id"] == sid for r in existing):
                    raise ValueError("Scenario ID already exists. Use the revision endpoint for material edits.")
                family = parsed["family_id"] or sid
                version = 1
            if any(r["family_id"] == family and r["split"] != parsed["split"] for r in existing):
                raise ValueError("Scenario family already belongs to a different split")
            value = {**parsed, "id": uid("sv"), "dataset_id": dataset_id, "scenario_id": sid,
                     "family_id": family, "version": version, "parent_id": parent, "generation": generation,
                     "review_status": "awaiting_review", "created_at": now(), "warnings": warnings(parsed["inputs"]) + scenario_generation.semantic_warnings(parsed['inputs'])}
            return self.s.put("scenarios", value, insert=True)

    def revise(self, identifier, changes, generation=None):
        p = self.s.get("scenarios", identifier)
        row = {k: p[k] for k in ScenarioRow.model_fields}
        row.update(changes)
        if row.get("scenario_id") != p["scenario_id"] or row.get("family_id") != p["family_id"]:
            raise ValueError("Scenario and family identities cannot change in a revision")
        return self.scenario(p["dataset_id"], row, identifier, generation)

    def question(self, raw, parent=None, bank_id=None, generation=None):
        raw = dict(raw)
        requested_bank = raw.pop("bank_id", None)
        if requested_bank and bank_id and requested_bank != bank_id:
            raise ValueError("Conflicting question bank IDs")
        bank_id = bank_id or requested_bank
        q = bank_questions.validate(raw)
        with self.s.lock:
            p = self.s.get("questions", parent) if parent else None
            if p and bank_id and bank_id != p["bank_id"]:
                raise ValueError("Question revisions must remain in their original bank")
            bank_id = p["bank_id"] if p else bank_id or bank_questions.DEFAULT_BANK_ID
            self.s.get("banks", bank_id)
            qid = p["question_id"] if p else uid("question")
            existing = [row for row in self.latest("questions", bank_id=bank_id) if row["question_id"] != qid]
            if any(bank_questions.normalize(row["text"]) == bank_questions.normalize(q["text"]) for row in existing):
                raise bank_questions.ExactDuplicate(q["text"])
            versions = [r["version"] for r in self.s.all("questions") if r["question_id"] == qid]
            return self.s.put("questions", {**q, "id": uid("qv"), "question_id": qid, "bank_id": bank_id,
                "generation": generation, "warnings": bank_questions.warnings(q, existing),
                "version": max(versions, default=0) + 1, "parent_id": parent, "review_status": "awaiting_review", "created_at": now()}, insert=True)

    def review(self, kind, identifier, decision, reviewer, feedback):
        if kind not in ["scenarios", "questions"] or decision not in ["approved", "rejected", "awaiting_review"]:
            raise ValueError("Invalid review type or decision")
        if not reviewer.strip():
            raise ValueError("Reviewer identifier is required")
        with self.s.connect() as c:
            raw = c.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, identifier)).fetchone()
            if not raw:
                raise ValueError("Version not found")
            row = json.loads(raw[0])
            event = {"id": uid("review"), "version_id": identifier, "kind": kind, "decision": decision,
                     "reviewer": reviewer, "feedback": feedback, "created_at": now()}
            self.s.write(c, "reviews", event, insert=True)
            row["review_status"] = decision
            self.s.write(c, kind, row)
        return event

    def snapshot(self, kind, name, dataset_id=None, bank_id=None, review_mode="approved"):
        if kind not in ["dataset", "bank"] or not name.strip():
            raise ValueError("Snapshot kind and name are required")
        if kind == "dataset":
            if review_mode != "approved":
                raise ValueError("Dataset snapshots always require approval")
            if not dataset_id:
                raise ValueError("Dataset is required")
            self.s.get("datasets", dataset_id)
        if review_mode not in ["approved", "experimental"]:
            raise ValueError("Bank review mode must be approved or experimental")
        bank = self.s.get("banks", bank_id or bank_questions.DEFAULT_BANK_ID) if kind == "bank" else None
        if kind == "bank":
            dataset_id = None
        with self.s.lock:
            rows = self.latest("scenarios" if kind == "dataset" else "questions", dataset_id if kind == "dataset" else None, bank["id"] if bank else None)
            approved = [r for r in rows if r["review_status"] == "approved" or (kind == "bank" and review_mode == "experimental" and r["review_status"] == "awaiting_review")]
            if not approved:
                raise ValueError("No approved latest versions. Human approval is required before freezing a regular snapshot; experimental snapshots may include unreviewed versions, never rejected ones.")
            if bank:
                for row in approved:
                    bank_questions.validate({key: row[key] for key in Question.model_fields})
            body = {"id": uid("snap"), "kind": kind, "name": name, "dataset_id": dataset_id,
                    "records": approved, "excluded_unapproved": len(rows) - len(approved), "created_at": now(),
                    "frozen_test": any(r.get("split") == "test" for r in approved), "content_hash": fingerprint(approved)}
            if bank:
                body.update(bank_id=bank["id"], bank_name=bank["name"], bank_configuration=bank, review_mode=review_mode,
                            usable_fallback=any(bank_questions.fallback(q) for q in approved),
                            review_counts=dict(Counter(q["review_status"] for q in approved)))
                body["content_hash"] = fingerprint({"bank_id": bank["id"], "configuration": bank, "review_mode": review_mode, "records": approved})
            return self.s.put("snapshots", body, insert=True)

    def bank_snapshot_identity(self, snapshot):
        if snapshot["kind"] != "bank":
            raise ValueError("Select a question-bank snapshot")
        return {"snapshot_id": snapshot["id"], "bank_id": snapshot.get("bank_id", bank_questions.DEFAULT_BANK_ID),
                "bank_name": snapshot.get("bank_name", "Default question bank (legacy snapshot)"),
                "review_mode": snapshot.get("review_mode", "approved"), "content_hash": snapshot["content_hash"]}

    def bank_summary(self, identifier):
        bank = self.s.get("banks", identifier)
        rows = self.latest("questions", bank_id=identifier)
        return {**bank, "question_count": len(rows), "review_counts": dict(Counter(q["review_status"] for q in rows)),
                "approved_fallback": any(q["review_status"] == "approved" and bank_questions.fallback(q) for q in rows),
                "experimental_fallback": any(q["review_status"] != "rejected" and bank_questions.fallback(q) for q in rows)}

    def coverage(self, dataset_id):
        rows = self.latest("scenarios", dataset_id)
        return {"total": len(rows), "categories": dict(Counter(t for r in rows for t in r["tags"])),
                "missing_patterns": dict(Counter(", ".join(missing_pattern(r["inputs"])) or "complete" for r in rows)),
                "review_status": dict(Counter(r["review_status"] for r in rows)), "splits": dict(Counter(r["split"] for r in rows))}

    def preview(self, dataset_id, fmt, text, mapping=None):
        self.s.get("datasets", dataset_id)
        if len(text) > 10_000_000:
            raise ValueError("Import limit is 10 MB; split larger research files")
        if fmt == "json":
            rows = json.loads(text)
            if isinstance(rows, dict) and "scenarios" in rows:
                rows = rows["scenarios"]
            if not isinstance(rows, list):
                raise ValueError("JSON must contain an array of ScenarioRow objects")
        elif fmt == "jsonl":
            rows = []
            for i, line in enumerate(text.splitlines()):
                if not line.strip():
                    rows.append({"_parse_error": "Blank JSONL line"})
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError as e:
                    rows.append({"_parse_error": str(e)})
        elif fmt == "csv":
            reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError("CSV requires unique column headers")
            rows = []
            for raw in reader:
                row = {"inputs": {}}
                try:
                    if None in raw:
                        raise ValueError("Extra CSV cells; check quoting")
                    fields = mapping or {k: k for k in raw}
                    for target, source in fields.items():
                        if source not in raw:
                            raise ValueError(f"Mapped source column {source!r} is missing")
                        v = raw[source]
                        if v is None or v.strip() == "":
                            continue
                        # Nested values and array fields use strict JSON cells.
                        parts = target.split(".")
                        leaf = parts[-1]
                        if leaf in ["progress", "planned_time", "reported_time", "control_results", "tags", "research_labels", "inputs", "planned_units", "completed_units", "value"]:
                            v = json.loads(v)
                        dest = row
                        for part in parts[:-1]:
                            dest = dest.setdefault(part, {})
                        dest[leaf] = v
                    rows.append(row)
                except (ValueError, TypeError, AttributeError) as e:
                    rows.append({"_parse_error": str(e)})
        else:
            raise ValueError("Choose json, jsonl or csv")
        if len(rows) > 5000:
            raise ValueError("Import limit is 5,000 rows per request")
        existing = self.s.all("scenarios")
        seen = [(fingerprint(r["inputs"]), r["scenario_id"], json.dumps(r["inputs"], sort_keys=True)) for r in self.latest("scenarios", dataset_id)]
        family_splits = {r["family_id"]: r["split"] for r in existing}
        ids = {r["scenario_id"] for r in existing}
        result = []
        for i, raw in enumerate(rows):
            item = {"row": i + (2 if fmt == "csv" else 1), "errors": [], "warnings": [], "data": None}
            try:
                if isinstance(raw, dict) and "_parse_error" in raw:
                    raise ValueError(raw["_parse_error"])
                parsed = ScenarioRow.model_validate(raw).model_dump()
                family = parsed["family_id"] or parsed["scenario_id"]
                if parsed["scenario_id"] and parsed["scenario_id"] in ids:
                    raise ValueError("Scenario ID already exists or occurs twice in this import")
                if family and family in family_splits and family_splits[family] != parsed["split"]:
                    raise ValueError("Family split conflict")
                if family:
                    family_splits[family] = parsed["split"]
                if parsed["scenario_id"]:
                    ids.add(parsed["scenario_id"])
                item["warnings"] = warnings(parsed["inputs"])
                fp, serial = fingerprint(parsed["inputs"]), json.dumps(parsed["inputs"], sort_keys=True)
                for other_fp, sid, other in seen:
                    if fp == other_fp:
                        item["warnings"].append(f"Exact duplicate of {sid}")
                    elif SequenceMatcher(None, serial, other, autojunk=False).ratio() >= .88:
                        item["warnings"].append(f"Potentially similar to {sid}; lexical heuristic only")
                seen.append((fp, parsed["scenario_id"] or f"import row {item['row']}", serial))
                item["data"] = parsed
            except (ValidationError, ValueError, TypeError) as e:
                item["errors"] = str(e).splitlines()
            result.append(item)
        return {"rows": result, "valid": sum(not r["errors"] for r in result), "invalid": sum(bool(r["errors"]) for r in result)}

    def commit_import(self, dataset_id, fmt, text, mapping=None):
        with self.s.connect():
            preview = self.preview(dataset_id, fmt, text, mapping)
            if preview["invalid"]:
                raise ValueError("Import contains structural errors. Correct them before committing; no rows imported.")
            created = [self.scenario(dataset_id, r["data"]) for r in preview["rows"]]
            return {"created": created, "warnings": [r for r in preview["rows"] if r["warnings"]]}

    def dataset_export(self, dataset_id):
        self.s.get("datasets", dataset_id)
        return [{k: row[k] for k in ScenarioRow.model_fields} for row in self.latest("scenarios", dataset_id)]

    def deletion_plan(self, kind, identifier):
        allowed = ["datasets", "questions", "scenarios", "snapshots", "jobs"]
        if kind not in allowed:
            raise ValueError("Unsupported deletion root")
        self.s.get(kind, identifier)
        kinds = allowed + ["outputs", "labels", "reviews", "influences", "assessment_attempts", "preflights"]
        rows = {k: self.s.all(k) for k in kinds}
        selected = {identifier}
        root = self.s.get(kind, identifier)
        if kind in ["scenarios", "questions"]:
            stable = "scenario_id" if kind == "scenarios" else "question_id"
            selected.update(r["id"] for r in rows[kind] if r[stable] == root[stable])
        changed = True
        while changed:
            changed = False
            for k, values in rows.items():
                for r in values:
                    refs = [r.get(f) for f in ["dataset_id", "parent_id", "dataset_snapshot_id", "bank_snapshot_id", "job_id", "evaluation_job_id", "source_job_id", "output_id", "version_id", "snapshot_id"]]
                    if k in ['jobs','preflights']:
                        refs.extend(r.get('config',{}).get(f) for f in ['source_job_id','dataset_snapshot_id','bank_snapshot_id'])
                    if k == 'jobs' and r.get('kind') == 'evaluation':
                        refs.extend(target['output_id'] for target in r.get('targets',[]))
                    if k == "snapshots":
                        refs.extend(v["id"] for v in r["records"])
                    if r["id"] not in selected and any(ref in selected for ref in refs):
                        selected.add(r["id"])
                        changed = True
        plan = {k: [r["id"] for r in values if r["id"] in selected] for k, values in rows.items()}
        if any(r["id"] in selected and r["status"] in ["queued", "running", "cancelling"] for r in rows["jobs"]):
            raise ValueError("Cancel dependent active jobs and wait for them to stop before deletion")
        return {k: ids for k, ids in plan.items() if ids}

    def delete(self, kind, identifier, cascade=False):
        with self.s.lock:
            plan = self.deletion_plan(kind, identifier)
            if sum(map(len, plan.values())) > 1 and not cascade:
                raise ValueError("Dependent records exist. Preview deletion and explicitly select cascade.")
            with self.s.connect() as c:
                for k, ids in plan.items():
                    c.executemany("DELETE FROM records WHERE kind=? AND id=?", [(k, i) for i in ids])
            return plan
