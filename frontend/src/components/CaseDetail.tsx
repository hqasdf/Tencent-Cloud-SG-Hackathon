import type { CaseAnalysis, DisputeCase } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { ResolutionRunResult } from "../services/caseService";
import { AdvocateRunPanel } from "./AdvocateRun";
import { JudgePanel } from "./JudgePanel";
import { DeterministicAnalysisPanel } from "./DeterministicAnalysis";
import { labelize, money } from "../utils/format";

export interface CaseDetailProps {
  caseData: DisputeCase;
  analysis: CaseAnalysis | null;
  advocateRun: AdvocateRunResult | null;
  advocateRunning: boolean;
  advocateError: string | null;
  onRunAdvocates: () => void;
  resolutionRun: ResolutionRunResult | null;
  resolutionRunning: boolean;
  resolutionError: string | null;
  onRunResolution: () => void;
}

export function CaseDetail({ caseData, analysis, advocateRun, advocateRunning, advocateError, onRunAdvocates, resolutionRun, resolutionRunning, resolutionError, onRunResolution }: CaseDetailProps) {
  const r = caseData.resolution;
  return <main className="content"><header className="case-header"><div><div className="breadcrumb">Disputes / {caseData.id}</div><h1>{caseData.title}</h1><p>{caseData.description}</p></div><div className={`mode ${r.mode === "HUMAN_REVIEW" ? "review" : "auto"}`}>{r.mode === "AUTO_RESOLVE" ? "Auto decision ready" : "Human review required"}</div></header>
  <section className="summary-grid"><article><span>Trip</span><b>{caseData.trip.tripId}</b><small>{caseData.trip.pickup} → {caseData.trip.destination}</small></article><article><span>Fare difference</span><b>{money(caseData.fare.difference, caseData.fare.currency)}</b><small>Quoted {money(caseData.fare.quoted, caseData.fare.currency)} · final {money(caseData.fare.actual, caseData.fare.currency)}</small></article><article><span>Case metadata</span><b>{caseData.metadata.priority} priority</b><small>Policy {caseData.metadata.policyVersion} · updated {caseData.metadata.lastUpdated}</small></article></section>
  <section className="two-col case-narrative"><article className="panel"><span className="eyebrow">Rider complaint</span><p>“{caseData.riderComplaint}”</p><small>{caseData.rider.name} · {caseData.rider.id}</small></article><article className="panel"><span className="eyebrow">Driver response</span><p>“{caseData.driverResponse}”</p><small>{caseData.driver.name} · {caseData.driver.id}</small></article></section>
  <section className="panel"><div className="panel-heading"><div><span className="eyebrow">CaseReplay</span><h3>Evidence-linked timeline</h3></div></div><div className="timeline">{caseData.timeline.map((event) => <div key={event.id} className={`timeline-event ${event.severity ?? "normal"}`}><time>{event.timestamp}</time><i></i><div><b>{event.type}</b><p>{event.description}</p><code>{event.evidenceIds.join(" · ")}</code></div></div>)}</div></section>
  <section className="panel"><div className="panel-heading"><div><span className="eyebrow">Evidence</span><h3>Case record</h3></div><span className="evidence-count">{caseData.evidence.length} items</span></div><div className="evidence-table"><div className="table-head"><span>ID</span><span>Type</span><span>Source</span><span>Summary</span><span>Status</span></div>{caseData.evidence.map((item) => <div className="evidence-row" key={item.id}><code>{item.id}</code><span>{item.type}</span><span>{item.source}</span><span>{item.summary}</span><b className={`evidence-status ${item.status.toLowerCase()}`}>{item.status}</b></div>)}</div></section>
  <AdvocateRunPanel result={advocateRun} running={advocateRunning} error={advocateError} onRun={onRunAdvocates} />
  <section className="panel stage5-control">
    <div className="panel-heading">
      <div>
        <span className="eyebrow">Stage 5 · Judge</span>
        <h3>Run the full resolution pipeline</h3>
      </div>
      <div className="advocate-heading-actions">
        {resolutionRun && (
          <span className={`mode-badge ${resolutionRun.agentRun.mode}`}>
            {resolutionRun.agentRun.mode === "real" ? "REAL MODEL" : "MOCK"}
          </span>
        )}
        <button onClick={onRunResolution} disabled={resolutionRunning} className="run-advocates">
          {resolutionRunning ? "Running resolution…" : resolutionRun ? "Run again" : "Run resolution"}
        </button>
      </div>
    </div>
    <p className="prototype-note">
      Runs the advocates, verifies their claims, asks the Judge to weigh the verified
      arguments, then applies the deterministic remedy. The Judge recommends; code decides.
    </p>
    {resolutionError && <p className="review-reason">{resolutionError}</p>}
  </section>
  {resolutionRun && (
    <JudgePanel
      judge={resolutionRun.judge}
      resolution={resolutionRun.deterministicResolution}
      audit={resolutionRun.audit}
    />
  )}
  {analysis && <DeterministicAnalysisPanel value={analysis} />}
  <section className="panel"><div className="panel-heading"><div><span className="eyebrow">Agent activity</span><h3>Decision pipeline</h3></div></div><div className="activity">{caseData.activity.map((step, index) => <div className={`activity-step ${step.state}`} key={step.id}><span>{step.state === "complete" ? "✓" : index + 1}</span><div><b>{step.label}</b><small>{step.detail}</small></div></div>)}</div></section>
  <section className="decision-grid"><article className="panel decision"><span className="eyebrow">Deterministic decision · CODE</span><h2>{r.ruling}</h2><p>{r.explanation}</p><div className="decision-stats"><span><small>Action</small><b>{r.recommendedAction}</b></span><span><small>Refund</small><b>{money(r.refundAmount, r.currency)}</b></span><span><small>Policy</small><b>{caseData.policyResult.policyId}</b></span></div><p className="accepted">Accepted claims: {r.acceptedClaimIds.join(", ")} · Rejected: {r.rejectedClaimIds.join(", ")}</p><p className="judge-boundary-note">This panel is the deterministic engine's output, not the AI Judge's. The Judge's advisory recommendation appears in the Stage 5 panel above.</p></article><article className="panel"><span className="eyebrow">Confidence &amp; escalation</span><h3>{caseData.confidence.overall}% overall confidence</h3><div className="factors">{Object.entries(caseData.confidence).filter(([key]) => key !== "overall").map(([key, value]) => <div key={key}><span>{labelize(key)}</span><b>{value}%</b><i><em style={{ width: `${value}%` }} /></i></div>)}</div><div className={`resolution-mode ${r.mode.toLowerCase()}`}>{r.mode}</div>{r.escalationReason && <p className="review-reason">Escalation: {r.escalationReason}</p>}</article></section>
  <section className="explain-grid"><article className="panel"><span className="eyebrow">Why this decision?</span><p>{r.explanation}</p></article><article className="panel"><span className="eyebrow">What would have changed the decision?</span><p>{r.counterfactualExplanation}</p></article></section>
  {caseData.humanReviewSummary && <section className="human-review"><span className="eyebrow">Human reviewer brief</span><h3>Evidence conflict requires adjudication</h3><p>{caseData.humanReviewSummary}</p></section>}</main>;
}
