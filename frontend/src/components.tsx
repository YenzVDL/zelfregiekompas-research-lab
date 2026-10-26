import React, { type ReactNode } from "react";
import { pretty, type RecordData as R } from "./api";
export function Badge({
  children,
  status = "",
}: {
  children: ReactNode;
  status?: string;
}) {
  return <span className={"badge " + status}>{children}</span>;
}

export function Json({ value }: { value: unknown }) {
  return <pre>{pretty(value)}</pre>;
}

export function Editor({
  label,
  value,
  set,
  rows = 12,
}: {
  label: string;
  value: string;
  set: (s: string) => void;
  rows?: number;
}) {
  return (
    <label className="full">
      {label}
      <textarea
        className="code"
        value={value}
        rows={rows}
        onChange={(e) => set(e.target.value)}
        spellCheck={false}
      />
    </label>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function DatasetSelect({
  datasets,
  value,
  onChange,
}: {
  datasets: R[];
  value: string;
  onChange: (s: string) => void;
}) {
  return (
    <label>
      Dataset
      <select
        aria-label="Dataset"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Select a dataset</option>
        {datasets.map((d) => (
          <option key={d.id} value={d.id}>
            {d.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ModelSelect({
  models,
  value,
  onChange,
  label = "Local model",
}: {
  models: R[];
  value: string;
  onChange: (s: string) => void;
  label?: string;
}) {
  return (
    <label>
      {label}
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Select an installed model</option>
        {models.map((m) => (
          <option key={m.name}>{m.name}</option>
        ))}
      </select>
      {models.find((m) => m.name === value)?.identity && <small>{models.find((m) => m.name === value)?.identity.variant} · artifact {models.find((m) => m.name === value)?.identity.finetune || "unspecified"} {models.find((m) => m.name === value)?.identity.artifact_version || ""} · thinking controls {JSON.stringify(models.find((m) => m.name === value)?.thinking?.values ?? "unknown")}</small>}
    </label>
  );
}

export function BankSelect({ banks, value, onChange }: { banks: R[]; value: string; onChange: (id: string) => void }) {
  return <label>Question bank<select aria-label="Question bank" value={value} onChange={(event) => onChange(event.target.value)}>
    <option value="">Select a bank</option>{banks.map((bank) => <option key={bank.id} value={bank.id}>{bank.name}</option>)}
  </select></label>;
}

export function InputTable({ inputs }: { inputs: R }) {
  return (
    <dl className="input-table">
      {Object.entries(inputs).map(([k, v]) => (
        <React.Fragment key={k}>
          <dt>{k.replaceAll("_", " ")}</dt>
          <dd>
            {v == null || v === "" ? (
              <span className="unknown">Unknown</span>
            ) : typeof v === "object" ? (
              <pre>{pretty(v)}</pre>
            ) : (
              String(v)
            )}
          </dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function Comparison({ before, after }: { before: R; after: R }) {
  return (
    <details open>
      <summary>Parent → candidate · highlighted changes</summary>
      <table className="diff">
        <thead>
          <tr>
            <th>Input</th>
            <th>Parent</th>
            <th>Candidate</th>
          </tr>
        </thead>
        <tbody>
          {Object.keys({ ...before, ...after }).map((k) => (
            <tr
              className={
                pretty(before[k]) !== pretty(after[k]) ? "changed" : ""
              }
              key={k}
            >
              <th>{k}</th>
              <td>{pretty(before[k])}</td>
              <td>{pretty(after[k])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
