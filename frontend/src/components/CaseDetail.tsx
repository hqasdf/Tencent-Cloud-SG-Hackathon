import { useCallback, useEffect, useMemo, useState } from "react";
import type { CaseAnalysis, DisputeCase, PartyStatement, PartyContradiction } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { IntakeCase } from "../types/intake";
import type { ResolutionRunResult } from "../services/caseService";
import { intakeService } from "../services/intakeService";
import { IntakeChatPanel } from "./IntakeChatPanel";
import { AdvocateArguments } from "./AdvocateRun";
import { CrossExaminationPanel } from "./CrossExaminationPanel";
import { ExplanationPanel } from "./ExplanationPanel";
import { ReplayBadge, ReplayInvalidBadge } from "./ReplayBadge";
import { AuditTrail, DeterministicOutcomePanel, JudgeAdvisoryPanel } from "./JudgePanel";
import { DeterministicAnalysisPanel } from "./DeterministicAnalysis";
import { StepPending, WorkflowStep, type StepState } from "./WorkflowStep";
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

function extractPartyStatements(intakeCase: IntakeCase | null): PartyStatement[] {
  if (!intakeCase?.finalAnalysis) return [];
  const fa = intakeCase.finalAnalysis as Record<string, unknown>;
  const raw = fa.partyStatements;
  if (!Array.isArray(raw)) return [];
  return raw as PartyStatement[];
}

function extractPartyContradictions(intakeCase: IntakeCase | null): PartyContradiction[] {
  if (!intakeCase?.finalAnalysis) return [];
  const fa = intakeCase.finalAnalysis as Record<string, unknown>;
  const raw = fa.partyContradictions;
  if (!Array.isArray(raw)) return [];
  return raw as PartyContradiction[];
}

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

// ---------------------------------------------------------------------------
// Stepper pill component (horizontal, matches design spec)
// ---------------------------------------------------------------------------

function StepperPill({ index, label, state }: { index: number; label: string; state: StepState }) {
  const base = "flex items-center gap-2.5 rounded-full px-4 py-2 text-sm font-medium transition-all";
  let cls: string;
  if (state === "done") {
    cls = `${base} bg-white text-gray-700 border border-gray-200`;
  } else if (state === "ready") {
    cls = `${base} bg-white text-[#f03b65] border-2 border-[#f03b65]`;
  } else {
    cls = `${base} bg-white text-gray-500 border border-gray-200`;
  }
  return (
    <div className={cls}>
      <span className={`flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-bold ${
        state === "done"
          ? "bg-green-500 text-white"
          : state === "ready"
            ? "bg-[#f03b65] text-white"
            : "bg-gray-200 text-gray-500"
      }`}>
        {state === "done" ? "✓" : index}
      </span>
      {label}
    </div>
  );
}

// ---------------------------------------------------------------------------
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

  useEffect(() => {
    if (!intakeCase || intakeCase.lifecycle !== "READY_FOR_ANALYSIS") return;
    let cancelled = false;
    setIntakeAnalysisError(null);
    void intakeService.analyse(intakeCase.id).then(async () => {
      if (cancelled) return;
      const refreshed = await intakeService.getCase(intakeCase.id);
      if (!cancelled) setIntakeCase(refreshed);
    }).catch((error: unknown) => {
      if (!cancelled) setIntakeAnalysisError(error instanceof Error ? error.message : "Unable to review intake. Run full analysis to retry using the saved accounts.");
    });
    return () => { cancelled = true; };
  }, [intakeCase?.id, intakeCase?.lifecycle]);

  useEffect(() => {
    setOpenStep(1);
    setIntakeCase(null);
    setIntakeAnalysisError(null);
  }, [caseData.id]);

  useEffect(() => {
    if (resolutionRun) setOpenStep(FIRST_LIVE_STEP);
  }, [resolutionRun]);

  useEffect(() => {
    if (intakeComplete) setOpenStep(current => current === 1 ? 2 : current);
  }, [intakeComplete]);

  const workflow = resolutionRun?.workflow ?? null;
  const aiIncomplete = workflow !== null && !bothSettledOk(workflow.advocates);

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

  // Stepper states for the horizontal pill bar
  const step1State = stateFor(1);
  const step2State = stateFor(2);
  const step3State = stateFor(3);
  const step4State = stateFor(4);

  return (
    <div className="min-h-screen">
      {/* ── Top Header ── */}
      <header className="px-8 pt-8 pb-5">
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-3 mb-3">
              <span className="text-[11px] font-mono tracking-wider uppercase text-gray-400">
                {caseData.id} · {labelize(caseData.disputeType)}
              </span>
              {intakeComplete && (
                <span className="inline-flex items-center gap-1 text-[10px] font-extrabold tracking-wider uppercase px-2.5 py-1 rounded-full bg-green-50 text-green-600">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                  Ready to review
                </span>
              )}
            </div>
            <h1 className="text-2xl font-medium tracking-tight text-[#131b2b] leading-tight">
              {caseData.title}
            </h1>
          </div>
          {decision && (
            <div className="flex flex-col items-end gap-1 shrink-0">
              <strong className="text-sm font-semibold text-[#131b2b]">{humanize(decision.recommendedAction)}</strong>
              <b className="text-2xl font-extrabold text-[#131b2b]">{money(decision.refundAmount, decision.currency)}</b>
              <small className="text-[10px] tracking-wider uppercase text-gray-400">Calculated by code</small>
            </div>
          )}
        </div>
      </header>

      {/* ── Dark Action Banner ── */}
      <div className="mx-8 mb-6">
        <div className="flex items-center justify-between gap-4 rounded-full bg-[#131b2b] px-6 py-3.5">
          <p className="text-sm font-medium text-white">
            {busy ? "Checking AI claims…" : decision ? "Review the decision" : intakeComplete ? "Ready for analysis" : "Hear both sides or review the existing case"}
          </p>
          <div className="flex items-center gap-3">
            {resolutionRun && (
              <div className="flex items-center gap-2">
                <ReplayBadge metadata={resolutionRun.replayMetadata} />
                <ReplayInvalidBadge metadata={resolutionRun.replayMetadata} />
                {!resolutionRun.replayMetadata.replayed && !resolutionRun.replayMetadata.reusedRoles?.length && (
                  <span className="text-[10px] font-extrabold tracking-wider px-2.5 py-1 rounded-full border border-transparent bg-white/10 text-gray-200">
                    {resolutionRun.replayMetadata.source === "MOCK_DEMO" ? "MOCK DEMO" : "LIVE"}
                  </span>
                )}
              </div>
            )}
            {advocateRun && !resolutionRun && (
              <span className="text-[10px] font-extrabold tracking-wider px-2.5 py-1 rounded-full border border-transparent bg-green-500/20 text-green-300">
                {advocateRun.agentRun.mode === "real" ? "LIVE" : "MOCK DEMO"}
              </span>
            )}
            <button
              type="button"
              onClick={aiReviewComplete ? () => setOpenStep(4) : onRunResolution}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-full bg-[#f03b65] px-5 py-2 text-sm font-bold text-white shadow-[0_0_15px_rgba(240,59,101,0.4)] transition-all hover:bg-[#d8334f] hover:shadow-[0_0_20px_rgba(240,59,101,0.5)] disabled:bg-gray-500 disabled:shadow-none disabled:cursor-progress"
            >
              {busy ? "Running analysis…" : aiReviewComplete ? "View decision" : resolutionRun ? "Retry AI review" : "Run analysis"}
              {!busy && <span className="text-base">⚡</span>}
            </button>
            <details className="relative">
              <summary className="cursor-pointer list-none flex items-center gap-1 text-gray-400 hover:text-white transition-colors text-sm">
                More
              </summary>
              <div className="absolute right-0 top-full mt-2 z-10 bg-white rounded-lg shadow-xl border border-gray-100 p-2 space-y-1 min-w-[180px]">
                {onRunDemo && (
                  <button type="button" className="block w-full text-left rounded-md px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50" onClick={onRunDemo} disabled={busy}>
                    View saved demo
                  </button>
                )}
                {!resolutionRun && (
                  <button type="button" className="block w-full text-left rounded-md px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50" onClick={onRunAdvocates} disabled={busy}>
                    Advocates only
                  </button>
                )}
                {aiReviewComplete && (
                  <button type="button" className="block w-full text-left rounded-md px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50" onClick={onRunResolution} disabled={busy}>
                    Run analysis again
                  </button>
                )}
              </div>
            </details>
          </div>
        </div>
      </div>

      {/* ── Error / quota messages ── */}
      {(resolutionError || advocateError) && (
        <div className="mx-8 mb-4 rounded-xl bg-red-50 border-l-4 border-red-400 px-4 py-3 text-sm font-semibold text-red-600" role="alert">
          Analysis could not finish. Your saved work and evidence remain available. Retry or view a saved demo.
          <details className="mt-1 font-normal text-red-400"><summary>Error details</summary>{resolutionError ?? advocateError}</details>
        </div>
      )}
      {quotaMessage && (
        <div className="mx-8 mb-4 rounded-xl bg-amber-50 border-l-4 border-amber-400 px-4 py-3 text-sm text-amber-800" role="alert">{quotaMessage}</div>
      )}
      {intakeAnalysisError && (
        <div className="mx-8 mb-4 rounded-xl bg-red-50 border-l-4 border-red-400 px-4 py-3 text-sm font-semibold text-red-600" role="alert">
          Statement review failed. Saved accounts remain available.
          <details className="mt-1 font-normal text-red-400"><summary>Error details</summary>{intakeAnalysisError}</details>
        </div>
      )}
      {aiIncomplete && !quotaMessage && (
        <div className="mx-8 mb-4 rounded-xl bg-amber-50 border-l-4 border-amber-400 px-4 py-3 text-sm text-amber-800" role="status">
          AI review is incomplete. The code-calculated decision remains available. Retry to reuse compatible saved stages.
        </div>
      )}

      {/* ── Horizontal Stepper ── */}
      <div className="mx-8 mb-6 flex items-center gap-3">
        <StepperPill index={1} label="Both Sides" state={step1State} />
        <div className={`flex-1 h-0.5 rounded-full ${step2State === "done" ? "bg-green-400" : "bg-gray-200"}`} />
        <StepperPill index={2} label="Evidence" state={step2State} />
        <div className={`flex-1 h-0.5 rounded-full ${step3State === "done" ? "bg-green-400" : "bg-gray-200"}`} />
        <StepperPill index={3} label="AI Review" state={step3State} />
        <div className={`flex-1 h-0.5 rounded-full ${step4State === "done" ? "bg-green-400" : "bg-gray-200"}`} />
        <StepperPill index={4} label="Final Decision" state={step4State} />
      </div>

      {/* ── Workflow Steps (expandable detail cards) ── */}
      {/* Step 1: Hear Both Sides — styled as a large white card */}
      <div className="mx-8 mb-6">
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
          {/* Card header with toggle */}
          <button
            type="button"
            onClick={() => toggle(1)}
            className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
          >
            <div className="flex items-center gap-3">
              <span className={`flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold ${
                openStep === 1 ? "bg-[#131b2b] text-white" : step1State === "done" ? "bg-green-500 text-white" : "bg-[#131b2b] text-white"
              }`}>
                {step1State === "done" && openStep !== 1 ? "✓" : 1}
              </span>
              <div className="text-left">
                <strong className="block text-base font-bold text-[#131b2b]">Hear Both Sides</strong>
                <span className="block text-xs text-gray-400">Rider and driver accounts</span>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {openStep !== 1 && (
                <span className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-500">
                  Action button
                </span>
              )}
              <svg className={`w-4 h-4 text-gray-400 transition-transform ${openStep === 1 ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </div>
          </button>

          {openStep === 1 && (
            <div className="px-6 pb-6 border-t border-gray-50">
              {/* Yellow warning banner */}
              <div className="mt-4 flex items-start gap-2 rounded-lg bg-amber-50/50 border border-amber-200 px-4 py-3">
                <span className="text-amber-600 text-sm flex-shrink-0">⚠</span>
                <p className="text-sm text-amber-800 leading-relaxed">Statements are allegations until checked against evidence.</p>
              </div>

              {/* Phone mockup grid — uses IntakeChatPanel which renders the phones */}
              <div className="mt-4">
                <IntakeChatPanel sourceCaseId={caseData.id} onLifecycleChange={handleIntakeChange} riderName={caseData.rider.name} driverName={caseData.driver.name} />
              </div>

              {partyStatements.some(p => p.facts.length) && <PartyStatementsPanel statements={partyStatements} />}

              <details className="mt-4">
                <summary className="cursor-pointer font-semibold text-[#131b2b] py-2">Original accounts and trip</summary>
                <section className="two-col">
                  <article className="panel">
                    <h3>Rider allegation</h3>
                    <p>{caseData.riderComplaint}</p>
                  </article>
                  <article className="panel">
                    <h3>Driver allegation</h3>
                    <p>{caseData.driverResponse}</p>
                  </article>
                </section>
                <p>{caseData.trip.pickup} / {caseData.trip.destination}</p>
                <p>Quoted {money(caseData.fare.quoted, caseData.fare.currency)} / Charged {money(caseData.fare.actual, caseData.fare.currency)}</p>
                <small>Trip {caseData.trip.tripId} / Policy {caseData.metadata.policyVersion}</small>
                <p>Rider: {caseData.rider.name} ({caseData.rider.id}) / Driver: {caseData.driver.name} ({caseData.driver.id})</p>
                <small>Priority {caseData.metadata.priority} / Submitted {caseData.metadata.submittedAt} / Updated {caseData.metadata.lastUpdated}</small>
              </details>

              {/* Step footer nav */}
              <div className="flex items-center justify-between mt-5 pt-4 border-t border-gray-50">
                <span className="text-xs font-semibold text-gray-400">Step 1 of {TOTAL_STEPS}</span>
                <button
                  type="button"
                  className="rounded-full px-4 py-2 text-xs font-bold bg-[#131b2b] text-white hover:bg-[#1e2a42]"
                  onClick={() => setOpenStep(2)}
                >
                  Next step →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Steps 2–4 use the existing WorkflowStep component for now */}
      <div className="mx-8 pb-8 space-y-3">
        <WorkflowStep index={2} total={TOTAL_STEPS} {...STEPS[1]} state={stateFor(2)} open={openStep === 2} onToggle={() => toggle(2)} onPrev={() => setOpenStep(1)} onNext={() => setOpenStep(3)}>
          <PartyContradictionBanner contradictions={partyContradictions} />
          <section className="panel">
            <h3>Evidence on file</h3>
            <ul className="compact-evidence">
              {caseData.evidence.map(item => <li key={item.id}><span><b>{item.type}</b><span>{item.summary}</span></span><b className={`evidence-status ${item.status.toLowerCase()}`}>{item.status}</b><details><summary>Details</summary><small>{item.id} / {item.source} / {item.timestamp}</small></details></li>)}
            </ul>
          </section>
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
          {decision ? <>
            <DeterministicOutcomePanel resolution={decision} />
            {decision.resolutionMode === "HUMAN_REVIEW" && <p className="human-review">A person must review this case before a refund or charge is approved.</p>}
            <details className="view-details"><summary>Why this decision</summary><ExplanationPanel explanation={resolutionRun!.explanation} counterfactual={resolutionRun!.counterfactual} /></details>
            <details className="view-details"><summary>Audit trail</summary><AuditTrail events={resolutionRun!.audit} /></details>
          </> : <StepPending title="Run analysis to view the final decision">Evidence checks are available in step 2. AI recommendations never control the refund.</StepPending>}
        </WorkflowStep>
      </div>
    </div>
  );
}
