import { api } from "../api";
import type { RecordData as R } from "../api";
import type { Common } from "../types";
import { Badge } from "../components";

export function Overview(p: Common & { setPage: (s: string) => void }) {
  return (
    <>
      <div className="intro">
        <div>
          <span className="eyebrow">FROM SCENARIO TO EVIDENCE</span>
          <h2>
            A workspace for better
            <br />
            reflection questions.
          </h2>
          <p>
            Build your evidence step by step. Review synthetic cases,
            <br />
            compare three methods, and keep every decision traceable.
          </p>
          <button className="primary" onClick={() => p.setPage("Scenarios")}>
            Open scenarios <span>→</span>
          </button>
        </div>
        <div className="orbit" aria-hidden="true">
          <div className="orbit-inner">
            ✳<small>REFLECT · REVIEW · REFINE</small>
          </div>
          <span className="orbit-label">
            Student agency
            <br />
            at the center
          </span>
        </div>
      </div>
      <div className="stats">
        {[
          [p.datasets.length, "Datasets", "Synthetic research cases"],
          [
            p.questions.filter((q) => q.review_status === "approved").length,
            "Approved questions",
            "Human-reviewed bank versions",
          ],
          [p.snapshots.length, "Frozen snapshots", "Stable experiment inputs"],
          [
            p.jobs.filter((j) => j.kind === "experiment").length,
            "Experiments",
            "Persistent local execution",
          ],
        ].map(([n, t, s]) => (
          <div className="stat" key={t}>
            <strong>{n}</strong>
            <b>{t}</b>
            <small>{s}</small>
          </div>
        ))}
      </div>
      <div className="section-title">
        <h2>Your research workflow</h2>
        <Badge>NO WINNING METHOD ASSUMED</Badge>
      </div>
      <div className="steps">
        {[
          [
            "01",
            "Build & review",
            "Import, inspect and revise synthetic cases.",
            "Scenarios",
          ],
          [
            "02",
            "Freeze your inputs",
            "Approve bank questions and create snapshots.",
            "Snapshots",
          ],
          [
            "03",
            "Compare & evaluate",
            "Run local methods and review blinded outputs.",
            "Experiments",
          ],
        ].map(([n, t, s, target]) => (
          <button className="step" key={n} onClick={() => p.setPage(target)}>
            <span>{n}</span>
            <h3>{t}</h3>
            <p>{s}</p>
            <b>Open workspace →</b>
          </button>
        ))}
      </div>
      <div className="card row spread">
        <div>
          <h3>Start with a small demonstration</h3>
          <p>
            Six clearly marked synthetic cases and three unvalidated starter
            questions. All await review. No simulated model outputs.
          </p>
        </div>
        <button
          disabled={p.busy}
          onClick={() =>
            p.run(
              () => api("/demo", "POST", {}),
              "Demonstration records created. Every scenario and question requires human review.",
            )
          }
        >
          Create demonstration data
        </button>
      </div>
      <div className="note">
        Synthetic data is not representative of real students. Question quality
        and evaluator agreement do not establish educational benefit.
      </div>
    </>
  );
}
