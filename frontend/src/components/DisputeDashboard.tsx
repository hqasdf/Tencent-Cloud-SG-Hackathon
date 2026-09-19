import type { DisputeCase } from "../types/dispute";

interface Props { cases: DisputeCase[]; selectedId: string; onSelect: (id: string) => void; }
export function DisputeDashboard({ cases, selectedId, onSelect }: Props) {
  return <aside className="dashboard"><div className="brand"><span className="brand-mark">R</span><div><strong>RydeResolve</strong><small>Operations workspace</small></div></div><div className="sidebar-label">Open cases</div><nav>{cases.map((item) => <button key={item.id} className={`case-row ${selectedId === item.id ? "selected" : ""}`} onClick={() => onSelect(item.id)}><div className="case-row-head"><span>{item.id}</span><b className={`status status-${item.status.replaceAll(" ", "-").toLowerCase()}`}>{item.status}</b></div><strong>{item.title}</strong><small>{item.rider.name} · {item.driver.name}</small><div className="case-row-foot"><span>{item.disputeType.replaceAll("_", " ")}</span>{item.status !== "Pending" && <span>{item.confidence.overall}% confidence</span>}</div></button>)}</nav><div className="sidebar-note"><b>Evidence-first routing</b><span>Claims are checked against the case record before policy evaluation.</span></div></aside>;
}
