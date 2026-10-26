"""Question quality and evaluator agreement are different quantities."""
from collections import Counter
from statistics import mean, median
from . import model_policy

CRITERIA = ["usefulness", "correctness"]


def latest_labels(labels):
    latest = {}
    for label in sorted(labels, key=lambda x: x["created_at"]):
        latest[(label["output_id"], label["output_version"], label["source"], label["reviewer"], label["role"])] = label
    return list(latest.values())


def reference(labels, output_id, version=1):
    eligible = [l for l in labels if l["output_id"] == output_id and l["output_version"] == version and l["source"] == "human"]
    adj = [l for l in eligible if l["role"] == "adjudication"]
    primary = [l for l in eligible if l["role"] == "primary"]
    return (adj or primary or [None])[-1]


def agreement(pairs):
    matrix = {truth: {pred: 0 for pred in ["Yes", "No"]} for truth in ["Yes", "No"]}
    for truth, pred in pairs:
        matrix[truth][pred] += 1
    classes = {}
    for cls, other in [("Yes", "No"), ("No", "Yes")]:
        tp, fp, fn = matrix[cls][cls], matrix[other][cls], matrix[cls][other]
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        classes[cls] = {"precision": precision, "recall": recall,
                        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
                        "support": tp + fn, "predicted": tp + fp}
    return {"paired_count": len(pairs), "classes": classes, "confusion_matrix": matrix,
            "matrix_axes": "rows: human reference; columns: AI prediction",
            "macro_f1": mean(c["f1"] for c in classes.values()) if pairs else None,
            "status": "Available" if pairs else "Not available: human reference labels required",
            "caution": "Small paired sample and/or absent class; interpret cautiously" if len(pairs) < 30 or any(c["support"] == 0 for c in classes.values()) else "",
            "zero_division": "Undefined precision, recall and class F1 are 0; fixed Yes/No classes always included"}


def report(outputs, labels, job, assessment_attempts=None):
    labels = latest_labels(labels)
    attempted = [o for o in outputs if o.get("attempt_started_at")]
    n = len(attempted)
    chosen = {"human": {}, "ai": {}}
    for o in outputs:
        ref = reference(labels, o["id"], o["version"])
        if ref:
            chosen["human"][o["id"]] = ref
        ai = [l for l in labels if l["source"] == "ai" and l["output_id"] == o["id"] and l["output_version"] == o["version"]]
        if ai:
            chosen["ai"][o["id"]] = ai[-1]
    def quality(source, subset):
        judgments = [(o, chosen[source].get(o["id"])) for o in subset if o['status'] == 'completed']
        criteria = {}
        for criterion in CRITERIA:
            vals = [l["judgment"][criterion]["value"] for _, l in judgments if l and l["judgment"][criterion]["value"] is not None]
            criteria[criterion] = {"assessed": len(vals), "yes": vals.count("Yes"), "no": vals.count("No"),
                                   "yes_rate_assessed": vals.count("Yes") / len(vals) if vals else None,
                                   "review_coverage": len(vals) / len(subset) if subset else None,
                                   "valid_output_coverage":len(vals) / len(judgments) if judgments else None}
        both_assessed = [(o, l) for o, l in judgments if l and all(l["judgment"][c]["value"] is not None for c in CRITERIA)]
        passed = sum(all(l["judgment"][c]["value"] == "Yes" for c in CRITERIA) for o, l in both_assessed)
        accepted = sum(o["status"] == "completed" and all(l["judgment"][c]["value"] == "Yes" for c in CRITERIA) for o, l in both_assessed)
        return {"criteria": criteria, "both_assessed": len(both_assessed), "both_yes": passed,
                "both_rate_assessed": passed / len(both_assessed) if both_assessed else None,
                "end_to_end_acceptance": accepted / len(subset) if subset and both_assessed else None,
                "assessment_status": "Assessed" if both_assessed else "Not assessed",
                "end_to_end_denominator": len(subset), "accepted_valid_outputs": accepted}
    categories = sorted({tag for o in attempted for tag in o.get("tags", [])})
    groups = sorted({(o["method"], o.get("model") or "Not applicable", o.get("condition_id") or "") for o in attempted})
    def condition_records(method, model, condition):
        return [o for o in attempted if o["method"] == method and (o.get("model") or "Not applicable") == model and (o.get("condition_id") or "") == condition]
    def condition_identity(method, records):
        return (records[0].get("bank_snapshot") or job.get("bank_snapshot")) if records and method in ["fixed", "rag"] else None
    quality_results = {src: {"overall": quality(src, attempted),
                           "by_category": {tag: quality(src, [o for o in attempted if tag in o["tags"]]) for tag in categories},
                           "by_condition": [{"method": m, "model": model, "condition_id": condition or None,
                                             "bank_snapshot": condition_identity(m, condition_records(m, model, condition)),
                                             **quality(src, condition_records(m, model, condition))} for m, model, condition in groups]}
                       for src in chosen}
    accuracy = {}
    for criterion in CRITERIA:
        pairs = []
        for o in outputs:
            human, ai = chosen["human"].get(o["id"]), chosen["ai"].get(o["id"])
            if human and ai and o['status'] == 'completed':
                h, a = human["judgment"][criterion]["value"], ai["judgment"][criterion]["value"]
                if h is not None and a is not None:
                    pairs.append((h, a))
        accuracy[criterion] = agreement(pairs)
    disagreements = []
    for o in outputs:
        human = [l for l in labels if l["source"] == "human" and l["output_id"] == o["id"] and l["output_version"] == o["version"] and l["role"] != "adjudication"]
        for criterion in CRITERIA:
            assessed = [l for l in human if l["judgment"][criterion]["value"] is not None]
            if len({l["judgment"][criterion]["value"] for l in assessed}) > 1:
                disagreements.append({"output_id": o["id"], "criterion": criterion, "label_ids": [l["id"] for l in assessed],
                                      "adjudicated": bool(reference(labels, o["id"], o["version"]) and reference(labels, o["id"], o["version"])["role"] == "adjudication")})
    generation = [o["generation_seconds"] for o in attempted if o.get("generation_seconds") is not None]
    evaluation = [o["evaluation_seconds"] for o in attempted if o.get("evaluation_seconds") is not None]
    inline_ids = {o.get('evaluation_trace',{}).get('id') for o in outputs}
    retrospective = [a.get('evaluation_seconds',0) for a in assessment_attempts or [] if a['id'] not in inline_ids and not a.get('calibration')]
    duration = job.get("active_seconds", 0)
    thresholds = job.get("thresholds", {})
    human_quality = quality_results["human"]["overall"]
    checks = {}
    if thresholds.get("min_both_quality") is not None:
        rate = human_quality["both_rate_assessed"]
        checks["min_both_quality"] = "Not assessed" if rate is None else rate >= thresholds["min_both_quality"]
    if thresholds.get("max_generation_seconds") is not None:
        checks["max_generation_seconds"] = "Not measured" if not generation else max(generation) <= thresholds["max_generation_seconds"]
    if thresholds.get("max_memory_mb") is not None:
        checks["max_memory_mb"] = "Not measured"
    selections = Counter(o.get('trace',{}).get('selected_version_id') for o in attempted if o['method'] == 'fixed' and o.get('trace',{}).get('selected_version_id'))
    retrievals = Counter(row['version_id'] for o in attempted if o['method'] == 'rag' for row in o.get('trace',{}).get('retrieved',[]))
    valid_count = sum(o['status'] == 'completed' for o in outputs)
    return {"planned": len(outputs), "attempted": n, "not_attempted": len(outputs) - n, "bank_snapshot": job.get("bank_snapshot"),
            "completion": {"processing_status":job.get('status','unknown'), "valid_outputs":valid_count, "execution_failures":sum(o['status'] not in ['pending','running','evaluating','completed','cancelled','interrupted'] for o in outputs), "notice":"Processing finished does not mean all generations succeeded"},
            "diagnostic_failure_reasons":dict(Counter(model_policy.classify(o.get('question') or '',o.get('generation_runtime') or {},o.get('validation_errors') or (['historical invalid output'] if o['status']=='invalid' else [])) if o['status'] in ['empty','invalid','empty_final_answer','output_limit_before_answer','truncated'] else o['status'] for o in attempted if o['status'] != 'completed')),
            "concentration": {"fixed_selected_version_counts":dict(selections), "fixed_top_share":max(selections.values())/sum(selections.values()) if selections else None, "rag_retrieved_version_counts":dict(retrievals), "rag_top_share":max(retrievals.values())/sum(retrievals.values()) if retrievals else None, "notice":"Concentration is diagnostic, not evidence of inappropriate selection or a winning model"},
            "statuses": dict(Counter(o["status"] for o in outputs)), "quality": quality_results,
            "failure_analysis": {"ai_evaluation_failures": sum(bool(o.get("evaluation_error")) for o in attempted),
                "by_category": {tag: dict(Counter(o["status"] for o in attempted if tag in o["tags"])) for tag in categories},
                "by_condition": [{"method": m, "model": model, "condition_id": condition or None,
                    "bank_snapshot": condition_identity(m, condition_records(m, model, condition)),
                    "statuses": dict(Counter(o["status"] for o in condition_records(m, model, condition))),
                    "generation_mean_seconds": mean([o["generation_seconds"] for o in condition_records(m, model, condition) if o.get("generation_seconds") is not None])
                    if any(o.get("generation_seconds") is not None for o in condition_records(m, model, condition)) else None}
                    for m, model, condition in groups]},
            "evaluator_accuracy": accuracy, "disagreements": disagreements,
            "feasibility": {"experiment_active_seconds": duration, "throughput_completed_per_second": sum(o["status"] == "completed" for o in outputs) / duration if duration else None,
                            "generation_mean_seconds": mean(generation) if generation else None, "generation_median_seconds": median(generation) if generation else None,
                            "generation_max_seconds": max(generation) if generation else None, "ai_evaluation_total_seconds": sum(evaluation),
                            "saved_output_evaluation_total_seconds":sum(retrospective), "evaluation_timing_scope":"Inline evaluation is part of original experiment time; later saved-output assessment is reported separately. Preflight/calibration timings excluded.",
                            "memory": "Not measured", "hardware": job.get("hardware"), "threshold_checks": checks,
                            "threshold_history": job.get("threshold_history", []), "warmup": "No separate warm-up; per-output Ollama load_duration retained when available"},
            "interpretation": "F1 measures AI evaluator agreement with human references for exact output versions. It does not measure question quality, educational benefit, or establish a winning method."}
