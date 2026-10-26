import { useEffect, useState } from "react";
import { api, pretty } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import {
  Badge,
  Json,
  Editor,
  Empty,
  DatasetSelect,
  ModelSelect,
  InputTable,
  Comparison,
} from "../components";
import { blankInputs, scenarioTopics, scenarioVariations } from "../constants";
import { GenerationPrompt } from "./GenerationPrompt";

export function Scenarios(p: Common & { setDatasetId: (s: string) => void }) {
  const [rows, setRows] = useState<R[]>([]),
    [coverage, setCoverage] = useState<R>({}),
    [selected, setSelected] = useState<R | null>(null),
    [history, setHistory] = useState<R[]>([]),
    [reviews, setReviews] = useState<R[]>([]);
  const [search, setSearch] = useState(""),
    [status, setStatus] = useState(""),
    [tag, setTag] = useState("");
  const [mode, setMode] = useState("browse"),
    [name, setName] = useState(""),
    [fmt, setFmt] = useState("json"),
    [text, setText] = useState(""),
    [mapping, setMapping] = useState("{}"),
    [preview, setPreview] = useState<R | null>(null);
  const [edit, setEdit] = useState(""),
    [metadataEdit, setMetadataEdit] = useState("{}"),
    [feedback, setFeedback] = useState(""),
    [model, setModel] = useState("");
  const [category, setCategory] = useState("normal"),
    [count, setCount] = useState(1),
    [missing, setMissing] = useState(""),
    [split, setSplit] = useState("development");
  const [topics, setTopics] = useState(scenarioTopics.slice(0, 5));
  const [variations, setVariations] = useState(scenarioVariations);
  const [distribution, setDistribution] = useState("");
  const [loadError, setLoadError] = useState("");
  useEffect(() => {setSelected(null); setRows([]); setCoverage({});}, [p.datasetId]);
  useEffect(() => {
    let active = true;
    if (!p.datasetId) {
      setRows([]);
      setCoverage({});
      return;
    }
    const qs = new URLSearchParams({
      dataset_id: p.datasetId,
      search,
      status,
      tag,
    });
    Promise.all([api("/scenarios?" + qs), api("/datasets/" + p.datasetId + "/coverage"), api("/scenarios?history=true&dataset_id=" + p.datasetId)])
      .then(([cases, stats, versions]) => {if (active) {setRows(cases); setCoverage(stats); setHistory(versions); setLoadError("");}})
      .catch((error) => {if (active) setLoadError(error.message);});
    return () => {active = false;};
  }, [p.datasetId, p.revision, search, status, tag]);
  useEffect(() => {
    if (selected) api("/reviews?version_id=" + selected.id).then(setReviews);
  }, [selected, p.revision]);
  function choose(row: R) {
    setSelected(row);
    setEdit(pretty(row.inputs));
    setMetadataEdit(
      pretty({
        tags: row.tags,
        synthetic_origin: row.synthetic_origin,
        research_labels: row.research_labels,
        evaluation_notes: row.evaluation_notes,
      }),
    );
    setFeedback("");
  }
  const parent = selected?.parent_id
    ? history.find((r) => r.id === selected.parent_id)
    : null;
  return (
    <>
      {loadError && <p className="error-text">{loadError}</p>}
      <div className="toolbar">
        <DatasetSelect
          datasets={p.datasets}
          value={p.datasetId}
          onChange={(v) => {
            p.setDatasetId(v);
            setSelected(null);
          }}
        />
        <button onClick={() => setMode("create")}>+ New dataset</button>
        <button
          disabled={!p.datasetId}
          onClick={() => {
            setMode("import");
            setPreview(null);
          }}
        >
          Import scenarios
        </button>
        <button
          disabled={!p.datasetId}
          onClick={() => {
            setMode("new");
            setEdit(pretty(blankInputs));
            setSelected(null);
          }}
        >
          + Manual scenario
        </button>
        <button disabled={!p.datasetId} onClick={() => setMode("generate")}>
          Generate locally
        </button>
      </div>
      {mode === "create" && (
        <section className="card">
          <h2>Create dataset</h2>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <div className="actions">
            <button
              className="primary"
              onClick={() =>
                p.run(async () => {
                  const d = await api("/datasets", "POST", { name });
                  p.setDatasetId(d.id);
                  setMode("browse");
                })
              }
            >
              Create dataset
            </button>
            <button onClick={() => setMode("browse")}>Close</button>
          </div>
        </section>
      )}
      {mode === "import" && (
        <section className="card">
          <h2>Import synthetic scenarios</h2>
          <p>
            Preview structural errors, duplicates and content warnings before
            saving. Empty fields remain unknown. CSV nested objects use JSON
            cells.
          </p>
          <div className="row">
            <label>
              Format
              <select
                value={fmt}
                onChange={(e) => {
                  setFmt(e.target.value);
                  setPreview(null);
                }}
              >
                {["json", "jsonl", "csv"].map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </select>
            </label>
            <label>
              Read local file
              <input
                type="file"
                accept=".json,.jsonl,.csv"
                onChange={async (e) => {
                  if (e.target.files?.[0]) {
                    setText(await e.target.files[0].text());
                    setPreview(null);
                  }
                }}
              />
            </label>
          </div>
          <Editor
            label="Import content"
            value={text}
            set={(s) => {
              setText(s);
              setPreview(null);
            }}
          />
          {fmt === "csv" && (
            <Editor
              label={
                'CSV mapping JSON: target field → source column (empty = use headers). Example: {"inputs.learning_goal":"Goal"}'
              }
              value={mapping}
              set={(s) => {
                setMapping(s);
                setPreview(null);
              }}
              rows={4}
            />
          )}
          <div className="actions">
            <button
              onClick={() =>
                p.run(
                  async () =>
                    setPreview(
                      await api(
                        `/datasets/${p.datasetId}/import/preview`,
                        "POST",
                        { format: fmt, text, mapping: JSON.parse(mapping) },
                      ),
                    ),
                  "Preview ready; no records saved.",
                )
              }
            >
              Preview import
            </button>
            <button
              className="primary"
              disabled={!preview || !!preview.invalid || p.busy}
              onClick={() =>
                p.run(async () => {
                  await api(`/datasets/${p.datasetId}/import/commit`, "POST", {
                    format: fmt,
                    text,
                    mapping: JSON.parse(mapping),
                  });
                  setMode("browse");
                }, "Scenarios imported. Human review is required.")
              }
            >
              Commit validated rows
            </button>
            <button onClick={() => setMode("browse")}>Close</button>
          </div>
          {preview && (
            <>
              <p>
                <b>
                  {preview.valid} valid · {preview.invalid} invalid
                </b>
              </p>
              {preview.rows.map((r: R) => (
                <div className="import-row" key={r.row}>
                  <b>Row {r.row}</b>
                  {r.errors.length ? (
                    <div className="error-text">{r.errors.join("\n")}</div>
                  ) : (
                    <Badge status="approved">Structurally valid</Badge>
                  )}
                  {r.warnings.map((w: string, i: number) => (
                    <p key={i}>{w}</p>
                  ))}
                </div>
              ))}
            </>
          )}
        </section>
      )}
      {mode === "generate" && (
        <section className="card">
          <h2>Targeted local synthetic generation</h2>
          <fieldset><legend>ICT topics</legend>{scenarioTopics.map((topic) => <label className="check" key={topic}><input type="checkbox" checked={topics.includes(topic)} onChange={() => setTopics(topics.includes(topic) ? topics.filter((t) => t !== topic) : [...topics, topic])} />{topic}</label>)}</fieldset>
          <fieldset><legend>Scenario variations</legend>{scenarioVariations.map((variation) => <label className="check" key={variation}><input type="checkbox" checked={variations.includes(variation)} onChange={() => setVariations(variations.includes(variation) ? variations.filter((v) => v !== variation) : [...variations, variation])} />{variation}</label>)}</fieldset>
          <p>{count} requested · balanced topic × variation plan. Each item receives one explicit assignment; 30 cases across 5 topics and 6 variations cover all combinations.</p>
          <details><summary>Custom distribution</summary><label>Distribution JSON (optional)<textarea aria-label="Scenario distribution JSON" placeholder={'[{"topic":"databases and SQL","variation":"partial completion","count":3}]'} value={distribution} onChange={(e) => setDistribution(e.target.value)} /></label><p>Rows contain topic, variation and count; counts must equal the requested total. Leave empty for the balanced plan.</p></details>
          <div className="form-grid">
            <ModelSelect
              models={p.modelState.models}
              value={model}
              onChange={setModel}
            />
            <label>
              Category
              <input
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
            </label>
            <label>
              Count
              <input
                type="number"
                min={1}
                max={100}
                value={count}
                onChange={(e) => setCount(+e.target.value)}
              />
            </label>
            <label>
              Split
              <select value={split} onChange={(e) => setSplit(e.target.value)}>
                {["development", "validation", "test"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="full">
              Missing input fields (comma separated)
              <input
                value={missing}
                placeholder="progress, control_results"
                onChange={(e) => setMissing(e.target.value)}
              />
            </label>
          </div>
          <div className="actions">
            <button
              className="primary"
              disabled={!model || p.busy || !topics.length || !variations.length}
              onClick={() =>
                p.run(
                  () =>
                    api("/scenario-tasks/generation", "POST", {
                      dataset_id: p.datasetId,
                      model,
                      category,
                      count,
                      topics,
                      variations,
                      ...(distribution.trim() ? { distribution: JSON.parse(distribution) } : {}),
                      split,
                      missing_fields: missing
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    }),
                  "Generation queued. Follow progress in Experiments; results enter the review queue.",
                )
              }
            >
              Queue generation
            </button>
            <button onClick={() => setMode("browse")}>Close</button>
          </div>
        </section>
      )}
      {mode === "new" && (
        <section className="card">
          <h2>Create synthetic scenario</h2>
          <Editor
            label="Model inputs (JSON; null = unknown)"
            value={edit}
            set={setEdit}
          />
          <div className="row">
            <label>
              Category
              <input
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
            </label>
            <label>
              Split
              <select value={split} onChange={(e) => setSplit(e.target.value)}>
                {["development", "validation", "test"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="actions">
            <button
              className="primary"
              onClick={() =>
                p.run(async () => {
                  const r = await api("/scenarios", "POST", {
                    dataset_id: p.datasetId,
                    row: { inputs: JSON.parse(edit), tags: [category], split },
                  });
                  choose(r);
                  setMode("browse");
                })
              }
            >
              Save for review
            </button>
            <button onClick={() => setMode("browse")}>Close</button>
          </div>
        </section>
      )}
      {p.datasetId && (
        <>
          <div className="mini-stats">
            <span>
              <b>{coverage.total || 0}</b> latest scenarios
            </span>
            {Object.entries(coverage.review_status || {}).map(([k, v]) => (
              <span key={k}>
                <b>{String(v)}</b> {k.replaceAll("_", " ")}
              </span>
            ))}
          </div>
          <details className="card">
            <summary>
              Coverage by category, split and missing-data pattern
            </summary>
            <Json value={coverage} />
          </details>
          <div className="toolbar">
            <input
              aria-label="Search scenarios"
              placeholder="Search scenarios…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              aria-label="Review status"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">All review states</option>
              <option value="awaiting_review">Review queue</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
            </select>
            <input
              aria-label="Category filter"
              placeholder="Category tag…"
              value={tag}
              onChange={(e) => setTag(e.target.value)}
            />
            <a className="button" href={`/api/datasets/${p.datasetId}/export?include_provenance=true`}>
              Export JSON
            </a>
            <a
              className="button"
              href={`/api/datasets/${p.datasetId}/export?format=jsonl`}
            >
              JSONL
            </a>
            <a
              className="button"
              href={`/api/datasets/${p.datasetId}/export?format=csv`}
            >
              CSV
            </a>
          </div>
        </>
      )}
      <div className="split-view">
        <section className="card list-panel">
          <div className="section-title">
            <h2>Scenario review queue</h2>
            <span>{rows.length} cases</span>
          </div>
          {!rows.length && (
            <Empty>Create a dataset or import synthetic cases to begin.</Empty>
          )}
          {rows.map((r) => (
            <button
              className={
                "list-item " + (selected?.id === r.id ? "selected" : "")
              }
              key={r.id}
              onClick={() => choose(r)}
            >
              <span className="row spread">
                <b>{r.inputs.learning_goal || "Unknown learning goal"}</b>
                <Badge status={r.review_status}>
                  {r.review_status.replaceAll("_", " ")}
                </Badge>
              </span>
              <small>
                {r.scenario_id} · v{r.version} · {r.split}
              </small>
              <span className="tags">{r.tags.join(" · ")}</span>
            </button>
          ))}
        </section>
        <section className="card detail-panel">
          {!selected ? (
            <Empty>
              Select a scenario to review inputs, compare versions or request a
              revision.
            </Empty>
          ) : (
            <>
              <div className="section-title">
                <h2>Review exact version {selected.version}</h2>
                <Badge status={selected.review_status}>
                  {selected.review_status.replaceAll("_", " ")}
                </Badge>
              </div>
              <small>
                {selected.id} · Family {selected.family_id}
              </small>
              <p className="note">{selected.synthetic_origin}</p>
              {selected.warnings.map((w: string) => (
                <div className="warning" key={w}>
                  {w}
                </div>
              ))}
              <InputTable inputs={selected.inputs} />
              <GenerationPrompt key={selected.id} scenario={selected} />
              <div className="row">
                <label>
                  Version history
                  <select
                    aria-label="Scenario version history"
                    value={selected.id}
                    onChange={(e) =>
                      choose(history.find((r) => r.id === e.target.value)!)
                    }
                  >
                    {history
                      .filter((r) => r.scenario_id === selected.scenario_id)
                      .map((r) => (
                        <option key={r.id} value={r.id}>
                          Version {r.version} · {r.review_status}
                        </option>
                      ))}
                  </select>
                </label>
              </div>
              {parent && (
                <Comparison before={parent.inputs} after={selected.inputs} />
              )}
              <label>
                Reviewer feedback
                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="Explain the decision or requested changes"
                />
              </label>
              <div className="actions">
                {["approved", "rejected"].map((decision) => (
                  <button
                    key={decision}
                    className={decision === "approved" ? "primary" : ""}
                    disabled={p.busy || !p.reviewer}
                    onClick={() =>
                      p.run(async () => {
                        await api(`/scenarios/${selected.id}/review`, "POST", {
                          decision,
                          reviewer: p.reviewer,
                          feedback,
                        });
                        setSelected({ ...selected, review_status: decision });
                      }, `Decision saved for version ${selected.version}.`)
                    }
                  >
                    {decision === "approved"
                      ? "Approve exact version"
                      : "Reject exact version"}
                  </button>
                ))}
              </div>
              {!p.reviewer && (
                <small>
                  Enter a reviewer ID in the header to record a decision.
                </small>
              )}
              <details>
                <summary>
                  Edit manually · creates a new unapproved version
                </summary>
                <Editor
                  label="Candidate model inputs"
                  value={edit}
                  set={setEdit}
                />
                <Editor
                  label="Candidate research metadata (excluded from prompts)"
                  value={metadataEdit}
                  set={setMetadataEdit}
                  rows={7}
                />
                <button
                  disabled={p.busy}
                  onClick={() =>
                    p.run(
                      async () =>
                        choose(
                          await api(
                            `/scenarios/${selected.id}/revise`,
                            "POST",
                            {
                              inputs: JSON.parse(edit),
                              ...JSON.parse(metadataEdit),
                            },
                          ),
                        ),
                      "New candidate saved. Its parent is preserved; human approval is required.",
                    )
                  }
                >
                  Save new version
                </button>
              </details>
              <details>
                <summary>Request an AI revision</summary>
                <p>
                  The local model receives the current inputs and feedback. The
                  new candidate cannot approve itself.
                </p>
                <ModelSelect
                  models={p.modelState.models}
                  value={model}
                  onChange={setModel}
                />
                <button
                  disabled={!model || !feedback || p.busy}
                  onClick={() =>
                    p.run(
                      () =>
                        api("/scenario-tasks/revision", "POST", {
                          version_id: selected.id,
                          feedback,
                          model,
                        }),
                      "Local revision queued. Follow progress in Experiments; then select the new version here.",
                    )
                  }
                >
                  Queue AI revision with feedback
                </button>
              </details>
              <details>
                <summary>
                  Research metadata & provenance (excluded from question
                  prompts)
                </summary>
                <Json
                  value={{
                    family_id: selected.family_id,
                    split: selected.split,
                    tags: selected.tags,
                    research_labels: selected.research_labels,
                    evaluation_notes: selected.evaluation_notes,
                    generation: selected.generation,
                  }}
                />
              </details>
              <details>
                <summary>Decision history</summary>
                <Json value={reviews} />
              </details>
            </>
          )}
        </section>
      </div>
    </>
  );
}
