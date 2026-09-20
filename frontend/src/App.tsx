import { useEffect, useState } from "react";
import { CaseDetail } from "./components/CaseDetail";
import { DisputeDashboard } from "./components/DisputeDashboard";
import { CaseApiError, caseService } from "./services/caseService";
import type { AdvocateRunResult } from "./types/advocate";
import type { CaseAnalysis, CaseSummary, DisputeCase } from "./types/dispute";

export default function App() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedId, setSelectedId] = useState("CASE-2026-1041");
  const [selectedCase, setSelectedCase] = useState<DisputeCase | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingCase, setLoadingCase] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [advocateRun, setAdvocateRun] = useState<AdvocateRunResult | null>(null);
  const [advocateRunning, setAdvocateRunning] = useState(false);
  const [advocateError, setAdvocateError] = useState<string | null>(null);

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
    // Advocate output is per-case: clear it so a stale run never shows under a
    // different case (the panel must never mix old and new advocates).
    setAdvocateRun(null);
    setAdvocateError(null);
    void Promise.all([caseService.getById(selectedId), caseService.getAnalysis(selectedId)]).then(([caseData, analysisData]) => {
      setSelectedCase(caseData);
      setAnalysis(analysisData);
    }).catch((requestError: unknown) => {
      if (requestError instanceof CaseApiError && requestError.status === 404) setError("This case is no longer available.");
      else setError(requestError instanceof Error ? requestError.message : "Unable to load the selected case.");
    }).finally(() => setLoadingCase(false));
  }, [selectedId, loadingList]);

  async function runAdvocates() {
    if (!selectedId) return;
    setAdvocateRunning(true);
    setAdvocateError(null);
    try {
      setAdvocateRun(await caseService.runAdvocates(selectedId));
    } catch (requestError: unknown) {
      setAdvocateRun(null);
      setAdvocateError(requestError instanceof Error ? requestError.message : "Unable to run the advocates.");
    } finally {
      setAdvocateRunning(false);
    }
  }

  if (loadingList) return <div className="loading">Loading RydeResolve case workspace…</div>;
  if (error && cases.length === 0) return <div className="app-state"><h1>Case API unavailable</h1><p>{error}</p><button onClick={() => window.location.reload()}>Try again</button></div>;
  return <div className="app-shell"><DisputeDashboard cases={cases} selectedId={selectedId} onSelect={setSelectedId} />{loadingCase ? <div className="loading">Loading case record…</div> : error ? <div className="app-state"><h1>Case unavailable</h1><p>{error}</p><button onClick={() => setSelectedId(cases[0]?.id ?? "")}>Open first available case</button></div> : selectedCase ? <CaseDetail caseData={selectedCase} analysis={analysis} advocateRun={advocateRun} advocateRunning={advocateRunning} advocateError={advocateError} onRunAdvocates={() => void runAdvocates()} /> : <div className="app-state"><h1>No dispute selected</h1><p>Select a case from the dashboard.</p></div>}</div>;
}
