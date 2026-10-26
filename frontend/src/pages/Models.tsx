import { useEffect, useRef, useState } from "react";
import { api, seconds, type RecordData as R } from "../api";
import type { Common } from "../types";
import { ModelSelect } from "../components";
import { ModelPicker, VariantDetails } from "./ModelPicker";

const bytes = (n?: number) => n == null ? "Unknown" : `${(n / 1024 ** 3).toFixed(2)} GiB`;

export function Models(p: Common & { setModelState: (state: R) => void }) {
  const [address, setAddress] = useState("");
  const [savedAddress, setSavedAddress] = useState("");
  const [name, setName] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [variant, setVariant] = useState<R | null>(null);
  const [model, setModel] = useState("");
  const [action, setAction] = useState("");
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<R | null>(null);
  const [result, setResult] = useState<R | null>(null);
  const [events, setEvents] = useState<R[]>([]);
  const [layers, setLayers] = useState<Record<string, R>>({});
  const [installed, setInstalled] = useState(false);
  const [installFailed, setInstallFailed] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    api("/models/connection").then((c) => {
      setAddress(c.address);
      setSavedAddress(c.address);
    }).catch((e) => setError(e.message));
    return () => controller.current?.abort();
  }, []);
  const refresh = async () => p.setModelState(await api("/models"));
  async function perform(label: string, fn: () => Promise<void>) {
    setAction(label);
    setError("");
    try { await fn(); } catch (e) { setError((e as Error).message); }
    finally { setAction(""); }
  }
  async function install() {
    setEvents([]); setLayers({}); setInstalled(false); setInstallFailed(false);
    controller.current = new AbortController();
    try {
      const response = await fetch("/api/models/install", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: name }), signal: controller.current.signal,
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Could not start installation");
      }
      if (!response.body) throw new Error("Download progress stream is unavailable");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", done = false;
      function consume(line: string) {
        if (!line.trim()) return;
        const event = JSON.parse(line);
        if (event.error) throw new Error(event.error);
        setEvents((old) => [...old.slice(-19), event]);
        if (event.digest && event.total != null) setLayers((old) => ({ ...old, [event.digest]: event }));
        if (event.done) { done = true; setInstalled(true); }
      }
      try {
        while (true) {
          const chunk = await reader.read();
          buffer += decoder.decode(chunk.value, { stream: !chunk.done });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          lines.forEach(consume);
          if (chunk.done) break;
        }
        consume(buffer);
        if (!done) throw new Error("Connection ended before local installation was verified. Retry Install to resume.");
      } finally { await reader.cancel(); }
    } catch (e) {
      setInstallFailed(true);
      setError((e as Error).message);
      throw e;
    } finally {
      controller.current = null;
      setAction("Refreshing");
      await refresh();
    }
  }
  const downloading = action === "Installing";
  const latest = events.at(-1);
  const blocked = !!action;
  return <>
    <section className="card">
      <h2>Ollama connection</h2>
      <p>Requests go through this application’s backend. The address is resolved on the computer or container running the backend.</p>
      <label>Ollama address<input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="http://127.0.0.1:11434" /></label>
      <p>Saved address: <code>{savedAddress || "Loading…"}</code></p>
      <div className="actions">
        <button disabled={blocked || !address.trim()} onClick={() => perform("Saving", async () => {
          const c = await api("/models/connection", "PUT", { address });
          setSavedAddress(c.address); setAddress(c.address); setConnection(null); setResult(null); await refresh();
        })}>Save connection</button>
        <button disabled={blocked || !savedAddress || address !== savedAddress} onClick={() => perform("Connecting", async () => {
          setConnection(null);
          const response = await fetch("/api/models/connection/test", { method: "POST" });
          const data = await response.json(); setConnection(data);
          if (!response.ok) throw new Error(data.detail);
          await refresh();
        })}>Test connection</button>
      </div>
      {connection && <p role="status">{connection.available ? `Connected to Ollama ${connection.version}` : "Connection failed"} · {seconds(connection.elapsed_seconds)}</p>}
      <p>For Docker on this computer, use <code>http://host.docker.internal:11434</code>. See README for server binding and container service configuration. In a remote development environment, localhost refers to that environment, not your computer.</p>
    </section>
    <section className="card">
      <h2>Install a local model</h2>
      <p>Browse complete model variants in the catalog. Only clicking Install downloads weights to the Ollama server. Installing an existing tag can update it; queued experiments retain their original digest and reject changes.</p>
      <button className="primary" disabled={blocked} onClick={() => setPickerOpen(true)}>Add model</button>
      <ModelPicker open={pickerOpen} close={() => setPickerOpen(false)} models={p.modelState.models} choose={(entry) => { setVariant(entry); setName(entry.name); setInstalled(false); setInstallFailed(false); setEvents([]); }} />
      {variant && <><VariantDetails entry={variant} models={p.modelState.models} /><button className="primary" disabled={blocked} onClick={() => perform("Installing", install)}>Install</button></>}
      <details><summary>Advanced / install by exact tag</summary>
        <p>For local models outside the curated catalog. An exact name and tag are required.</p>
        <label>Exact model name and tag<input disabled={blocked} value={name} onChange={(e) => { setName(e.target.value); setVariant(null); }} placeholder="model-name:tag" /></label>
        <button disabled={blocked || !name.trim()} onClick={() => perform("Installing", install)}>Install exact tag</button>
      </details>
      {events.length > 0 && <div aria-live="polite">
        <p>{installFailed ? "Installation failed" : installed ? "Installed and verified locally" : latest?.status === "success" ? "Verifying local installation…" : latest?.status}</p>
        {Object.entries(layers).map(([digest, layer]) => <div key={digest}>
          <small className="model-digest">{digest}</small>
          <progress aria-label={`Download ${digest}`} max={layer.total || 1} value={layer.completed || 0} />
          <span>{bytes(layer.completed || 0)} / {bytes(layer.total)} ({Math.min(100, Math.round(100 * (layer.completed || 0) / (layer.total || 1)))}%)</span>
        </div>)}
      </div>}
      {downloading && !error && !events.length && <p role="status">Connecting to Ollama to start download…</p>}
    </section>
    {error && <div className="alert error" role="alert">{error}</div>}
    <section className="card">
      <div className="section-title"><h2>Installed local models</h2><button disabled={blocked} onClick={() => perform("Refreshing", refresh)}>Refresh models</button></div>
      <p>Refreshed automatically every 15 seconds and after installation. These models are shared with scenario generation, revision, experiments, and the separate evaluator selector.</p>
      {p.modelState.error && <p className="warning">{p.modelState.error}</p>}
      {!p.modelState.models.length ? <p>No verified installed local models found.</p> : <div className="table-wrap"><table>
        <thead><tr><th>Name and artifact</th><th>Digest</th><th>Parameters</th><th>Quantization</th><th>Disk size</th></tr></thead>
        <tbody>{p.modelState.models.map((m: R) => <tr key={m.name}>
          <td>{m.name}<small>{m.identity?.family || "Family unknown"} · {m.identity?.variant || "Variant unknown"} · {m.identity?.finetune || ""} {m.identity?.artifact_version || ""} · think {JSON.stringify(m.thinking?.values ?? "unknown")}</small></td><td className="model-digest">{m.digest || "Unknown"}</td><td>{m.details?.parameter_size || "Unknown"}</td><td>{m.details?.quantization_level || "Unknown"}</td><td>{bytes(m.size)}</td>
        </tr>)}</tbody>
      </table></div>}
    </section>
    <section className="card">
      <h2>Short inference test</h2>
      <ModelSelect models={p.modelState.models} value={model} onChange={setModel} label="Model to test" />
      <button disabled={blocked || !p.modelState.models.some((m: R) => m.name === model)} onClick={() => perform("Testing inference", async () => {
        setResult(null);
        const response = await fetch("/api/models/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) });
        const data = await response.json(); setResult(data);
        if (!response.ok) throw new Error(data.detail || "Inference failed");
      })}>Test inference</button>
      {action === "Testing inference" && <p role="status">Running locally; first load may take up to 60 seconds…</p>}
      {result && <div role="status"><p>{result.success ? "Inference succeeded" : "Inference failed"} · {seconds(result.elapsed_seconds)}</p>
        {result.response && <pre>{result.response}</pre>}{result.kind && <p>Failure type: {result.kind}</p>}{result.http_status != null && <p>Ollama HTTP status: {result.http_status}</p>}{result.partial_text && <pre>{result.partial_text}</pre>}
        {result.runtime && <p>Model load: {seconds(result.runtime.load_duration == null ? null : result.runtime.load_duration / 1e9)} · Prompt evaluation: {seconds(result.runtime.prompt_eval_duration == null ? null : result.runtime.prompt_eval_duration / 1e9)} · Generation: {seconds(result.runtime.eval_duration == null ? null : result.runtime.eval_duration / 1e9)} · Stop reason: {result.runtime.done_reason || "Unknown"}</p>}
        {(result.effective_settings || result.diagnostics) && <details><summary>Inference test diagnostics and effective settings</summary><pre>{JSON.stringify(result.diagnostics || result.effective_settings, null, 2)}</pre></details>}
      </div>}
    </section>
  </>;
}
