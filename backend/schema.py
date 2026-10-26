"""Structural validation only: incomplete and contradictory inputs remain valid."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Progress(Strict):
    description: str | None = None
    planned_units: float | None = Field(default=None, strict=True)
    completed_units: float | None = Field(default=None, strict=True)
    unit: str | None = None


class TimeValue(Strict):
    value: float = Field(strict=True)
    unit: Literal["seconds", "minutes", "hours"]


class ControlResult(Strict):
    question: str | None = None
    student_answer: str | None = None
    reference_answer: str | None = None
    recorded_assessment: str | None = None
    ai_inferred_assessment: str | None = None


class Inputs(Strict):
    learning_goal: str | None = None
    planned_activity: str | None = None
    progress: Progress | None = None
    planned_time: TimeValue | None = None
    reported_time: TimeValue | None = None
    control_results: list[ControlResult] | None = None
    student_note: str | None = None
    previous_question: str | None = None


class ScenarioRow(Strict):
    inputs: Inputs
    scenario_id: str | None = None
    family_id: str | None = None
    split: Literal["development", "validation", "test"] = "development"
    tags: list[str] = Field(default_factory=list)
    synthetic_origin: str = "researcher-supplied synthetic"
    research_labels: dict = Field(default_factory=dict)
    evaluation_notes: str = ""


class Comparison(Strict):
    left: str
    op: Literal["eq", "gt", "gte", "lt", "lte"]
    right: str


class Conditions(Strict):
    present: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    contains: dict[str, str] = Field(default_factory=dict)
    compare: list[Comparison] = Field(default_factory=list)
    control_assessment: Literal["correct", "incorrect"] | None = None


class Question(Strict):
    text: str = Field(min_length=3, max_length=400)
    tags: list[str] = Field(default_factory=list)
    conditions: Conditions = Field(default_factory=Conditions)
    priority: int = Field(default=0, strict=True)
    origin: str = "human-authored; unvalidated"


class Criterion(Strict):
    value: Literal["Yes", "No"] | None = None
    explanation: str = ""
    context: str = ""


class Judgment(Strict):
    usefulness: Criterion = Field(default_factory=Criterion)
    correctness: Criterion = Field(default_factory=Criterion)


def prompt_inputs(value: dict) -> dict:
    """An explicit allow-list at ALL nesting levels; never serialize metadata."""
    result = Inputs.model_validate(value).model_dump()
    # An inferred assessment is provenance, not observed student evidence.
    if result.get("control_results"):
        for row in result["control_results"]:
            row.pop("ai_inferred_assessment", None)
    return result


def warnings(inputs: dict) -> list[str]:
    out = []
    if not inputs.get("learning_goal"):
        out.append("Learning goal is unknown.")
    p = inputs.get("progress") or {}
    if p.get("planned_units") is not None and p.get("completed_units") is not None:
        if p["completed_units"] > p["planned_units"]:
            out.append("Completed units exceed planned units; may be intentional.")
    if any(isinstance(v, (int, float)) and v < 0 for v in p.values()):
        out.append("Negative progress; retain only if intentional.")
    if any((inputs.get(k) or {}).get("value", 0) < 0 for k in ["planned_time", "reported_time"]):
        out.append("Negative time; may be an intentional robustness case.")
    note = (inputs.get("student_note") or "").lower()
    if any(t in note for t in ["ignore", "system prompt", "instruction"]):
        out.append("Possible embedded instruction. Treat as scenario data, never instructions.")
    return out
