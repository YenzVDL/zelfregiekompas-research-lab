import { useEffect, useRef, useState } from "react";
import { api, type RecordData as R } from "../api";

export const catalogSize = (entry: R) => entry.reported_size || (entry.size_bytes == null ? "Unknown" : `${(entry.size_bytes / 1e9).toFixed(2)} GB`);

export function VariantDetails({ entry, models }: { entry: R; models: R[] }) {
  const installed = models.find((m) => m.name === entry.name);
  return <div className="variant-details">
    <h3>{entry.name}</h3>
    <dl>
      <dt>Family</dt><dd>{entry.family || "Unknown"}</dd>
      <dt>Generation / version</dt><dd>{entry.generation || "Unknown"}</dd>
      <dt>Reported parameters</dt><dd>{entry.parameter_size || "Unknown"}</dd>
      <dt>Quantization</dt><dd>{entry.quantization || "Unknown"}</dd>
      <dt>Reported disk / download size</dt><dd>{catalogSize(entry)} (not RAM/VRAM)</dd>
      <dt>Known capabilities</dt><dd>{entry.capabilities?.join(", ") || "Unknown"}</dd>
      <dt>Installed artifact digest</dt><dd className="model-digest">{installed ? installed.digest || "Unknown" : "Not installed"}</dd>
      <dt>Public source digest prefix</dt><dd>{entry.source_digest_prefix || "Unknown"} (metadata only)</dd>
      <dt>Metadata checked</dt><dd>{entry.retrieved_at || "Unknown"}</dd>
    </dl>
    <a href={entry.source_url || `https://ollama.com/library/${entry.name}`} target="_blank" rel="noreferrer">Ollama source for {entry.name}</a>
    {entry.last_error && <p className="warning">Metadata refresh failed: {entry.last_error}. Showing cached information.</p>}
    <p>Tags may change at the source. Installation verifies the downloaded local artifact; experiments pin its full digest.</p>
  </div>;
}

export function ModelPicker({ open, close, choose, models }: { open: boolean; close: () => void; choose: (entry: R) => void; models: R[] }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const [catalog, setCatalog] = useState<R | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({ family: "", generation: "", parameter_size: "", quantization: "" });
  const [selected, setSelected] = useState<R | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (open) { dialog.current?.showModal(); searchInput.current?.focus(); }
    else if (dialog.current?.open) dialog.current.close();
  }, [open]);
  useEffect(() => { setSelected(null); }, [search, filters]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const query = new URLSearchParams({ search, ...filters });
        const response = await fetch("/api/model-catalog?" + query, { signal: controller.signal });
        if (!response.ok) throw new Error("Could not read the cached model catalog");
        setCatalog(await response.json());
      } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }, 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, search, filters, revision]);
  const labels: Record<string, string> = { family: "Family", generation: "Generation / version", parameter_size: "Reported parameters", quantization: "Quantization" };
  return <dialog ref={dialog} className="model-picker" aria-labelledby="model-picker-title" onCancel={(e) => { e.preventDefault(); close(); }}>
    <div className="section-title"><h2 id="model-picker-title">Add model</h2><button onClick={close} aria-label="Close model picker">Close</button></div>
    <p>{catalog?.scope || "Loading the cached curated catalog…"}</p>
    <p>Browsing uses cached public metadata. Installation requires internet access from Ollama. Hardware compatibility is not assessed.</p>
    <label>Search model catalog<input ref={searchInput} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search family, version, size or tag" /></label>
    <div className="form-grid catalog-filters">{Object.entries(labels).map(([key, label]) => <label key={key}>{label}
      <select value={filters[key]} onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}>
        <option value="">All</option>{(catalog?.facets?.[key] || []).map((value: string) => <option key={value}>{value}</option>)}
      </select>
    </label>)}</div>
    <div className="actions"><button onClick={() => { setSearch(""); setFilters({ family: "", generation: "", parameter_size: "", quantization: "" }); }}>Clear filters</button>
      <button disabled={refreshing} onClick={async () => {
        setRefreshing(true); setError("");
        try {
          const updated = await api("/model-catalog/refresh", "POST");
          setSelected(null);
          if (updated.refresh_errors) setError(`${updated.refresh_errors} source pages could not be refreshed. Cached entries remain available.`);
          setRevision((v) => v + 1);
        } catch (e) { setError(`Catalog refresh failed. Cached entries remain available. ${(e as Error).message}`); }
        finally { setRefreshing(false); }
      }}>Refresh catalog metadata</button>
    </div>
    <p role="status">{refreshing ? "Refreshing public metadata…" : loading ? "Searching cached entries…" : `${catalog?.entries?.length || 0} of ${catalog?.total || 0} curated variants`}</p>
    {catalog?.last_refresh_attempt && <p>Last refresh attempt: {catalog.last_refresh_attempt} · {catalog.refresh_errors || 0} source errors</p>}
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="catalog-layout">
      <ul className="catalog-results" aria-label="Installable model variants">{catalog?.entries?.map((entry: R) => <li key={entry.name}>
        <button disabled={loading || refreshing} aria-pressed={selected?.name === entry.name} onClick={() => setSelected(entry)}>{entry.name}</button>
        <p>{entry.family} · Generation {entry.generation} · {entry.parameter_size || "Unknown parameters"} · {entry.quantization || "Unknown quantization"} · {catalogSize(entry)}</p>
        <p>{entry.capabilities?.join(", ") || "Capabilities unknown"} · {models.some((m) => m.name === entry.name) ? "Already installed" : "Not installed"}</p>
        <small>Checked: {entry.retrieved_at || "Unknown"}{entry.last_error ? " · Cached; refresh failed" : ""}</small>
        <a href={entry.source_url || `https://ollama.com/library/${entry.name}`} target="_blank" rel="noreferrer">Source</a>
      </li>)}</ul>
      <section aria-label="Selected variant details">
        {selected ? <VariantDetails entry={selected} models={models} /> : <p>Select a complete variant to inspect it. Selection does not download anything.</p>}
      </section>
    </div>
    {!loading && catalog?.entries?.length === 0 && <p>No catalog entries match these filters. Clear filters or use Advanced / install by exact tag on Models.</p>}
    <div className="catalog-footer"><button className="primary" disabled={!selected || loading || refreshing} onClick={() => { if (selected) { choose(selected); close(); } }}>Use this variant</button></div>
  </dialog>;
}
