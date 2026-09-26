import { useEffect, useState } from "react";
import { CaseDetail } from "./components/CaseDetail";
import { DisputeDashboard } from "./components/DisputeDashboard";
import { IntakeWorkspace } from "./components/IntakeWorkspace";
import { CaseApiError, caseService } from "./services/caseService";
import type { CaseAnalysis, CaseSummary, DisputeCase } from "./types/dispute";

type View = "cases" | "intake";

export default function App() {
  const [view, setView] = useState<View>("cases");
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedId, setSelectedId] = useState("DISP-002");
  const [selectedCase, setSelectedCase] = useState<DisputeCase | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingCase, setLoadingCase] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void caseService.list().then((items) => {
      setCases(items);
      if (items.length && !items.some((item) => item.id === selectedId)) setSelectedId(items[0].id);
    }).catch((requestError: unknown) => setError(requestError instanceof Error ? requestError.message : "Unable to load disputes.")).finally(() => setLoadingList(false));
  }, []);

  useEffect(() => {
    if (!selectedId || loadingList) return;
    setLoadingCase(true);
    setError(null);
    setSelectedCase(null);
    setAnalysis(null);
    void Promise.all([caseService.getById(selectedId), caseService.getAnalysis(selectedId)]).then(([caseData, analysisData]) => {
      setSelectedCase(caseData);
      setAnalysis(analysisData);
    }).catch((requestError: unknown) => {
      if (requestError instanceof CaseApiError && requestError.status === 404) setError("This case is no longer available.");
      else setError(requestError instanceof Error ? requestError.message : "Unable to load the selected case.");
    }).finally(() => setLoadingCase(false));
  }, [selectedId, loadingList]);

  if (loadingList) return <div className="loading">Loading RydeResolve case workspace…</div>;
  if (error && cases.length === 0) return <div className="app-state"><h1>Case API unavailable</h1><p>{error}</p><button onClick={() => window.location.reload()}>Try again</button></div>;
  return <div className="app-shell"><aside className="dashboard"><div className="brand"><span className="brand-mark">R</span><div><strong>RydeResolve</strong><small>Operations workspace</small></div></div><div className="sidebar-label">Views</div><nav className="view-toggle"><button className={`case-row ${view === "cases" ? "selected" : ""}`} onClick={() => setView("cases")}><strong>Case workspace</strong><small>Evidence-based disputes</small></button><button className={`case-row ${view === "intake" ? "selected" : ""}`} onClick={() => setView("intake")}><strong>AI intake</strong><small>LLM-guided interviews</small></button></nav>{view === "cases" && <><div className="sidebar-label" style={{marginTop:14}}>Open cases</div><nav>{cases.map((item) => <button key={item.id} className={`case-row ${selectedId === item.id ? "selected" : ""}`} onClick={() => setSelectedId(item.id)}><div className="case-row-head"><span>{item.id}</span><b className={`status status-${item.status.replaceAll(" ", "-").toLowerCase()}`}>{item.status}</b></div><strong>{item.title}</strong><small>{item.rider.name} · {item.driver.name}</small><div className="case-row-foot"><span>{item.disputeType.replaceAll("_", " ")}</span>{item.status !== "Pending" && <span>{item.confidence.overall}% confidence</span>}</div></button>)}</nav></>}<div className="sidebar-note"><b>Evidence-first routing</b><span>Claims are checked against the case record before policy evaluation.</span></div></aside>{view === "intake" ? <IntakeWorkspace /> : loadingCase ? <div className="loading">Loading case record…</div> : error ? <div className="app-state"><h1>Case unavailable</h1><p>{error}</p><button onClick={() => setSelectedId(cases[0]?.id ?? "")}>Open first available case</button></div> : selectedCase ? <CaseDetail caseData={selectedCase} analysis={analysis} /> : <div className="app-state"><h1>No dispute selected</h1><p>Select a case from the dashboard.</p></div>}</div>;
}
