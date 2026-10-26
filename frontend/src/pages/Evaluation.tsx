import { useEffect, useState } from "react";
import { api, pretty } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import { Badge, Json, Empty, InputTable, ModelSelect, Editor } from "../components";
import { emptyCriterion, defaultSettings } from "../constants";

export function Evaluation(p: Common) {
  const [sourceJob, setSourceJob] = useState(""), [subsetMethod, setSubsetMethod] = useState(""), [generator, setGenerator] = useState(""), [category, setCategory] = useState("");
  const [hasAI, setHasAI] = useState("all"), [hasHuman, setHasHuman] = useState("all"), [reevaluate, setReevaluate] = useState(false);
  const [evaluator, setEvaluator] = useState(""), [evaluationMode, setEvaluationMode] = useState("short");
  const [settings, setSettings] = useState(pretty({...defaultSettings, temperature:0, num_predict:512}));
  const [preview, setPreview] = useState<R | null>(null), [sourceOutputs, setSourceOutputs] = useState<R[]>([]), [calibration, setCalibration] = useState<R | null>(null);
  const [assessmentAttempts, setAssessmentAttempts] = useState<R[]>([]);
  const selection = {source_job_id:sourceJob, methods:subsetMethod ? [subsetMethod] : [], generator_models:generator ? [generator] : [], categories:category ? [category] : [], ai_assessed:hasAI, human_assessed:hasHuman, reevaluate};
  useEffect(() => {
    let current = true;
    if (!sourceJob) {setPreview(null); setSourceOutputs([]); return;}
    api("/outputs?job_id=" + sourceJob).then((rows) => {if (current) setSourceOutputs(rows);});
    api("/evaluation/eligible", "POST", selection).then((result) => {if (current) setPreview(result);});
    return () => {current = false;};
  }, [sourceJob, subsetMethod, generator, category, hasAI, hasHuman, reevaluate, p.revision]);
  useEffect(() => {api("/evaluation/attempts").then(setAssessmentAttempts);}, [p.revision]);
  const [blind, setBlind] = useState(true),
    [seed, setSeed] = useState(42),
    [queue, setQueue] = useState<R[]>([]),
    [index, setIndex] = useState(0),
    [role, setRole] = useState("primary"),
    [judgment, setJudgment] = useState<R>({
      usefulness: emptyCriterion(),
      correctness: emptyCriterion(),
    }),
    [labels, setLabels] = useState<R[]>([]),
    [refs, setRefs] = useState<string[]>([]);
  useEffect(() => {
    api(`/evaluation-queue?blind=${blind}&order_seed=${seed}${sourceJob ? "&job_id="+sourceJob : ""}`).then(setQueue);
  }, [p.revision, blind, seed, sourceJob]);
  const current = queue[index];
  useEffect(() => {
    setJudgment({
      usefulness: emptyCriterion(),
      correctness: emptyCriterion(),
    });
    setRefs([]);
    setLabels([]);
    if (current) api("/labels?output_id=" + current.id).then(setLabels);
  }, [current?.id, p.revision]);
  const change = (c: string, k: string, v: unknown) =>
    setJudgment({ ...judgment, [c]: { ...judgment[c], [k]: v } });
  return (
    <>
      <section className="card">
        <h2>Evaluate saved outputs with a local model</h2>
        <p>Assessments are separate from generation. Selecting filters or models never runs inference. Invalid/empty/failed outputs are skipped as execution failures, not judged as valid questions.</p>
        <div className="form-grid">
          <label>Experiment to evaluate<select aria-label="Experiment to evaluate" value={sourceJob} onChange={(e) => {setSourceJob(e.target.value); setIndex(0); setSubsetMethod(""); setGenerator(""); setCategory("");}}><option value="">Select experiment</option>{p.jobs.filter((j) => j.kind === "experiment").map((j) => <option key={j.id} value={j.id}>{j.name} · {j.id}</option>)}</select></label>
          <label>Method subset<select aria-label="Evaluation method subset" value={subsetMethod} onChange={(e) => setSubsetMethod(e.target.value)}><option value="">All methods</option>{["fixed","rag","constrained"].map((m) => <option key={m}>{m}</option>)}</select></label>
          <label>Generator subset<select aria-label="Evaluation generator subset" value={generator} onChange={(e) => setGenerator(e.target.value)}><option value="">All generators</option>{[...new Set(sourceOutputs.map((o) => o.model || "Not applicable"))].map((m) => <option key={m}>{m}</option>)}</select></label>
          <label>Scenario category<select aria-label="Evaluation category subset" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{[...new Set(sourceOutputs.flatMap((o) => o.tags || []))].map((tag) => <option key={tag}>{tag}</option>)}</select></label>
          <label>Existing AI assessment<select value={hasAI} onChange={(e) => setHasAI(e.target.value)}><option value="all">All</option><option value="no">Not AI assessed</option><option value="yes">AI assessed</option></select></label>
          <label>Existing human assessment<select value={hasHuman} onChange={(e) => setHasHuman(e.target.value)}><option value="all">All</option><option value="no">Not human assessed</option><option value="yes">Human assessed</option></select></label>
          <ModelSelect label="Local evaluator for saved outputs" models={p.modelState.models} value={evaluator} onChange={setEvaluator} />
          <label>Evaluation mode<select value={evaluationMode} onChange={(e) => setEvaluationMode(e.target.value)}><option value="short">Short non-thinking assessment</option><option value="thinking">Intentional thinking assessment</option></select></label>
        </div>
        <label className="check"><input type="checkbox" checked={reevaluate} onChange={(e) => setReevaluate(e.target.checked)} />Re-evaluate already AI-assessed output versions (creates new records)</label>
        <details><summary>Explicit local evaluator settings</summary><Editor label="Saved-output evaluator settings (JSON)" value={settings} set={setSettings} rows={9} /></details>
        <p aria-live="polite">{preview?.eligible_count || 0} eligible · {preview?.skipped_count || 0} skipped</p>
        {preview?.eligible.some((item: R) => item.generator_model === evaluator) && <p className="warning">Evaluator also generated selected outputs. Intentional self-evaluation is allowed and recorded as a bias flag.</p>}
        <button className="primary" disabled={p.busy || !evaluator || !preview?.eligible_count} onClick={() => p.run(() => api("/evaluation/jobs", "POST", {...selection, model:evaluator, settings:JSON.parse(settings), mode:evaluationMode}), "Local evaluation queued; original outputs will not be regenerated or changed.")}>Evaluate with local model</button>
        <details><summary>Eligibility and skipped-output reasons</summary><Json value={preview} /></details>
        <details><summary>Evaluator calibration on controlled development examples</summary><p>Six examples check reflective relevance, factual contradiction, invented emotions, irrelevance, a generic safe question and technical content with limited reflection. Checks are excluded from research metrics and do not establish evaluator accuracy.</p><button disabled={p.busy || !evaluator} onClick={() => p.run(async () => setCalibration(await api("/evaluation/calibrate", "POST", {model:evaluator, settings:JSON.parse(settings), mode:evaluationMode})), "Development calibration finished; inspect judgments and failures.")}>Run evaluator calibration</button>{calibration && <Json value={calibration} />}</details>
        {p.jobs.filter((j) => j.kind === "evaluation" && !j.archived && (!sourceJob || j.config.source_job_id === sourceJob)).map((j) => <div className="job" key={j.id}><b>{j.name} · {j.status}</b><p>{j.done}/{j.total} processed · {j.assessed_count || 0} assessed · {j.failed_count || 0} failed · {j.skipped_count || 0} skipped</p><progress value={j.done} max={j.total} />{j.error && <p className="error-text">{j.error}</p>}{j.bias_flags?.map((flag: string) => <p className="warning" key={flag}>{flag}</p>)}
          {["queued","running"].includes(j.status) && <button disabled={p.busy} onClick={() => p.run(() => api(`/jobs/${j.id}/cancel`, "POST", {}), "Evaluation cancellation requested; assessments retained.")}>Cancel evaluation</button>}
          {["failed","cancelled","interrupted","completed_with_errors"].includes(j.status) && <button disabled={p.busy} onClick={() => p.run(() => api(`/jobs/${j.id}/resume`, "POST", {}), "Evaluation resumed; successful exact-version assessments are retained.")}>Resume evaluation</button>}
          <a className="button" href={`/api/jobs/${j.id}/export`}>Export evaluation</a><details><summary>Inspect assessment requests and outcomes</summary><Json value={assessmentAttempts.filter((a) => a.evaluation_job_id === j.id)} /></details>
        </div>)}
      </section>
      <h2>Manual review and comparison</h2>
      <div className="toolbar">
        <label className="check">
          <input
            type="checkbox"
            checked={blind}
            onChange={(e) => setBlind(e.target.checked)}
          />
          Hide model and method identities
        </label>
        <label>
          Randomized order seed
          <input
            type="number"
            value={seed}
            onChange={(e) => {
              setSeed(+e.target.value);
              setIndex(0);
            }}
          />
        </label>
        <Badge>{queue.length} outputs available</Badge>
      </div>
      <p className="lead">
        Assess each exact output version. “Not assessed” is an incomplete review
        state and is never converted to No. Other judgments remain hidden until
        you choose to inspect them.
      </p>
      {!current ? (
        <Empty>
          No finalized questions available. Run an experiment first.
        </Empty>
      ) : (
        <>
          <div className="row spread">
            <button disabled={index === 0} onClick={() => setIndex(index - 1)}>
              ← Previous output
            </button>
            <span>
              Output {index + 1} of {queue.length}
            </span>
            <button
              disabled={index >= queue.length - 1}
              onClick={() => setIndex(index + 1)}
            >
              Next output →
            </button>
          </div>
          <div className="evaluation-layout">
            <section className="card">
              <h2>Available scenario context</h2>
              <InputTable inputs={current.inputs} />
            </section>
            <section className="card">
              <span className="eyebrow">
                REFLECTION QUESTION · OUTPUT V{current.version}
              </span>
              <blockquote>{current.question}</blockquote>
              <small>{current.id}</small>
              {!blind && (
                <p>
                  Method: {current.method} · Model:{" "}
                  {current.model || "Not applicable"}
                </p>
              )}
              {current.status === "invalid" && (
                <div className="warning">
                  This output failed format or repetition checks. Its record
                  remains part of reporting.
                </div>
              )}
              <label>
                Review role
                <select value={role} onChange={(e) => setRole(e.target.value)}>
                  <option value="primary">Primary reference review</option>
                  <option value="secondary">
                    Independent second reviewer (optional subset)
                  </option>
                  <option value="adjudication">Adjudication</option>
                </select>
              </label>
              {(["usefulness", "correctness"] as const).map((c) => (
                <fieldset key={c}>
                  <legend>
                    {c === "usefulness"
                      ? "Usefulness"
                      : "Contextual correctness"}
                  </legend>
                  <p>
                    {c === "usefulness"
                      ? "Meaningful reflection? Clear, answerable, relevant? Preserves agency without pressure or judgment?"
                      : "Accurate use of available evidence? No invented performance, emotions, causes or circumstances? Handles unknowns and contradictions?"}
                  </p>
                  <label>
                    Judgment
                    <select
                      aria-label={c + " judgment"}
                      value={judgment[c].value || ""}
                      onChange={(e) =>
                        change(c, "value", e.target.value || null)
                      }
                    >
                      <option value="">Not assessed</option>
                      <option>Yes</option>
                      <option>No</option>
                    </select>
                  </label>
                  <label>
                    Short explanation
                    <textarea
                      aria-label={c + " explanation"}
                      value={judgment[c].explanation}
                      onChange={(e) => change(c, "explanation", e.target.value)}
                    />
                  </label>
                  <label>
                    Supporting or conflicting context
                    <textarea
                      aria-label={c + " context"}
                      value={judgment[c].context}
                      onChange={(e) => change(c, "context", e.target.value)}
                    />
                  </label>
                </fieldset>
              ))}
              {role === "adjudication" && (
                <fieldset>
                  <legend>Reference labels from at least two reviewers</legend>
                  {labels
                    .filter(
                      (l) =>
                        l.source === "human" &&
                        l.output_version === current.version &&
                        l.role !== "adjudication",
                    )
                    .map((l) => (
                      <label className="check" key={l.id}>
                        <input
                          type="checkbox"
                          checked={refs.includes(l.id)}
                          onChange={() =>
                            setRefs(
                              refs.includes(l.id)
                                ? refs.filter((v) => v !== l.id)
                                : [...refs, l.id],
                            )
                          }
                        />
                        {l.reviewer} · {l.role} · {l.id}
                      </label>
                    ))}
                </fieldset>
              )}
              <button
                className="primary"
                disabled={!p.reviewer || p.busy}
                onClick={() =>
                  p.run(
                    () =>
                      api("/labels", "POST", {
                        output_id: current.id,
                        output_version: current.version,
                        reviewer: p.reviewer,
                        role,
                        judgment,
                        reference_label_ids: refs,
                      }),
                    "Human judgment saved separately from AI judgments.",
                  )
                }
              >
                Save human evaluation
              </button>
              <details>
                <summary>
                  Inspect existing judgments (may affect blinding)
                </summary>
                <p>AI explanations are model judgments, not proof.</p>
                <p>Active AI assessment is the most recent record for this exact output version. Earlier assessments and human reviews remain stored separately.</p>
                <table><thead><tr><th>Source and reviewer</th><th>Usefulness</th><th>Contextual correctness</th></tr></thead><tbody>{labels.filter((label) => label.output_version === current.version && label.is_active).map((label) => <tr key={label.id}><td>{label.source} · {label.reviewer} · {label.role}</td><td>{label.judgment.usefulness.value || "Not assessed"}</td><td>{label.judgment.correctness.value || "Not assessed"}</td></tr>)}</tbody></table>
                <Json value={labels} />
              </details>
            </section>
          </div>
        </>
      )}
    </>
  );
}
