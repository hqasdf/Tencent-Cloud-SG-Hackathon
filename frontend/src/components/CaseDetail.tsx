import { useCallback, useEffect, useMemo, useState } from "react";
import type { CaseAnalysis, DisputeCase, PartyStatement, PartyContradiction } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { IntakeCase } from "../types/intake";
import type { ResolutionRunResult } from "../services/caseService";
import { intakeService } from "../services/intakeService";
import { AdvocateArguments } from "./AdvocateRun";
import { CrossExaminationPanel } from "./CrossExaminationPanel";
import { ExplanationPanel } from "./ExplanationPanel";
import { IntakeChatPanel } from "./IntakeChatPanel";
import { ReplayBadge, ReplayInvalidBadge } from "./ReplayBadge";
import { AuditTrail, DeterministicOutcomePanel, JudgeAdvisoryPanel } from "./JudgePanel";
import { DeterministicAnalysisPanel } from "./DeterministicAnalysis";
import { StepPending, WorkflowRail, WorkflowStep, type StepState } from "./WorkflowStep";
import { bothSettledOk, isSettledNotOk, isSettledOk } from "../types/workflow";
import type { SideStages, StageState } from "../types/workflow";
import { AiReviewTimeline } from "./AiReviewTimeline";
import { humanize, labelize, money } from "../utils/format";
import {
  resolutionIntakeLabel,
  rateLimitMessage
} from "../utils/uiContracts";

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
  onRunDemo?: () => void;
}

const STEPS = [
  { title: "Hear Both Sides", short: "Both sides", summary: "Rider and driver accounts" },
  { title: "Check the Evidence", short: "Evidence", summary: "Trip records, policy and disputed facts" },
  { title: "AI Review", short: "AI review", summary: "Five roles, with claims checked by code" },
  { title: "Final Decision", short: "Decision", summary: "Code-calculated outcome and next steps" }
];
const TOTAL_STEPS = 4;
const FIRST_LIVE_STEP = 3;

// ---------------------------------------------------------------------------
// Intake analysis helpers
// ---------------------------------------------------------------------------

/** Extract party statements from the intake case's final analysis, if present. */
function extractPartyStatements(intakeCase: IntakeCase | null): PartyStatement[] {
  if (!intakeCase?.finalAnalysis) return [];
  const fa = intakeCase.finalAnalysis as Record<string, unknown>;
  const raw = fa.partyStatements;
  if (!Array.isArray(raw)) return [];
  return raw as PartyStatement[];
}

/** Extract party contradictions from the intake case's final analysis, if present. */
function extractPartyContradictions(intakeCase: IntakeCase | null): PartyContradiction[] {
  if (!intakeCase?.finalAnalysis) return [];
  const fa = intakeCase.finalAnalysis as Record<string, unknown>;
  const raw = fa.partyContradictions;
  if (!Array.isArray(raw)) return [];
  return raw as PartyContradiction[];
}

/** A compact panel showing what each party stated during the AI intake interview. */
function PartyStatementsPanel({ statements }: { statements: PartyStatement[] }) {
  if (statements.length === 0) return null;
  return (
    <section className="panel party-statements-panel">
      <span className="eyebrow">AI intake · What each party said</span>
      <div className="party-statements-grid">
        {statements.map((ps) => (
          <div key={ps.party} className="party-statement-col">
            <h4>{ps.party === "rider" ? "Rider" : "Driver"}</h4>
            {ps.facts.length === 0 ? (
              <p className="judge-empty">No specific facts stated.</p>
            ) : (
              <ul className="party-fact-list">
                {ps.facts.map((f, i) => (
                  <li key={i}>
                    <span>{labelize(f.key)}</span>
                    <span>{f.value}</span>
                    <small>{humanize(f.evidenceStatus ?? "ALLEGED")}</small>
                    <details><summary>Source details</summary><small>{f.sourceMessageId ?? "Source unavailable"} {f.evidenceIds?.join(", ")}</small></details>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/** A warning banner shown when rider and driver gave contradictory accounts. */
function PartyContradictionBanner({ contradictions }: { contradictions: PartyContradiction[] }) {
  if (contradictions.length === 0) return null;
  return (
    <section className="panel party-contradiction-banner">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Intake contradiction detected</span>
          <h3>Parties disagree on {contradictions.length} key {contradictions.length === 1 ? "point" : "points"}</h3>
        </div>
      </div>
      <p className="judge-boundary-note">
        Different accounts are not proof of dishonesty. Unresolved disagreements need human review.
      </p>
      <div className="contradiction-list">
        {contradictions.map((c, i) => (
          <div className="contradiction-row" key={i}>
            <span>{labelize(c.factKey)}</span>
            <small>{c.status === "RESOLVED_BY_EVIDENCE" ? `Resolved by evidence: ${c.supportedValue}` : "Disputed · requires review"}</small>
            <details><summary>Source details</summary><small>{c.riderSourceMessageId ?? "unavailable"} / {c.driverSourceMessageId ?? "unavailable"} {c.evidenceIds?.join(", ")}</small></details>
            <div className="contradiction-claims">
              <div>
                <small>Rider said</small>
                <b>{c.riderClaim}</b>
              </div>
              <div>
                <small>Driver said</small>
                <b>{c.driverClaim}</b>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// Main component
// ---------------------------------------------------------------------------

export function CaseDetail({
  caseData,
  analysis,
  advocateRun,
  advocateRunning,
  advocateError,
  onRunAdvocates,
  resolutionRun,
  resolutionRunning,
  resolutionError,
  onRunResolution,
  onRunDemo
}: CaseDetailProps) {
  const [openStep, setOpenStep] = useState(1);
  const [intakeCase, setIntakeCase] = useState<IntakeCase | null>(null);
  const [intakeAnalysisError, setIntakeAnalysisError] = useState<string | null>(null);
  const quotaMessage = rateLimitMessage(resolutionRun ? [resolutionRun.rider.execution, resolutionRun.driver.execution,
    resolutionRun.rebuttals.rider.execution, resolutionRun.rebuttals.driver.execution, resolutionRun.judge.execution]
    : [advocateRun?.rider.execution, advocateRun?.driver.execution]);

  const intakeComplete = !!(intakeCase?.riderState?.interviewComplete && intakeCase?.driverState?.interviewComplete && (
    intakeCase?.lifecycle === "READY_FOR_ANALYSIS" ||
    intakeCase?.lifecycle === "ANALYSING" ||
    intakeCase?.lifecycle === "AUTO_RESOLVED" ||
    intakeCase?.lifecycle === "HUMAN_REVIEW"
  ));

  const handleIntakeChange = useCallback((ic: IntakeCase) => {
    if (ic.sourceCaseId !== caseData.id) return;
    setIntakeCase(ic);
  }, [caseData.id]);

  // Extract intake analysis data (party statements + contradictions) for display
  const partyStatements = useMemo(() => {
    if (resolutionRun) return resolutionRun.intakeContext?.partyStatements ?? [];
    const saved = extractPartyStatements(intakeCase);
    if (saved.length) return saved;
    return [intakeCase?.riderState, intakeCase?.driverState].flatMap(state => state ? [{
      party: state.party, facts: state.facts.filter(f => f.active !== false)
    }] : []);
  }, [intakeCase, resolutionRun]);
  const partyContradictions = useMemo(() => resolutionRun
    ? resolutionRun.intakeContext?.partyContradictions ?? []
    : extractPartyContradictions(intakeCase), [intakeCase, resolutionRun]);

  // When the intake interviews are complete (READY_FOR_ANALYSIS), auto-run the
  // intake analysis so party statements and contradiction detection are
  // computed and saved to finalAnalysis.
  useEffect(() => {
    if (!intakeCase || intakeCase.lifecycle !== "READY_FOR_ANALYSIS") return;
    let cancelled = false;
    setIntakeAnalysisError(null);
    void intakeService.analyse(intakeCase.id).then(async () => {
      if (cancelled) return;
      // The analysis is saved server-side; refresh the intake case to load
      // finalAnalysis with party statements and contradictions.
      const refreshed = await intakeService.getCase(intakeCase.id);
      if (!cancelled) setIntakeCase(refreshed);
    }).catch((error: unknown) => {
      if (!cancelled) setIntakeAnalysisError(error instanceof Error ? error.message : "Unable to review intake. Run full analysis to retry using the saved accounts.");
    });
    return () => { cancelled = true; };
  }, [intakeCase?.id, intakeCase?.lifecycle]);

  // A new case is a new workflow: start it from the top.
  useEffect(() => {
    setOpenStep(1);
    setIntakeCase(null);
    setIntakeAnalysisError(null);
  }, [caseData.id]);

  // When a run lands, open the first step that has new content.
  useEffect(() => {
    if (resolutionRun) setOpenStep(FIRST_LIVE_STEP);
  }, [resolutionRun]);

  // When intake completes, auto-advance to step 2 (Review the case)
  useEffect(() => {
    if (intakeComplete) setOpenStep(current => current === 1 ? 2 : current);
  }, [intakeComplete]);


  /**
   * The backend's own verdict on each stage. When it is present it is the only
   * input to step state — the UI does not infer anything from the fact that a
   * response object exists.
   */
  const workflow = resolutionRun?.workflow ?? null;
  const aiIncomplete = workflow !== null && !bothSettledOk(workflow.advocates);

  /**
   * One stage's status, as the backend reported it.
   *
   * `PENDING_HUMAN_REVIEW` is "done": the stage ran, and code decided a human
   * must sign off. `BLOCKED` and `SKIPPED` are not "failed" — nothing was
   * attempted, so the fault is upstream and the wording must not imply that
   * this stage went wrong.
   */
  function fromStage(state: StageState | undefined, fallback: StepState): StepState {
    if (!state) return fallback;
    switch (state.status) {
      case "COMPLETE":
      case "PENDING_HUMAN_REVIEW":
        return "done";
      case "FAILED":
        return "failed";
      case "BLOCKED":
      case "SKIPPED":
        return "blocked";
      default:
        return "waiting";
    }
  }

  /** A paired stage counts as done only when BOTH sides produced a result. */
  function fromPair(sides: SideStages | undefined, fallback: StepState): StepState {
    if (!sides) return fallback;
    if (sides.rider.status === "FAILED" || sides.driver.status === "FAILED") return "failed";
    if (isSettledNotOk(sides.rider) || isSettledNotOk(sides.driver)) return "blocked";
    if (isSettledOk(sides.rider) && isSettledOk(sides.driver)) return "done";
    return "waiting";
  }

  function stateFor(index: number): StepState {
    if (index === 1) return intakeComplete ? "done" : "ready";
    if (index === 2) return analysis ? fromStage(workflow?.deterministic, "done") : "waiting";
    if (index === 4) return fromStage(workflow?.finalResolution, "waiting");
    if (!workflow) return "ready";
    const states = [fromPair(workflow.advocates, "waiting"), fromPair(workflow.rebuttals, "waiting"), fromStage(workflow.judge, "waiting")];
    if (states.includes("failed")) return "failed";
    if (states.includes("blocked")) return "blocked";
    return states.every(state => state === "done") ? "done" : "waiting";
  }
  const aiReviewComplete = stateFor(3) === "done";
  const busy = resolutionRunning || advocateRunning;
  const decision = resolutionRun?.deterministicResolution;
  const toggle = (index: number) => setOpenStep(current => current === index ? 0 : index);
  const args = resolutionRun ?? advocateRun;

  return (
    <main className="content">
      <header className="case-header">
        <div><div className="breadcrumb">{caseData.id} · {labelize(caseData.disputeType)}</div>
          <h1>{caseData.title}</h1>
          <span className="mode">{decision?.resolutionMode === "HUMAN_REVIEW" ? "Needs human review" : aiReviewComplete ? "Review complete" : busy ? "Analysis in progress" : "Ready to review"}</span>
        </div>
        {decision && <div className="case-header-side"><strong>{humanize(decision.recommendedAction)}</strong><b>{money(decision.refundAmount, decision.currency)}</b><small>Calculated by code</small></div>}
      </header>
      <section className="workflow-toolbar">
        <div className="workflow-toolbar-left"><strong>{busy ? "Checking AI claims…" : decision ? "Review the decision" : intakeComplete ? "Ready for analysis" : "Hear both sides or review the existing case"}</strong>
          {resolutionRun ? <><ReplayBadge metadata={resolutionRun.replayMetadata} /><ReplayInvalidBadge metadata={resolutionRun.replayMetadata} />
            {!resolutionRun.replayMetadata.replayed && !resolutionRun.replayMetadata.reusedRoles?.length && <span className="mode-badge">{resolutionRun.replayMetadata.source === "MOCK_DEMO" ? "MOCK DEMO" : "LIVE"}</span>}</>
            : advocateRun ? <span className="mode-badge">{advocateRun.agentRun.mode === "real" ? "LIVE" : "MOCK DEMO"}</span> : null}
        </div>
        <div className="workflow-toolbar-actions">
          <button type="button" className="wf-run" onClick={aiReviewComplete ? () => setOpenStep(4) : onRunResolution} disabled={busy}>{busy ? "Running analysis…" : aiReviewComplete ? "View decision" : resolutionRun ? "Retry AI review" : "Run analysis"}</button>
          <details className="more-actions"><summary>More options</summary>
            {onRunDemo && <button type="button" className="wf-run-ghost" onClick={onRunDemo} disabled={busy}>View saved demo</button>}
            {!resolutionRun && <button type="button" className="wf-run-ghost" onClick={onRunAdvocates} disabled={busy}>Advocates only</button>}
            {aiReviewComplete && <button type="button" className="wf-run-ghost" onClick={onRunResolution} disabled={busy}>Run analysis again</button>}
          </details>
        </div>
      </section>
      {(resolutionError || advocateError) && <div className="workflow-alert" role="alert">Analysis could not finish. Your saved work and evidence remain available. Retry or view a saved demo.<details><summary>Error details</summary>{resolutionError ?? advocateError}</details></div>}
      {quotaMessage && <p className="workflow-alert" role="alert">{quotaMessage}</p>}
      {intakeAnalysisError && <div className="workflow-alert" role="alert">Statement review failed. Saved accounts remain available.<details><summary>Error details</summary>{intakeAnalysisError}</details></div>}
      {aiIncomplete && !quotaMessage && <p className="workflow-notice" role="status">AI review is incomplete. The code-calculated decision remains available. Retry to reuse compatible saved stages.</p>}
      <WorkflowRail steps={STEPS} stateFor={stateFor} current={openStep} onSelect={setOpenStep} />
      <div className="workflow">
        <WorkflowStep index={1} total={TOTAL_STEPS} {...STEPS[0]} state={stateFor(1)} open={openStep === 1} onToggle={() => toggle(1)} onNext={() => setOpenStep(2)}>
          <p className="judge-boundary-note">Statements are allegations until checked against evidence.</p>
          <IntakeChatPanel sourceCaseId={caseData.id} onLifecycleChange={handleIntakeChange} />
          {partyStatements.some(p => p.facts.length) && <PartyStatementsPanel statements={partyStatements} />}
          <details className="view-details"><summary>Original accounts and trip</summary><section className="two-col"><article className="panel"><h3>Rider allegation</h3><p>{caseData.riderComplaint}</p></article><article className="panel"><h3>Driver allegation</h3><p>{caseData.driverResponse}</p></article></section><p>{caseData.trip.pickup} / {caseData.trip.destination}</p><p>Quoted {money(caseData.fare.quoted, caseData.fare.currency)} / Charged {money(caseData.fare.actual, caseData.fare.currency)}</p><small>Trip {caseData.trip.tripId} / Policy {caseData.metadata.policyVersion}</small><p>Rider: {caseData.rider.name} ({caseData.rider.id}) / Driver: {caseData.driver.name} ({caseData.driver.id})</p><small>Priority {caseData.metadata.priority} / Submitted {caseData.metadata.submittedAt} / Updated {caseData.metadata.lastUpdated}</small></details>
        </WorkflowStep>
        <WorkflowStep index={2} total={TOTAL_STEPS} {...STEPS[1]} state={stateFor(2)} open={openStep === 2} onToggle={() => toggle(2)} onPrev={() => setOpenStep(1)} onNext={() => setOpenStep(3)}>
          <PartyContradictionBanner contradictions={partyContradictions} />
          <section className="panel"><h3>Evidence on file</h3><ul className="compact-evidence">{caseData.evidence.map(item => <li key={item.id}><span><b>{item.type}</b><span>{item.summary}</span></span><b className={`evidence-status ${item.status.toLowerCase()}`}>{item.status}</b><details><summary>Details</summary><small>{item.id} / {item.source} / {item.timestamp}</small></details></li>)}</ul></section>
          <details className="view-details"><summary>Trip timeline</summary>{caseData.timeline.map(event => <p key={event.id}><b>{event.timestamp} / {event.type}</b> {event.description}<small> {event.evidenceIds.join(", ")}</small></p>)}</details>
          <details className="view-details"><summary>Calculations and policy checks</summary>{analysis ? <DeterministicAnalysisPanel value={analysis} /> : <p>Evidence checks could not be loaded.</p>}</details>
        </WorkflowStep>
        <WorkflowStep index={3} total={TOTAL_STEPS} {...STEPS[2]} state={stateFor(3)} open={openStep === 3} onToggle={() => toggle(3)} onPrev={() => setOpenStep(2)} onNext={() => setOpenStep(4)}>
          <p className="judge-boundary-note">AI argues; code checks claims. The Judge advises and cannot set a refund.</p>
          {resolutionRun && <p className="workflow-notice">{resolutionIntakeLabel(resolutionRun.intakeContext)}</p>}
          {args ? <>
            <AiReviewTimeline rider={args.rider} driver={args.driver} resolution={resolutionRun} />
            <details className="view-details"><summary>View details: advocate claims and verification</summary><AdvocateArguments rider={args.rider} driver={args.driver} pipeline={args.pipeline} verificationSummary={args.verificationSummary} /></details>
            {resolutionRun && <><details className="view-details"><summary>View details: rebuttals and verification</summary><CrossExaminationPanel rebuttals={resolutionRun.rebuttals} /></details><details className="view-details"><summary>View details: Judge validation</summary><JudgeAdvisoryPanel judge={resolutionRun.judge} /></details></>}
          </> : <StepPending title="Ready for AI review">Choose Run analysis to check all five roles.</StepPending>}
        </WorkflowStep>
        <WorkflowStep index={4} total={TOTAL_STEPS} {...STEPS[3]} state={stateFor(4)} open={openStep === 4} onToggle={() => toggle(4)} onPrev={() => setOpenStep(3)}>
          {decision ? <><DeterministicOutcomePanel resolution={decision} />
            {decision.resolutionMode === "HUMAN_REVIEW" && <p className="human-review">A person must review this case before a refund or charge is approved.</p>}
            <details className="view-details"><summary>Why this decision</summary><ExplanationPanel explanation={resolutionRun!.explanation} counterfactual={resolutionRun!.counterfactual} /></details>
            <details className="view-details"><summary>Audit trail</summary><AuditTrail events={resolutionRun!.audit} /></details>
          </> : <StepPending title="Run analysis to view the final decision">Evidence checks are available in step 2. AI recommendations never control the refund.</StepPending>}
        </WorkflowStep>
      </div>
    </main>
  );
}
