import { useEffect, useRef, useState } from "react";
import { CaseDetail } from "./components/CaseDetail";
import { CaseApiError, caseService } from "./services/caseService";
import type { ResolutionRunResult } from "./services/caseService";
import type { AdvocateRunResult } from "./types/advocate";
import type { CaseAnalysis, CaseSummary, DisputeCase } from "./types/dispute";
import type { CaseStatus } from "./types/dispute";

/** Tailwind badge class for a given case status, matching the design spec. */
function statusBadgeClass(status: CaseStatus, isActive: boolean): string {
  if (isActive) {
    switch (status) {
      case "Auto Resolved":
        return "bg-green-50 text-green-600";
      case "Investigating":
        return "bg-yellow-100 text-yellow-700";
      case "Human Review":
        return "bg-red-100 text-red-600";
      default:
        return "bg-white/20 text-gray-300";
    }
  }
  switch (status) {
    case "Auto Resolved":
      return "bg-green-500/20 text-green-300";
    case "Investigating":
      return "bg-yellow-500/20 text-yellow-300";
    case "Human Review":
      return "bg-red-500/20 text-red-300";
    default:
      return "bg-white/10 text-gray-300";
  }
}

/** Tailwind badge text for a given case status. */
function statusBadgeText(status: CaseStatus): string {
  switch (status) {
    case "Auto Resolved":
      return "AUTO RESOLVED";
    case "Investigating":
      return "INVESTIGATING";
    case "Human Review":
      return "HUMAN REVIEW";
    default:
      return "PENDING";
  }
}

function humanizeDispute(disputeType: string): string {
  return disputeType.split("_").map(part => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase()).join(" ");
}

export default function App() {
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
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
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
      const result = await caseService.runResolution(selectedId, replayMode);
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
      if (epoch === selectionEpoch.current) setResolutionError(requestError instanceof Error ? requestError.message : "Unable to run the resolution pipeline.");
    } finally {
      setResolutionRunning(false);
      aiRequestActive.current = false;
    }
  }

  if (loadingList) return <div className="flex items-center justify-center min-h-screen bg-[#fdfcfaf] text-gray-500">Loading RydeResolve case workspace…</div>;
  if (error && cases.length === 0) return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-[#fdfcfaf] gap-3">
      <h1 className="text-2xl font-bold text-[#131b2b]">Case API unavailable</h1>
      <p className="text-gray-500">{error}</p>
      <button className="px-5 py-2.5 rounded-lg bg-[#f03b65] text-white font-semibold hover:opacity-90" onClick={() => window.location.reload()}>Try again</button>
    </div>
  );

  return (
    <div className="flex min-h-screen bg-[#fdfcfaf]">
      {/* ── Left Sidebar ── */}
      <aside className="w-[350px] shrink-0 sticky top-0 h-screen bg-[#131b2b] flex flex-col overflow-hidden">
        {/* Logo / brand — auto-trimmed PNG (no internal padding) */}
        <div className="px-6 pt-7 pb-8 border-b border-white/8">
          <img
            src="/logo-trimmed.png"
            alt="ryderesolve"
            className="h-10 w-auto block"
          />
        </div>

        {/* OPEN CASES header + search/filter icons (search input collapses, only expands on click) */}
        <div className="px-6 pt-5 pb-3 border-b border-white/8">
          <div className="flex items-center justify-between">
            <span className="text-[12px] font-extrabold tracking-widest text-white uppercase">Open Cases</span>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setSearchOpen((open) => !open)}
                aria-label="Toggle search"
                aria-expanded={searchOpen}
                className="text-gray-400 hover:text-white transition-colors"
              >
                <svg className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                  <circle cx={11} cy={11} r={7} />
                  <path strokeLinecap="round" d="m20 20-3.5-3.5" />
                </svg>
              </button>
              <button type="button" aria-label="Filter" className="text-gray-400 hover:text-white transition-colors">
                <svg className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 5h18l-7 8v6l-4-2v-4L3 5z" />
                </svg>
              </button>
            </div>
          </div>
          {searchOpen && (
            <div className="mt-3 flex items-center gap-2 bg-white/5 rounded-lg px-3 py-2">
              <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                autoFocus
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="flex-1 bg-transparent text-sm text-white placeholder-gray-500 outline-none"
                placeholder="Search cases…"
              />
            </div>
          )}
        </div>

        {/* Case list */}
        <nav className="sidebar-case-list flex-1 overflow-y-auto px-6 pt-8 pb-4">
          {cases
            .filter((item) => {
              const q = searchQuery.trim().toLowerCase();
              if (!q) return true;
              return (
                item.title.toLowerCase().includes(q) ||
                item.id.toLowerCase().includes(q) ||
                item.rider.name.toLowerCase().includes(q) ||
                item.driver.name.toLowerCase().includes(q)
              );
            })
            .map((item) => {
              const isActive = selectedId === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => selectCase(item.id)}
                  className={`w-full text-left rounded-xl p-3 transition-all ${
                    isActive
                      ? "bg-transparent ring-2 ring-[#f03b65] shadow-[0_0_15px_rgba(240,59,101,0.3)]"
                      : "bg-transparent hover:bg-white/5"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-[10px] font-mono font-semibold ${isActive ? "text-white" : "text-gray-400"}`}>{item.id}</span>
                    <span className={`text-[9px] font-bold tracking-wide px-2 py-0.5 rounded ${isActive ? statusBadgeClass(item.status, isActive) : "bg-white/8 text-gray-400"}`}>
                      {statusBadgeText(item.status)}
                    </span>
                  </div>
                  <p className={`text-[13px] font-semibold leading-snug mb-1 ${isActive ? "text-white" : "text-gray-300"}`}>{item.title}</p>
                  <p className={`text-[11px] ${isActive ? "text-gray-300" : "text-gray-500"}`}>
                    {item.rider.name} · {item.driver.name}
                  </p>
                  {isActive && (
                    <div className="flex items-center justify-between mt-2 pt-2 border-t border-white/8">
                      <span className="text-[10px] font-semibold text-white bg-white/10 border border-white/15 px-2 py-0.5 rounded-full">{humanizeDispute(item.disputeType)}</span>
                      <span className="text-[10px] text-gray-300">{item.confidence.overall}% confidence</span>
                    </div>
                  )}
                  {!isActive && item.status !== "Pending" && (
                    <p className="text-[10px] text-gray-500 mt-1">{item.confidence.overall}% confidence</p>
                  )}
                </button>
              );
            })}
        </nav>

        {/* Sidebar footer note */}
        <div className="mx-6 mb-4 rounded-xl border border-white/10 bg-white/4 p-3">
          <b className="block text-white text-xs font-semibold mb-1">Evidence-first routing</b>
          <span className="text-[11px] leading-relaxed text-gray-400">Claims are checked against the case record before policy evaluation.</span>
        </div>
      </aside>

      {/* ── Main Content Area ── */}
      <main className="flex-1 min-w-0 bg-[#fdfcfaf]">
        {loadingCase ? (
          <div className="flex items-center justify-center min-h-screen text-gray-500">Loading case record…</div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center min-h-screen gap-3">
            <h1 className="text-2xl font-bold text-[#131b2b]">Case unavailable</h1>
            <p className="text-gray-500">{error}</p>
            <button className="px-5 py-2.5 rounded-lg bg-[#f03b65] text-white font-semibold hover:opacity-90" onClick={() => selectCase(cases[0]?.id ?? "")}>Open first available case</button>
          </div>
        ) : selectedCase ? (
          <CaseDetail
            key={selectedId}
            caseData={selectedCase}
            analysis={analysis}
            advocateRun={advocateRun}
            advocateRunning={advocateRunning}
            advocateError={advocateError}
            onRunAdvocates={() => void runAdvocates()}
            resolutionRun={resolutionRun}
            resolutionRunning={resolutionRunning}
            resolutionError={resolutionError}
            onRunResolution={() => void runResolution()}
            onRunDemo={() => void runResolution("FULL_AI")}
          />
        ) : (
          <div className="flex flex-col items-center justify-center min-h-screen gap-3">
            <h1 className="text-2xl font-bold text-[#131b2b]">No dispute selected</h1>
            <p className="text-gray-500">Select a case from the dashboard.</p>
          </div>
        )}
      </main>
    </div>
  );
}
