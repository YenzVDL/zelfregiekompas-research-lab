import { useEffect, useState } from "react";
import { api } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import { Badge, Json, Empty, DatasetSelect, BankSelect } from "../components";

export function Snapshots(p: Common) {
  const [reviewMode, setReviewMode] = useState("approved");
  const [kind, setKind] = useState("dataset"),
    [ds, setDs] = useState(""),
    [name, setName] = useState(""),
    [selected, setSelected] = useState(""),
    [decision, setDecision] = useState("prompt"),
    [notes, setNotes] = useState(""),
    [influences, setInfluences] = useState<R[]>([]);
  useEffect(() => {
    api("/influences").then(setInfluences);
  }, [p.revision]);
  return (
    <>
      <p className="lead">
        Freeze inputs before an experiment. Regular snapshots require approved latest versions. Explicit experimental bank snapshots may include unreviewed questions for development only. Snapshot contents never change.
      </p>
      <section className="card">
        <h2>Create immutable snapshot</h2>
        <div className="form-grid">
          <label>
            Snapshot type
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="dataset">Dataset</option>
              <option value="bank">Question bank</option>
            </select>
          </label>
          {kind === "dataset" && (
            <DatasetSelect
              datasets={p.datasets}
              value={ds || p.datasetId}
              onChange={setDs}
            />
          )}
          {kind === "bank" && <><BankSelect banks={p.banks} value={p.bankId} onChange={p.setBankId} />
            <label>Bank snapshot review mode<select aria-label="Bank snapshot review mode" value={reviewMode} onChange={(e) => setReviewMode(e.target.value)}>
              <option value="approved">Approved only</option><option value="experimental">Experimental — unreviewed allowed, development only</option>
            </select></label>
          </>}
          <label>
            Snapshot name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Development review round 1"
            />
          </label>
        </div>
        <button
          className="primary"
          disabled={!name || p.busy || (kind === "bank" && !p.bankId)}
          onClick={() =>
            p.run(
              () =>
                api("/snapshots", "POST", {
                  kind,
                  name,
                  dataset_id: ds || p.datasetId,
                  bank_id: kind === "bank" ? p.bankId : undefined,
                  review_mode: kind === "bank" ? reviewMode : "approved",
                }),
              kind === "bank" && reviewMode === "experimental" ? "Experimental development snapshot frozen. Review statuses are retained; rejected questions are excluded." : "Immutable snapshot created from approved latest versions.",
            )
          }
        >
          {kind === "bank" && reviewMode === "experimental" ? "Freeze experimental bank" : "Freeze approved versions"}
        </button>
      </section>
      <section className="card">
        <h2>Frozen research inputs</h2>
        {!p.snapshots.length ? (
          <Empty>
            No snapshots yet. Approve scenarios and bank questions, then freeze
            them here.
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Records</th>
                <th>Exclusions</th>
                <th>Content hash</th>
              </tr>
            </thead>
            <tbody>
              {p.snapshots.map((s) => (
                <tr key={s.id}>
                  <td>
                    <b>{s.name}</b>
                    <small>{s.id}</small>
                    {s.frozen_test && <Badge>FROZEN TEST CONTENT</Badge>}
                    {s.kind === "bank" && <small>{s.bank_name || "Default question bank"}</small>}
                    {s.review_mode === "experimental" && <Badge>EXPERIMENTAL — DEVELOPMENT ONLY</Badge>}
                  </td>
                  <td>{s.kind}</td>
                  <td>{s.records.length}</td>
                  <td>{s.excluded_unapproved} excluded</td>
                  <td>
                    <code>{s.content_hash.slice(0, 16)}…</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="card">
        <h2>Record research influence</h2>
        <p>
          Record which snapshot informed prompt, rule or model decisions. Using
          test cases to tune a method limits the interpretation of later test
          results.
        </p>
        <div className="form-grid">
          <label>
            Snapshot
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">Choose snapshot</option>
              {p.snapshots.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Decision type
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
            >
              {["prompt", "rule", "model"].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Decision notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <button
          disabled={!selected || !notes || !p.reviewer || p.busy}
          onClick={() =>
            p.run(() =>
              api("/influences", "POST", {
                snapshot_id: selected,
                decision_type: decision,
                notes,
                researcher: p.reviewer,
              }),
            )
          }
        >
          Record influence
        </button>
        <details>
          <summary>Influence log</summary>
          <Json value={influences} />
        </details>
      </section>
    </>
  );
}
