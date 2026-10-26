import { useEffect, useState } from "react";
import { api, rate, seconds } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import { Badge, Json, Editor, Empty } from "../components";

export function Metrics(p: Common) {
  const [jobId, setJobId] = useState(""),
    [data, setData] = useState<R | null>(null),
    [source, setSource] = useState("human"),
    [thresholds, setThresholds] = useState("{}"),
    [reason, setReason] = useState("");
  useEffect(() => {
    if (jobId) api(`/jobs/${jobId}/metrics`).then(setData);
  }, [jobId, p.revision]);
  const quality = data?.quality[source].overall;
  return (
    <>
      <div className="toolbar">
        <label>
          Experiment
          <select value={jobId} onChange={(e) => setJobId(e.target.value)}>
            <option value="">Select experiment</option>
            {p.jobs
              .filter((j) => j.kind === "experiment")
              .map((j) => (
                <option key={j.id} value={j.id}>
                  {j.name} · {j.id.slice(-6)}
                </option>
              ))}
          </select>
        </label>
        <label>
          Question-quality reference
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="human">Human reference judgments</option>
            <option value="ai">Local AI judgments</option>
          </select>
        </label>
        {jobId && (
          <a className="button" href={`/api/jobs/${jobId}/export`}>
            Export results & metrics
          </a>
        )}
      </div>
      {!data ? (
        <Empty>
          Select an experiment to inspect quality, failures, evaluator agreement
          and feasibility.
        </Empty>
      ) : (
        <>
          {data.bank_snapshot && <p className={data.bank_snapshot.review_mode === "experimental" ? "warning" : "note"}>
            Bank: {data.bank_snapshot.bank_name} · {data.bank_snapshot.snapshot_id} · {data.bank_snapshot.review_mode === "experimental" ? "EXPERIMENTAL — development only" : "Approved only"} · <code>{data.bank_snapshot.content_hash}</code>
          </p>}
          <div className="stats">
            {[
              [
                rate(quality.criteria.usefulness.yes_rate_assessed),
                "Useful",
                `${quality.criteria.usefulness.assessed} assessed`,
              ],
              [
                rate(quality.criteria.correctness.yes_rate_assessed),
                "Contextually correct",
                `${quality.criteria.correctness.assessed} assessed`,
              ],
              [
                rate(quality.both_rate_assessed),
                "Pass both criteria",
                `${quality.both_assessed} jointly assessed`,
              ],
              [
                rate(quality.end_to_end_acceptance),
                "End-to-end acceptance",
                `${quality.end_to_end_denominator} attempted cases`,
              ],
            ].map(([n, t, s]) => (
              <div className="stat" key={t}>
                <strong>{n}</strong>
                <b>{t}</b>
                <small>{s}</small>
              </div>
            ))}
          </div>
          <div className="note">
            Rates above use assessed outputs. End-to-end acceptance counts valid
            questions passing both criteria against every attempted case,
            including failures and unreviewed outputs. Pending cases are
            reported separately. No winner is inferred.
          </div>
          <section className="card">
            <h2>Coverage & failures</h2>
            {data.completion && <p><b>{data.completion.valid_outputs}/{data.planned} valid outputs</b> · {data.completion.execution_failures} execution failures · processing {data.completion.processing_status}. Processing finished is distinct from successful generation.</p>}
            {quality.assessment_status === "Not assessed" && <p className="note">{source === "human" ? "Human quality is not assessed; there are no jointly assessed valid outputs." : "AI quality is not assessed."}</p>}
            <p>
              {data.planned} planned · {data.attempted} attempted ·{" "}
              {data.not_attempted} not attempted
            </p>
            <div className="row">
              {Object.entries(data.statuses).map(([k, v]) => (
                <Badge key={k} status={k}>
                  {k}: {String(v)}
                </Badge>
              ))}
            </div>
            <p>
              Usefulness review coverage:{" "}
              {rate(quality.criteria.usefulness.review_coverage)} · Correctness
              review coverage:{" "}
              {rate(quality.criteria.correctness.review_coverage)}
            </p>
            <details>
              <summary>Quality by method / model and scenario category</summary>
              <Json
                value={{
                  by_condition: data.quality[source].by_condition,
                  by_category: data.quality[source].by_category,
                  failure_analysis: data.failure_analysis,
                }}
              />
            </details>
          </section>
          <details className="card"><summary>Selection and retrieval concentration diagnostics</summary><Json value={data.concentration} /><Json value={data.diagnostic_failure_reasons} /><p>Low diversity alone does not establish a failure; inspect applicability, context and relevance.</p></details>
          <section className="card">
            <h2>AI evaluator agreement with human reference labels</h2>
            <p>
              These metrics describe evaluator agreement, not question quality
              or educational benefit. Reference = adjudication when present,
              otherwise primary human review; pair by exact output ID and
              version.
            </p>
            {["usefulness", "correctness"].map((c) => {
              const a = data.evaluator_accuracy[c];
              return (
                <div key={c}>
                  <h3>
                    {c === "correctness"
                      ? "Contextual correctness"
                      : "Usefulness"}
                  </h3>
                  {!a.paired_count ? (
                    <p className="note">{a.status}</p>
                  ) : (
                    <>
                      <p>
                        Paired judgments: {a.paired_count} ·{" "}
                        <b>
                          Macro-F1 for fixed Yes/No classes:{" "}
                          {a.macro_f1.toFixed(3)}
                        </b>
                      </p>
                      <table>
                        <thead>
                          <tr>
                            <th>Class</th>
                            <th>Precision</th>
                            <th>Recall</th>
                            <th>F1</th>
                            <th>Support</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(a.classes).map(([cls, vals]) => {
                            const v = vals as R;
                            return (
                              <tr key={cls}>
                                <td>{cls}</td>
                                <td>{v.precision.toFixed(3)}</td>
                                <td>{v.recall.toFixed(3)}</td>
                                <td>{v.f1.toFixed(3)}</td>
                                <td>{v.support}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      <p>Confusion matrix · rows: human, columns: AI</p>
                      <table>
                        <thead>
                          <tr>
                            <th>Human / AI</th>
                            <th>Yes</th>
                            <th>No</th>
                          </tr>
                        </thead>
                        <tbody>
                          {["Yes", "No"].map((cls) => (
                            <tr key={cls}>
                              <th>{cls}</th>
                              <td>{a.confusion_matrix[cls].Yes}</td>
                              <td>{a.confusion_matrix[cls].No}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="warning">
                        {a.caution ||
                          "Interpret agreement within this sample only."}
                      </p>
                      <small>{a.zero_division}</small>
                    </>
                  )}
                </div>
              );
            })}
          </section>
          <section className="card">
            <h2>Feasibility & resource measurements</h2>
            <div className="mini-stats">
              <span>
                Generation mean{" "}
                <b>{seconds(data.feasibility.generation_mean_seconds)}</b>
              </span>
              <span>
                Active experiment time{" "}
                <b>{seconds(data.feasibility.experiment_active_seconds)}</b>
              </span>
              <span>
                AI evaluation time{" "}
                <b>{seconds(data.feasibility.ai_evaluation_total_seconds)}</b>
              </span>
              <span>
                Memory <b>Not measured</b>
              </span>
            </div>
            <p>
              Throughput:{" "}
              {data.feasibility.throughput_completed_per_second == null
                ? "Not measured"
                : data.feasibility.throughput_completed_per_second.toFixed(3) +
                  " completed cases / second"}
            </p>
            <details>
              <summary>Hardware, warm-up, thresholds and history</summary>
              <Json value={data.feasibility} />
            </details>
            <details>
              <summary>Record a retrospective threshold change</summary>
              <Editor
                label="Thresholds JSON"
                value={thresholds}
                set={setThresholds}
                rows={4}
              />
              <label>
                Reason
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                disabled={!p.reviewer || !reason || p.busy}
                onClick={() =>
                  p.run(
                    () =>
                      api(`/jobs/${jobId}/thresholds`, "PUT", {
                        thresholds: JSON.parse(thresholds),
                        reason,
                        researcher: p.reviewer,
                      }),
                    "Threshold change recorded and flagged as retrospective.",
                  )
                }
              >
                Record threshold change
              </button>
            </details>
          </section>
          <section className="card">
            <h2>Second-review disagreements & adjudication</h2>
            {!data.disagreements.length ? (
              <p>No disagreements recorded for assessed reviewer pairs.</p>
            ) : (
              <Json value={data.disagreements} />
            )}
          </section>
        </>
      )}
    </>
  );
}
