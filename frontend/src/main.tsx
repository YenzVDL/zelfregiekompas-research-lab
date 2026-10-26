import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, type RecordData as R } from "./api";
import { Badge } from "./components";
import { PAGES, ICONS } from "./constants";
import { Overview } from "./pages/Overview";
import { Scenarios } from "./pages/Scenarios";
import { Bank } from "./pages/Bank";
import { Snapshots } from "./pages/Snapshots";
import { Experiments } from "./pages/Experiments";
import { Evaluation } from "./pages/Evaluation";
import { Metrics } from "./pages/Metrics";
import { Protocol } from "./pages/Protocol";
import { Models } from "./pages/Models";
import "./style.css";

function App() {
  const [page, setPage] = useState("Overview");
  const [datasets, setDatasets] = useState<R[]>([]),
    [questions, setQuestions] = useState<R[]>([]),
    [snapshots, setSnapshots] = useState<R[]>([]),
    [jobs, setJobs] = useState<R[]>([]);
  const [banks, setBanks] = useState<R[]>([]), [bankId, setBankId] = useState("");
  const [modelState, setModelState] = useState<R>({
    available: false,
    models: [],
  });
  const [datasetId, setDatasetId] = useState("");
  const [reviewer, setReviewer] = useState(
    localStorage.getItem("reviewer") || "",
  );
  const [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const refresh = () => setRevision((x) => x + 1);
  useEffect(() => {
    localStorage.setItem("reviewer", reviewer);
  }, [reviewer]);
  useEffect(() => {
    let active = true;
    Promise.all([
      api("/datasets"),
      api("/questions"),
      api("/snapshots"),
      api("/jobs"),
      api("/banks"),
    ])
      .then(([d, q, s, j, b]) => {
        if (!active) return;
        setDatasets(d);
        setQuestions(q);
        setSnapshots(s);
        setJobs(j);
        setBanks(b);
        setBankId((old) => b.some((bank: R) => bank.id === old) ? old : b[0]?.id || "");
        setDatasetId((old) =>
          d.some((v: R) => v.id === old) ? old : d[0]?.id || "",
        );
      })
      .catch((e) => setError(e.message));
    return () => {
      active = false;
    };
  }, [revision]);
  useEffect(() => {
    let active = true;
    const load = () => api("/models").then((state) => { if (active) setModelState(state); }).catch((e) => { if (active) setError(e.message); });
    load();
    const timer = setInterval(load, 15000);
    window.addEventListener("focus", load);
    return () => { active = false; clearInterval(timer); window.removeEventListener("focus", load); };
  }, []);
  useEffect(() => {
    if (
      !jobs.some((j) => ["queued", "running", "cancelling"].includes(j.status))
    )
      return;
    const timer = setInterval(refresh, 1200);
    return () => clearInterval(timer);
  }, [jobs]);
  const run = async (
    fn: () => Promise<unknown>,
    message = "Saved to the local database.",
  ) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await fn();
      setNotice(message);
      refresh();
      return result;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const common = {
    run,
    refresh,
    revision,
    reviewer,
    busy,
    modelState,
    datasetId,
    datasets,
    questions,
    banks,
    bankId,
    setBankId,
    snapshots,
    jobs,
  };
  return (
    <div className="app">
      <aside>
        <div className="brand">
          <span className="compass">✳</span>
          <div>
            Zelfregiekompas<small>RESEARCH LAB</small>
          </div>
        </div>
        <div className="workspace">
          LOCAL WORKSPACE <span>01</span>
        </div>
        <nav>
          {PAGES.map((p, i) => (
            <button
              aria-label={p}
              className={page === p ? "active" : ""}
              key={p}
              onClick={() => {
                setPage(p);
                setError("");
                setNotice("");
              }}
            >
              <span aria-hidden="true">{ICONS[i]}</span>
              {p}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="dot" /> Local data · SQLite
          <small>Research tools, student agency.</small>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <span className="eyebrow">
              ZELFREGIEKOMPAS / RESEARCH WORKSPACE
            </span>
            <h1>{page}</h1>
          </div>
          <div className="header-tools">
            <Badge
              status={modelState.available ? "approved" : "awaiting_review"}
            >
              {modelState.available
                ? `${modelState.models.length} local models`
                : "Ollama offline"}
            </Badge>
            <label>
              Reviewer ID
              <input
                aria-label="Reviewer ID"
                placeholder="e.g. researcher-01"
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
              />
            </label>
          </div>
        </header>
        <div className="privacy">
          <span>⌁</span> All inference runs on your configured local Ollama server. Use synthetic
          scenarios and avoid unnecessary personal information.
        </div>
        {error && (
          <div className="alert error" role="alert">
            {error}
            <button className="text" onClick={() => setError("")}>
              Dismiss
            </button>
          </div>
        )}
        {notice && (
          <div className="alert success" role="status">
            {notice}
            <button className="text" onClick={() => setNotice("")}>
              Dismiss
            </button>
          </div>
        )}
        {page === "Overview" && <Overview {...common} setPage={setPage} />}
        {page === "Scenarios" && (
          <Scenarios {...common} setDatasetId={setDatasetId} />
        )}
        {page === "Question bank" && <Bank key={bankId} {...common} />}
        {page === "Snapshots" && <Snapshots {...common} />}
        {page === "Experiments" && <Experiments {...common} />}
        {page === "Evaluation" && <Evaluation {...common} />}
        {page === "Metrics" && <Metrics {...common} />}
        <div hidden={page !== "Models"}><Models {...common} setModelState={setModelState} /></div>
        {page === "Protocol & records" && (
          <Protocol {...common} setModelState={setModelState} />
        )}
        <footer>
          Zelfregiekompas Research Lab{" "}
          <span>LOCAL FIRST / SYNTHETIC BY DEFAULT</span>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
