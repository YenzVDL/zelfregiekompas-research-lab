import { useEffect, useState } from "react";
import { api, pretty } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import { Badge, Editor, Empty, BankSelect, ModelSelect, Json } from "../components";
import { blankQuestion } from "../constants";
import { GenerationPrompt } from "./GenerationPrompt";

export function Bank(p: Common) {
  const [mode, setMode] = useState(""), [bankName, setBankName] = useState(""), [description, setDescription] = useState("");
  const [model, setModel] = useState(""), [count, setCount] = useState(5), [instructions, setInstructions] = useState("");
  const themeOptions = ["progress", "learning strategy", "understanding", "time management", "obstacles", "clarification", "next steps", "neutral fallback"];
  const [themes, setThemes] = useState(themeOptions);
  const [diagnosticDataset, setDiagnosticDataset] = useState(""), [diagnosticBank, setDiagnosticBank] = useState(""), [diagnostic, setDiagnostic] = useState<R | null>(null);
  const questions = p.questions.filter((question) => question.bank_id === p.bankId);
  const bank = p.banks.find((item) => item.id === p.bankId);
  const [selected, setSelected] = useState<R | null>(null),
    [text, setText] = useState(pretty(blankQuestion)),
    [feedback, setFeedback] = useState(""),
    [query, setQuery] = useState(""),
    [status, setStatus] = useState(""),
    [imports, setImports] = useState("[]"),
    [history, setHistory] = useState<R[]>([]);
  useEffect(() => {
    if (p.bankId) api("/questions?history=true&bank_id=" + p.bankId).then(setHistory);
  }, [p.revision, p.bankId]);
  function choose(q: R) {
    setSelected(q);
    setText(
      pretty(
        Object.fromEntries(Object.keys(blankQuestion).map((k) => [k, q[k]])),
      ),
    );
  }
  return (
    <>
      <details className="card"><summary>Bank applicability and selection diagnostics</summary>
        <label>Bank version<select aria-label="Diagnostic bank snapshot" value={diagnosticBank} onChange={(e) => setDiagnosticBank(e.target.value)}><option value="">Latest editable bank questions</option>{p.snapshots.filter((s) => s.kind === "bank" && s.bank_id === p.bankId).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label>Development dataset snapshot<select aria-label="Diagnostic development snapshot" value={diagnosticDataset} onChange={(e) => setDiagnosticDataset(e.target.value)}><option value="">Rules only</option>{p.snapshots.filter((s) => s.kind === "dataset").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <button disabled={p.busy || !p.bankId} onClick={() => p.run(async () => setDiagnostic(await api(`/banks/${p.bankId}/diagnostics?` + new URLSearchParams({ ...(diagnosticDataset ? {dataset_snapshot_id:diagnosticDataset} : {}), ...(diagnosticBank ? {bank_snapshot_id:diagnosticBank} : {}) }))), "Bank diagnostics calculated; no inference used.")}>Inspect applicability</button>
        {diagnostic && <><p>{diagnostic.unconditional_versions.length} fallback questions · {diagnostic.likely_near_duplicates.length} likely near-duplicate pairs · {diagnostic.development_cases} development cases. Unused does not mean unsuitable.</p><Json value={diagnostic} /></>}
      </details>
      <p className="lead">
        Keep independent question collections and freeze their exact versions. Regular snapshots require approval; experimental snapshots may include unreviewed questions for development only.
      </p>
      <div className="toolbar">
        <BankSelect banks={p.banks} value={p.bankId} onChange={p.setBankId} />
        <button onClick={() => setMode(mode === "bank" ? "" : "bank")}>+ New bank</button>
        <button disabled={!p.bankId} onClick={() => setMode(mode === "generate" ? "" : "generate")}>Generate questions</button>
        <input
          aria-label="Search bank"
          placeholder="Search questions or tags…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          aria-label="Bank review status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">All states</option>
          {["awaiting_review", "approved", "rejected"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <button
          disabled={!p.bankId}
          onClick={() => {
            setSelected(null);
            setText(pretty(blankQuestion));
          }}
        >
          + New question
        </button>
      </div>
      {bank && <div className="card"><b>{bank.name}</b><p>{bank.description}</p>
        <p>{bank.question_count} questions · Approved fallback: {bank.approved_fallback ? "Available" : "Not available"} · Experimental fallback: {bank.experimental_fallback ? "Available" : "Not available"}</p>
        <small>Tags do not execute rules. An unconditional question provides a fallback; its review status must be eligible for the selected snapshot mode.</small>
        <p><a href={`/api/banks/${p.bankId}/export`}>Export this bank with versions and generation prompts</a></p>
        {p.snapshots.filter((s) => s.kind === "bank" && s.bank_id === p.bankId).map((s) => <p key={s.id}>{s.name} · {s.review_mode === "experimental" ? "EXPERIMENTAL — development only" : "Approved only"} · {s.records.length} questions · <code>{s.content_hash.slice(0, 12)}</code></p>)}
      </div>}
      {mode === "bank" && <section className="card"><h2>Create question bank</h2>
        <label>Bank name<input value={bankName} onChange={(e) => setBankName(e.target.value)} /></label>
        <label>Bank description<textarea value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <button className="primary" disabled={!bankName.trim() || p.busy} onClick={() => p.run(async () => {
          const created = await api("/banks", "POST", { name: bankName, description }); p.setBankId(created.id); setMode("");
        }, "Independent question bank created.")}>Create bank</button>
      </section>}
      {mode === "generate" && <section className="card"><h2>Generate questions for {bank?.name}</h2>
        <ModelSelect models={p.modelState.models} value={model} onChange={setModel} label="Local bank question model" />
        <label>Number of questions<input type="number" min={1} max={1000} value={count} onChange={(e) => setCount(+e.target.value)} /></label>
        <p>Reflection themes</p><div className="actions">{themeOptions.map((theme) => <label className="check" key={theme}><input type="checkbox" checked={themes.includes(theme)} onChange={() => setThemes(themes.includes(theme) ? themes.filter((t) => t !== theme) : [...themes, theme])} />{theme}</label>)}</div>
        <label>Additional instructions (optional)<textarea maxLength={2000} value={instructions} onChange={(e) => setInstructions(e.target.value)} /></label>
        <button className="primary" disabled={p.busy || !p.bankId || !model || !themes.length || count < 1 || count > 1000} onClick={() => p.run(() => api(`/banks/${p.bankId}/generate`, "POST", { model, count, themes, instructions }), "Local question generation queued. Saved questions require review or an explicit experimental development snapshot.")}>Queue question generation</button>
        <p>One question per local request. Selecting a bank or model does not generate or download anything. The generator is independent of research-model and evaluator selections.</p>
      </section>}
      {p.jobs.filter((job) => job.kind === "question_generation" && job.bank_id === p.bankId && job.status !== "terminated" && !job.archived).map((job) => <div className="card" key={job.id}>
        <b>{job.name}</b> · {job.status} · {job.done}/{job.total} questions saved <progress value={job.done} max={job.total} />
        <p>Requested {job.total} · Saved {job.saved_count ?? job.done} · Failed {job.failed_count ?? 0} · Pending {job.pending_count ?? job.total - job.done}</p>
        {job.error && <p className="error-text">{job.error}</p>}
        {["queued", "running"].includes(job.status) && <button disabled={p.busy} onClick={() => p.run(() => api(`/jobs/${job.id}/cancel`, "POST", {}), "Cancellation requested; saved question drafts are retained.")}>Cancel question generation</button>}
        <small>Job {job.id} · Inspect attempts or resume remaining work in Experiments.</small>
      </div>)}
      <div className="split-view">
        <section className="card list-panel">
          <h2>Question bank</h2>
          {questions
            .filter(
              (q) =>
                (!query ||
                  pretty(q).toLowerCase().includes(query.toLowerCase())) &&
                (!status || q.review_status === status),
            )
            .map((q) => (
              <button
                key={q.id}
                className={
                  "list-item " + (q.id === selected?.id ? "selected" : "")
                }
                onClick={() => choose(q)}
              >
                <b>{q.text}</b>
                <small>
                  {q.question_id} · v{q.version}
                </small>
                <Badge status={q.review_status}>
                  {q.review_status.replaceAll("_", " ")}
                </Badge>
                <span className="tags">{q.tags.join(" · ")}</span>
              </button>
            ))}
          {!questions.length && (
            <Empty>
              No question bank exists yet. Create questions or import
              suggestions.
            </Empty>
          )}
        </section>
        <section className="card">
          <h2>
            {selected
              ? `Question version ${selected.version}`
              : "Create a question"}
          </h2>
          <p>
            Conditions support <code>present</code>, <code>missing</code>, and
            case-insensitive <code>contains</code> on allowed input paths. Empty
            conditions provide a fallback. Higher priority wins.
          </p>
          {selected && (
            <>
              <Badge status={selected.review_status}>
                {selected.review_status}
              </Badge>
              <label>
                Question version history
                <select
                  value={selected.id}
                  onChange={(e) =>
                    choose(history.find((q) => q.id === e.target.value)!)
                  }
                >
                  {history
                    .filter((q) => q.question_id === selected.question_id)
                    .map((q) => (
                      <option value={q.id} key={q.id}>
                        Version {q.version} · {q.review_status}
                      </option>
                    ))}
                </select>
              </label>
            </>
          )}
          <Editor
            label="Question and applicability (JSON)"
            value={text}
            set={setText}
          />
          <button
            className="primary"
            disabled={p.busy || !p.bankId}
            onClick={() =>
              p.run(
                async () =>
                  choose(
                    await api(
                      selected
                        ? `/questions/${selected.id}/revise`
                        : "/questions",
                      "POST",
                      { ...JSON.parse(text), bank_id: p.bankId },
                    ),
                  ),
                "Question saved as a new unapproved version.",
              )
            }
          >
            {selected ? "Save edited candidate" : "Create question for review"}
          </button>
          {selected && (
            <>
              <label>
                Review feedback
                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                />
              </label>
              <div className="actions">
                {["approved", "rejected"].map((decision) => (
                  <button
                    key={decision}
                    disabled={!p.reviewer || p.busy}
                    onClick={() =>
                      p.run(async () => {
                        await api(`/questions/${selected.id}/review`, "POST", {
                          decision,
                          reviewer: p.reviewer,
                          feedback,
                        });
                        setSelected({ ...selected, review_status: decision });
                      })
                    }
                  >
                    {decision === "approved"
                      ? "Approve question version"
                      : "Reject question version"}
                  </button>
                ))}
              </div>
              <p className="note">{selected.origin}</p>
              {selected.warnings?.map((warning: string) => <p className="warning" key={warning}>{warning}</p>)}
              <GenerationPrompt key={selected.id} scenario={selected} kind="question" />
            </>
          )}
        </section>
      </div>
      <details className="card">
        <summary>Import question suggestions (JSON array)</summary>
        <Editor label="Question objects" value={imports} set={setImports} />
        <button
          disabled={!p.bankId || p.busy}
          onClick={() =>
            p.run(
              () =>
                api("/questions/import", "POST", {
                  questions: JSON.parse(imports),
                  bank_id: p.bankId,
                }),
              "Questions imported as unapproved suggestions.",
            )
          }
        >
          Validate & import questions
        </button>
      </details>
    </>
  );
}
