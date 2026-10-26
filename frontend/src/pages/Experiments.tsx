import { useEffect, useState } from "react";
import { api, pretty } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import { Badge, Json, Editor, Empty, ModelSelect } from "../components";
import { defaultSettings } from "../constants";

export function Experiments(p: Common) {
  const [showTerminated, setShowTerminated] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [jobSearch, setJobSearch] = useState("");
  const visibleJobs = [...p.jobs].reverse().filter((job) => Boolean(job.archived) === showArchived
    && (showArchived || showTerminated || job.status !== "terminated")
    && `${job.name} ${job.id} ${job.kind} ${job.status}`.toLowerCase().includes(jobSearch.toLowerCase()));
  const [name, setName] = useState("Reflection comparison"),
    [ds, setDs] = useState(""),
    [bank, setBank] = useState(""),
    [split, setSplit] = useState("development");
  const [methods, setMethods] = useState(["fixed"]),
    [models, setModels] = useState<string[]>([]),
    [ai, setAi] = useState(false),
    [evaluator, setEvaluator] = useState(""),
    [reps, setReps] = useState(1),
    [topK, setTopK] = useState(3);
  const [settings, setSettings] = useState(pretty(defaultSettings)),
    [evalSettings, setEvalSettings] = useState(
      pretty({ ...defaultSettings, temperature: 0, num_predict: 512 }),
    ),
    [thresholds, setThresholds] = useState("{}"),
    [hardware, setHardware] = useState("");
  const [selected, setSelected] = useState(""),
    [outputs, setOutputs] = useState<R[]>([]);
  const selectedBankSnapshot = p.snapshots.find((snapshot) => snapshot.id === bank);
  const usesBank = methods.some((method) => method === "fixed" || method === "rag");
  const invalidExperimental = usesBank && selectedBankSnapshot?.review_mode === "experimental" && split !== "development";
  const [mode, setMode] = useState("short"), [evaluationMode, setEvaluationMode] = useState("short");
  const [preflight, setPreflight] = useState<R | null>(null);
  const configuration = () => ({ name, dataset_snapshot_id:ds, bank_snapshot_id:usesBank ? bank || null : null, split, methods, models, repetitions:reps,
    settings:JSON.parse(settings), evaluation_settings:JSON.parse(evalSettings), ai_control:ai, evaluator_model:evaluator || null,
    top_k:topK, thresholds:JSON.parse(thresholds), hardware_notes:hardware, mode, evaluation_mode:evaluationMode });
  const incompatible = mode === "short" && methods.some((m) => m !== "fixed") && p.modelState.models.some((m: R) => models.includes(m.name) && ["thinking-only", "thinking controls unknown"].includes(m.identity?.variant));
  useEffect(() => {
    if (selected) api("/outputs?job_id=" + selected).then(setOutputs);
  }, [selected, p.revision]);
  const toggle = (values: string[], s: string) =>
    values.includes(s) ? values.filter((x) => x !== s) : [...values, s];
  return (
    <>
      <section className="card">
        <div className="section-title">
          <h2>Configure a single run or batch comparison</h2>
          <Badge>ACTUAL LOCAL EXECUTION</Badge>
        </div>
        <div className="form-grid">
          <label>
            Experiment name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Dataset snapshot
            <select
              aria-label="Dataset snapshot"
              value={ds}
              onChange={(e) => setDs(e.target.value)}
            >
              <option value="">Select frozen dataset</option>
              {p.snapshots
                .filter((s) => s.kind === "dataset")
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Split
            <select value={split} onChange={(e) => setSplit(e.target.value)}>
              {["development", "validation", "test"].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label>
            Question-bank snapshot
            <select
              aria-label="Question-bank snapshot"
              disabled={!usesBank}
              value={bank}
              onChange={(e) => setBank(e.target.value)}
            >
              <option value="">Select a bank snapshot</option>
              {p.snapshots
                .filter((s) => s.kind === "bank")
                .map((s) => (
                  <option value={s.id} key={s.id}>
                    {s.bank_name || "Default bank"} · {s.name} · {s.review_mode === "experimental" ? "EXPERIMENTAL — development only" : "Approved only"}
                  </option>
                ))}
            </select>
          </label>
        </div>
        {usesBank && selectedBankSnapshot?.review_mode === "experimental" && <p className="warning">EXPERIMENTAL BANK — includes unreviewed questions and is restricted to development experiments. Review statuses are preserved in the frozen snapshot.</p>}
        {invalidExperimental && <p className="error-text">Choose the development split or an approved-only bank snapshot.</p>}
        <div className="method-grid">
          {[
            [
              "fixed",
              "Fixed question bank",
              "Explicit rules. Model: Not applicable.",
            ],
            [
              "rag",
              "Retrieval-augmented generation",
              "TF-IDF retrieval + local LLM.",
            ],
            [
              "constrained",
              "Constrained LLM",
              "Scenario + constraints; no retrieval.",
            ],
          ].map(([id, title, desc]) => (
            <label
              className={"method " + (methods.includes(id) ? "chosen" : "")}
              key={id}
            >
              <input
                type="checkbox"
                checked={methods.includes(id)}
                onChange={() => setMethods(toggle(methods, id))}
              />
              <b>{title}</b>
              <small>{desc}</small>
            </label>
          ))}
        </div>
        {methods.some((m) => m !== "fixed") && (
          <fieldset>
            <legend>
              Installed local generator models · compare across all selected
              models
            </legend>
            {!p.modelState.models.length && (
              <p>
                No verified installed models found. Start Ollama and refresh
                discovery in Models. Use Install there to download a model.
              </p>
            )}
            {p.modelState.models.map((m: R) => (
              <label className="check" key={m.name}>
                <input
                  type="checkbox"
                  checked={models.includes(m.name)}
                  onChange={() => setModels(toggle(models, m.name))}
                />
                {m.name}{" "}
                <small>
                  {m.details?.parameter_size || "Size unknown"} ·{" "}
                  {m.details?.quantization_level || "Quantization unknown"}
                  {" · "}{m.identity?.variant || "Variant unknown"} {m.identity?.finetune || ""} {m.identity?.artifact_version || ""}
                </small>
              </label>
            ))}
          </fieldset>
        )}
        <label>Research inference mode<select aria-label="Research inference mode" value={mode} onChange={(e) => setMode(e.target.value)}><option value="short">Short reflection — instruct or explicit non-thinking</option><option value="thinking">Intentional thinking experiment</option></select></label>
        {incompatible && <p className="warning">Selected artifact is thinking-only or has unknown controls. Choose an installed instruct/non-thinking variant, or explicitly choose Thinking experiment with supported think=true/level and at least 1024 output tokens. Settings and models are never changed automatically.</p>}
        {mode === "thinking" && <p className="note">Thinking and final answers share the output budget. Set a supported explicit think value and an intentional budget ≥1024 (up to 65536), with num_ctx ≥ num_predict + 1024. Thinking-2507 often needs much larger budgets; use the official model guidance and preflight to confirm a final answer. No budget is increased automatically.</p>}
        {ai && <label>Inline evaluator mode<select aria-label="Inline evaluator mode" value={evaluationMode} onChange={(e) => setEvaluationMode(e.target.value)}><option value="short">Short non-thinking evaluation</option><option value="thinking">Intentional thinking evaluation</option></select></label>}
        {methods.some((m) => ["fixed", "rag"].includes(m)) &&
          !p.snapshots.some((s) => s.kind === "bank") && (
            <div className="warning">
              Bank-dependent methods cannot run: no snapshot of approved
              questions exists. Approve questions and freeze a bank snapshot
              first.
            </div>
          )}
        <div className="form-grid">
          <label>
            Repetitions
            <input
              type="number"
              min={1}
              max={100}
              value={reps}
              onChange={(e) => setReps(+e.target.value)}
            />
          </label>
          <label>
            Retrieval top k
            <input
              type="number"
              min={1}
              max={20}
              value={topK}
              onChange={(e) => setTopK(+e.target.value)}
            />
          </label>
          <label>
            AI control
            <select
              aria-label="AI control"
              value={ai ? "Yes" : "No"}
              onChange={(e) => setAi(e.target.value === "Yes")}
            >
              <option>No</option>
              <option>Yes</option>
            </select>
          </label>
          {ai && (
            <ModelSelect
              models={p.modelState.models}
              value={evaluator}
              onChange={setEvaluator}
              label="Separate local evaluator model"
            />
          )}
        </div>
        <p className="note">
          AI control enables optional automatic evaluation. Generation and
          scenario revision remain available when it is off. Human and AI labels
          stay separate.
        </p>
        {ai && models.includes(evaluator) && (
          <div className="warning">
            The same model fills generator and evaluator roles. Correlated bias
            is possible.
          </div>
        )}
        <details>
          <summary>
            Generation, evaluation, hardware & feasibility settings
          </summary>
          <div className="form-grid">
            <Editor
              label="Generation settings (JSON)"
              value={settings}
              set={setSettings}
              rows={9}
            />
            {ai && (
              <Editor
                label="Evaluator settings (JSON)"
                value={evalSettings}
                set={setEvalSettings}
                rows={9}
              />
            )}
            <Editor
              label="Optional thresholds (min_both_quality 0–1, max_generation_seconds, max_memory_mb); empty = no assumed thresholds"
              value={thresholds}
              set={setThresholds}
              rows={4}
            />
            <label className="full">
              Hardware notes (GPU, RAM, driver, runtime configuration)
              <textarea
                value={hardware}
                onChange={(e) => setHardware(e.target.value)}
              />
            </label>
          </div>
        </details>
        <button disabled={p.busy || invalidExperimental || incompatible} onClick={() => p.run(async () => setPreflight(await api("/experiments/preflight", "POST", configuration())), "Research preflight finished; inspect diagnostics before queuing.")}>Run research preflight</button>
        {preflight && <details open><summary>Research preflight — {preflight.passed ? "passed for saved configuration" : "failed; inspect before queuing"}</summary><p>Separate from research outputs and timings. Configuration/model changes require another preflight for batches over 10 model-using cases.</p><Json value={preflight} /></details>}
        <button
          className="primary"
          disabled={p.busy || invalidExperimental || incompatible}
          onClick={() =>
            p.run(async () => {
              const j = await api("/experiments", "POST", {...configuration(), preflight_id:preflight?.id || null});
              setSelected(j.id);
            }, "Experiment queued. Cases and partial results are saved locally.")
          }
        >
          Queue experiment →
        </button>
      </section>
      <section className="card">
        <h2>Persistent jobs</h2>
        <label className="check"><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />Archived jobs ({p.jobs.filter((job) => job.archived).length})</label>
        <label>Find job<input aria-label="Find job" value={jobSearch} onChange={(e) => setJobSearch(e.target.value)} placeholder="Name, ID or status" /></label>
        <label className="check"><input type="checkbox" checked={showTerminated} onChange={(e) => setShowTerminated(e.target.checked)} />Show terminated jobs</label>
        {!p.jobs.length && <Empty>No experiments or scenario tasks yet.</Empty>}
        {p.jobs.length > 0 && !visibleJobs.length && <Empty>No jobs in this view. Use Archived jobs or Show terminated jobs to find retained history.</Empty>}
        {visibleJobs.map((j) => (
          <div className="job" key={j.id}>
            <div className="row spread">
              <button className="text" onClick={() => setSelected(j.id)}>
                <b>{j.name}</b>
              </button>
              <Badge status={j.status}>{j.status}</Badge>
            </div>
            <small>
              {j.id} · {j.kind} · {j.kind === "question_generation" ? `Requested ${j.total} · Saved ${j.saved_count ?? j.done} · Failed ${j.failed_count ?? 0} · Pending ${j.pending_count ?? j.total - j.done}` : `${j.done}/${j.total} finished`}
            </small>
            <progress value={j.done} max={j.total || 1} />
            {j.error && <p className="error-text">{j.error}</p>}
            {j.kind === "evaluation" && <p>{j.assessed_count || 0} assessments saved · {j.failed_count || 0} failed · {j.skipped_count || 0} skipped · exact output versions retained. {j.bias_flags?.join("; ")}</p>}
            {j.generation_summary && <details><summary>Scenario generation coverage and warnings</summary><Json value={j.generation_summary} /></details>}
            {j.bank_snapshot_id && <p>Bank snapshot: {j.bank_snapshot?.bank_name || p.snapshots.find((snapshot) => snapshot.id === j.bank_snapshot_id)?.bank_name || "Default bank"} · {j.bank_snapshot_id} · {(j.bank_snapshot?.review_mode || p.snapshots.find((snapshot) => snapshot.id === j.bank_snapshot_id)?.review_mode) === "experimental" ? "EXPERIMENTAL — development only" : "Approved only"} · <code>{j.bank_snapshot?.content_hash?.slice(0, 16)}</code></p>}
            {(j.scenario_attempts || j.question_attempts)?.length > 0 && <details>
              <summary>{j.kind === "question_generation" ? "Question" : "Scenario"} generation attempts ({(j.scenario_attempts || j.question_attempts).length})</summary>
              <p>Valid drafts are retained. Each new draft requires human review. Inspect execution for prompts, schema, raw responses and effective settings.</p>
              {(j.scenario_attempts || j.question_attempts).map((attempt: R, i: number) => <div key={i}>
                <b>Item {attempt.sequence} · Attempt {attempt.attempt || "legacy"} · {attempt.status}</b>
                {attempt.errors?.map((error: R, k: number) => <p key={k} className="error-text">{error.path}: {error.message} ({error.type})</p>)}
              </div>)}
            </details>}
            <div className="actions">
              {["completed", "completed_with_errors", "failed", "cancelled", "interrupted", "terminated"].includes(j.status) && <button disabled={p.busy} onClick={() => p.run(async () => {
                await api(`/jobs/${j.id}/${j.archived ? "unarchive" : "archive"}`, "POST", {});
                if (selected === j.id) { setSelected(""); setOutputs([]); }
              }, j.archived ? "Job restored to the main list." : "Job archived. Find it under Archived jobs; all records are retained.")}>{j.archived ? "Restore job" : "Archive job"}</button>}
              {["queued", "failed", "cancelled", "interrupted"].includes(j.status) && <button disabled={p.busy} onClick={() => p.run(async () => {
                await api(`/jobs/${j.id}/terminate`, "POST", {});
                if (selected === j.id) { setSelected(""); setOutputs([]); }
              }, "Job terminated and removed from the queue. Results and diagnostics are retained under Show terminated jobs.")}>{j.status === "queued" ? "Remove from queue" : "Terminate job"}</button>}
              {j.status === "terminated" && <p>Terminated. Results and diagnostics are retained; this job cannot be resumed.</p>}
              {j.status === "running" && (
                <button
                  disabled={p.busy}
                  onClick={() =>
                    p.run(
                      () => api(`/jobs/${j.id}/cancel`, "POST", {}),
                      "Cancellation requested; partial records are retained.",
                    )
                  }
                >
                  Cancel job
                </button>
              )}
              {["interrupted", "cancelled", "failed", "completed_with_errors"].includes(j.status) && (
                <button
                  disabled={p.busy}
                  onClick={() =>
                    p.run(
                      () => api(`/jobs/${j.id}/resume`, "POST", {}),
                      "Job resumed; finalized outputs will not be regenerated.",
                    )
                  }
                >
                  Resume remaining work
                </button>
              )}
              <a className="button" href={`/api/jobs/${j.id}/export`}>
                Export records
              </a>
              <button onClick={() => setSelected(j.id)}>
                Inspect execution
              </button>
            </div>
            {j.results?.length > 0 && (
              <p>Candidate {j.kind === "question_generation" ? "question" : "scenario"} versions: {j.results.join(", ")}</p>
            )}
          </div>
        ))}
      </section>
      {selected && (
        <section className="card">
          <h2>Inspectable execution records</h2>
          <p>{selected}</p>
          <details>
            <summary>Experiment configuration & model metadata</summary>
            <Json value={p.jobs.find((j) => j.id === selected)} />
          </details>
          {outputs.map((o) => (
            <details key={o.id}>
              <summary>
                {o.status} · {o.method} · {o.model || "Not applicable"} ·{" "}
                {o.question || o.error || "Waiting"}
              </summary>
              <Json value={o} />
            </details>
          ))}
        </section>
      )}
    </>
  );
}
