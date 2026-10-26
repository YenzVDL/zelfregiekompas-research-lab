import { useEffect, useState } from "react";
import { api } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import { Json } from "../components";

export function Protocol(p: Common & { setModelState: (s: R) => void }) {
  const [protocol, setProtocol] = useState<R>({}),
    [model, setModel] = useState(""),
    [parameters, setParameters] = useState(""),
    [provenance, setProvenance] = useState("");
  const [kind, setKind] = useState("datasets"),
    [identifier, setIdentifier] = useState(""),
    [plan, setPlan] = useState<R | null>(null),
    [cascade, setCascade] = useState(false);
  useEffect(() => {
    api("/protocol").then(setProtocol);
  }, []);
  return (
    <>
      <section className="card">
        <h2>Inspectable methods & prompts</h2>
        <Json value={protocol} />
      </section>
      <section className="card">
        <h2>Local model discovery</h2>
        <p>
          Only installed models verified as local are offered. Remote/cloud
          Ollama targets and unverifiable models are excluded. Model size is
          never inferred from a name.
        </p>
        <button
          onClick={() =>
            p.run(
              async () => p.setModelState(await api("/models")),
              "Local model discovery refreshed.",
            )
          }
        >
          Refresh installed models
        </button>
        {p.modelState.error && <p className="warning">{p.modelState.error}</p>}
        <details>
          <summary>Provider-reported identities and metadata</summary>
          <Json value={p.modelState} />
        </details>
        <div className="form-grid">
          <label>
            Model identity
            <input value={model} onChange={(e) => setModel(e.target.value)} />
          </label>
          <label>
            Manual parameter count
            <input
              value={parameters}
              onChange={(e) => setParameters(e.target.value)}
              placeholder="Value from a documented source"
            />
          </label>
          <label className="full">
            Provenance for manual parameter count
            <input
              value={provenance}
              onChange={(e) => setProvenance(e.target.value)}
              placeholder="Source and how it was established"
            />
          </label>
        </div>
        <button
          onClick={() =>
            p.run(() =>
              api("/models/metadata", "POST", {
                model,
                parameter_count: parameters,
                provenance,
              }),
            )
          }
        >
          Record manual model metadata
        </button>
      </section>
      <section className="card">
        <h2>Responsible AI & research limits</h2>
        <ul>
          <li>
            Synthetic cases explore coverage and failure modes; they do not
            establish representativeness or improved learning.
          </li>
          <li>
            Student notes and all scenario text are untrusted data. Embedded
            instructions are robustness cases. Prompt defenses reduce risk
            without guaranteeing resistance.
          </li>
          <li>
            Local inference receives only permitted inputs. The local Ollama
            process receives prompts; this API and SQLite store research
            records. The browser displays them. No external AI APIs are used.
          </li>
          <li>
            Preserve student agency. Outputs are reflection suggestions, not
            diagnoses or authoritative judgments about ability.
          </li>
          <li>
            Scenario generation, question generation and evaluation are separate
            roles. Reusing a model can cause correlated bias. Review provenance
            and category-level failures.
          </li>
          <li>
            No feature proves legal compliance or ethical validity. Human review
            remains necessary.
          </li>
        </ul>
        <a className="button" href="/api/export">
          Export all local research records
        </a>
        <a className="button" href="/docs" target="_blank" rel="noreferrer">
          Open API reference
        </a>
      </section>
      <section className="card">
        <h2>Delete local records with dependencies</h2>
        <p>
          Preview the exact records first. Deleting a scenario or question
          removes its version family and dependent snapshots, experiments,
          outputs and labels. Export first if you need a research archive.
          Active jobs must stop before deletion.
        </p>
        <div className="form-grid">
          <label>
            Record type
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setPlan(null);
              }}
            >
              {["datasets", "scenarios", "questions", "snapshots", "jobs"].map(
                (s) => (
                  <option key={s}>{s}</option>
                ),
              )}
            </select>
          </label>
          <label>
            Record ID
            <input
              value={identifier}
              onChange={(e) => {
                setIdentifier(e.target.value);
                setPlan(null);
              }}
            />
          </label>
        </div>
        <button
          disabled={!identifier}
          onClick={() =>
            p.run(
              async () => setPlan(await api(`/delete/${kind}/${identifier}`)),
              "Deletion dependency preview ready; nothing deleted.",
            )
          }
        >
          Preview dependencies
        </button>
        {plan && (
          <>
            <Json value={plan} />
            <label className="check">
              <input
                type="checkbox"
                checked={cascade}
                onChange={(e) => setCascade(e.target.checked)}
              />
              Delete this record and all listed dependencies permanently
            </label>
            <button
              className="danger"
              disabled={!cascade || p.busy}
              onClick={() =>
                p.run(async () => {
                  await api(
                    `/delete/${kind}/${identifier}?cascade=true`,
                    "DELETE",
                  );
                  setPlan(null);
                  setIdentifier("");
                }, "Listed records deleted from the local database.")
              }
            >
              Delete listed records
            </button>
          </>
        )}
      </section>
    </>
  );
}
