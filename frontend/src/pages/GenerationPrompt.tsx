import { useState } from "react";
import { Json } from "../components";
import type { RecordData as R } from "../api";

export function GenerationPrompt({ scenario, kind = "scenario" }: { scenario: R; kind?: "scenario" | "question" }) {
  const generation = scenario.generation;
  const attempts: R[] = generation?.attempts?.length ? generation.attempts : generation ? [generation] : [];
  const saved = Math.max(0, attempts.findIndex((a) => a.id === generation?.saved_attempt_id));
  const [chosen, setChosen] = useState(saved);
  const trace = attempts[chosen] || generation;
  const payload = trace?.request?.payload;
  // Historical values are read only from saved records, never current templates.
  const system = payload?.system ?? trace?.system_prompt;
  const prompt = payload?.prompt ?? trace?.prompt;
  const recorded = typeof system === "string" && typeof prompt === "string";
  const schema = payload ? (typeof payload.format === "object" ? payload.format : null) : trace?.response_schema;
  const effectiveSettings = trace?.effective_settings
    ? Object.fromEntries(Object.entries(trace.effective_settings).filter(([key]) => key !== "format"))
    : trace?.settings;
  return <details>
    <summary>View generation prompt</summary>
    {!recorded ? <p>Generation prompt not recorded</p> : <>
      {attempts.length > 1 && <label>Generation attempt
        <select value={chosen} onChange={(e) => setChosen(Number(e.target.value))}>
          {attempts.map((a, i) => <option key={a.id || i} value={i}>Attempt {a.attempt || i + 1} · {a.status || "recorded"}{i === saved ? ` · produced this ${kind}` : ""}</option>)}
        </select>
      </label>}
      <p>{chosen === saved ? `This attempt produced the saved ${kind} version.` : `Earlier attempt; this response was not saved as the ${kind}.`}</p>
      {!payload && <p>Original saved prompt; the full HTTP request was not recorded.</p>}
      <h3>System message</h3><pre>{system}</pre>
      <h3>User message</h3><pre>{prompt}</pre>
      <h3>Output schema</h3>{schema ? <Json value={schema} /> : <p>Output schema not recorded</p>}
      <h3>Model</h3><p>{payload?.model || trace?.model || "Not recorded"}</p>
      {kind === "question" && <><p>Installed digest: {trace?.model_metadata?.digest || "Not recorded"}</p><p>Generation job: {trace?.job_id || "Not recorded"} · Attempt: {trace?.id || "Not recorded"}</p></>}
      <h3>{trace?.effective_settings ? "Effective generation settings" : "Saved settings (effective settings not recorded)"}</h3>
      <Json value={effectiveSettings || "Not recorded"} />
      {trace?.errors?.length > 0 && <><h3>Attempt feedback</h3><Json value={trace.errors} /></>}
    </>}
    <a className="button" href={`/api/${kind === "question" ? "questions" : "scenarios"}/${scenario.id}/export`}>Export {kind} with generation prompt</a>
  </details>;
}
