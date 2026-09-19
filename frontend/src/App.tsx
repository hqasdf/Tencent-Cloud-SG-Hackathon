import { useEffect, useState } from "react";
import { CaseDetail } from "./components/CaseDetail";
import { DisputeDashboard } from "./components/DisputeDashboard";
import { caseService } from "./services/caseService";
import type { DisputeCase } from "./types/dispute";

export default function App() {
  const [cases, setCases] = useState<DisputeCase[]>([]);
  const [selectedId, setSelectedId] = useState("CASE-2026-1041");
  useEffect(() => { void caseService.list().then(setCases); }, []);
  const selected = cases.find((item) => item.id === selectedId) ?? cases[0];
  if (!selected) return <div className="loading">Loading RydeResolve…</div>;
  return <div className="app-shell"><DisputeDashboard cases={cases} selectedId={selected.id} onSelect={setSelectedId} /><CaseDetail caseData={selected} /></div>;
}
