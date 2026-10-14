import { useEffect, useRef, useState } from "react";
import { CaseDetail } from "./components/CaseDetail";
import { CaseApiError, caseService } from "./services/caseService";
import type { ResolutionRunResult } from "./services/caseService";
import type { AdvocateRunResult } from "./types/advocate";
import type { CaseAnalysis, CaseSummary, DisputeCase } from "./types/dispute";

export default function App({ cloudDemo = false }: { cloudDemo?: boolean } = {}) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedId, setSelectedId] = useState("DISP-002");
  const [selectedCase, setSelectedCase] = useState<DisputeCase | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingCase, setLoadingCase] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [advocateRun, setAdvocateRun] = useState<AdvocateRunResult | null>(null);
  const [advocateRunning, setAdvocateRunning] = useState(false);
  const [advocateError, setAdvocateError] = useState<string | null>(null);
  const [resolutionRun, setResolutionRun] = useState<ResolutionRunResult | null>(null);
  const [resolutionRunning, setResolutionRunning] = useState(false);
  const [resolutionError, setResolutionError] = useState<string | null>(null);
  const selectionEpoch = useRef(0);
  const aiRequestActive = useRef(false);

  function selectCase(id: string) {
    selectionEpoch.current += 1;
    setSelectedId(id);
  }

  useEffect(() => {
    void caseService.list().then((items) => {
      setCases(items);
      if (items.length && !items.some((item) => item.id === selectedId)) selectCase(items[0].id);
    }).catch((requestError: unknown) => setError(requestError instanceof Error ? requestError.message : "Unable to load disputes.")).finally(() => setLoadingList(false));
  }, []);

  useEffect(() => {
    if (!selectedId || loadingList) return;
    let cancelled = false;
    setLoadingCase(true);
    setError(null);
    setSelectedCase(null);
    setAnalysis(null);
    setAdvocateRun(null);
    setAdvocateError(null);
    setResolutionRun(null);
    setResolutionError(null);
    void Promise.all([caseService.getById(selectedId), caseService.getAnalysis(selectedId)]).then(([caseData, analysisData]) => {
      if (cancelled) return;
      setSelectedCase(caseData);
      setAnalysis(analysisData);
    }).catch((requestError: unknown) => {
      if (cancelled) return;
      if (requestError instanceof CaseApiError && requestError.status === 404) setError("This case is no longer available.");
      else setError(requestError instanceof Error ? requestError.message : "Unable to load the selected case.");
    }).finally(() => { if (!cancelled) setLoadingCase(false); });
    return () => { cancelled = true; };
  }, [selectedId, loadingList]);

  async function runAdvocates() {
    if (!selectedId || aiRequestActive.current) return;
    aiRequestActive.current = true;
    const epoch = selectionEpoch.current;
    setAdvocateRunning(true);
    setAdvocateError(null);
    try {
      const result = await caseService.runAdvocates(selectedId);
      if (epoch !== selectionEpoch.current || result.caseId !== selectedId) return;
      setAdvocateRun(result);
    } catch (requestError: unknown) {
      // Same rule as the full run: keep the previous advocates rather than
      // blanking the panel because one call failed.
      if (epoch === selectionEpoch.current) setAdvocateError(requestError instanceof Error ? requestError.message : "Unable to run the advocates.");
    } finally {
      setAdvocateRunning(false);
      aiRequestActive.current = false;
    }
  }

  async function runResolution(replayMode: "NONE" | "FULL_AI" = "NONE") {
    if (!selectedId || aiRequestActive.current) return;
    aiRequestActive.current = true;
    const epoch = selectionEpoch.current;
    setResolutionRunning(true);
    setResolutionError(null);
    try {
      const result = await caseService.runResolution(selectedId, cloudDemo ? "FULL_AI" : replayMode);
      if (epoch !== selectionEpoch.current || result.caseId !== selectedId) return;
      setResolutionRun(result);
      setAdvocateRun({
        caseId: result.caseId,
        disputeType: result.disputeType,
        rider: result.rider,
        driver: result.driver,
        agentRun: result.agentRun,
        verificationSummary: result.verificationSummary,
        pipeline: result.pipeline
      });
    } catch (requestError: unknown) {
      // A failed run must not erase a result the operator already has. The
      // error is reported alongside the previous result; the alternative â€”
      // clearing it â€” turns a transient outage into lost work, and the
      // deterministic half of a completed run is still perfectly valid.
      if (epoch === selectionEpoch.current) setResolutionError(requestError instanceof Error ? requestError.message : "Unable to run the resolution pipeline.");
    } finally {
      setResolutionRunning(false);
      aiRequestActive.current = false;
    }
  }

  if (loadingList) return <div className="loading">Loading RydeResolve case workspaceâ€¦</div>;
  if (error && cases.length === 0) return <div className="app-state"><h1>Case API unavailable</h1><p>{error}</p><button onClick={() => window.location.reload()}>Try again</button></div>;
  return <div className="app-shell"><aside className="dashboard"><div className="brand"><span className="brand-mark">R</span><div><strong>RydeResolve</strong><small>Dispute review</small></div></div><div className="sidebar-label">Open cases</div><nav>{cases.map((item) => <button key={item.id} className={`case-row ${selectedId === item.id ? "selected" : ""}`} onClick={() => selectCase(item.id)}><div className="case-row-head"><span>{item.id}</span><b className={`status status-${item.status.replaceAll(" ", "-").toLowerCase()}`}>{item.status}</b></div><strong>{item.title}</strong><small>{item.rider.name} Â· {item.driver.name}</small><div className="case-row-foot"><span>{item.disputeType.replaceAll("_", " ")}</span>{item.status !== "Pending" && <span>{item.confidence.overall}% confidence</span>}</div></button>)}</nav><div className="sidebar-note"><b>Evidence first</b><span>AI claims are checked. Code decides.</span></div></aside>{loadingCase ? <div className="loading">Loading case recordâ€¦</div> : error ? <div className="app-state"><h1>Case unavailable</h1><p>{error}</p><button onClick={() => selectCase(cases[0]?.id ?? "")}>Open first available case</button></div> : selectedCase ? <CaseDetail cloudDemo={cloudDemo} key={selectedId} caseData={selectedCase} analysis={analysis} advocateRun={advocateRun} advocateRunning={advocateRunning} advocateError={advocateError} onRunAdvocates={() => void runAdvocates()} resolutionRun={resolutionRun} resolutionRunning={resolutionRunning} resolutionError={resolutionError} onRunResolution={() => void runResolution()} onRunDemo={() => void runResolution("FULL_AI")} /> : <div className="app-state"><h1>No dispute selected</h1><p>Select a case from the dashboard.</p></div>}</div>;
}
