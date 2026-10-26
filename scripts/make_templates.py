"""Regenerate human-readable import examples without external tools or inference."""
import csv
import json
from pathlib import Path
from backend.demo import SCENARIOS, QUESTIONS

root = Path(__file__).resolve().parents[1] / "templates"
root.mkdir(exist_ok=True)
rows = [{**r, "split": "development", "synthetic_origin": "DEMONSTRATION: manually constructed synthetic case"} for r in SCENARIOS]
(root / "scenarios.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
(root / "scenarios.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
with (root / "scenarios.csv").open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["inputs", "tags", "split", "synthetic_origin"])
    writer.writeheader()
    for r in rows:
        writer.writerow({k: json.dumps(v) if isinstance(v, (dict,list)) else v for k,v in r.items()})
(root / "questions.json").write_text(json.dumps(QUESTIONS, indent=2), encoding="utf-8")
(root / "csv-mapping.json").write_text(json.dumps({"inputs.learning_goal":"Goal", "inputs.progress":"Progress", "inputs.student_note":"Note", "tags":"Categories"}, indent=2), encoding="utf-8")
