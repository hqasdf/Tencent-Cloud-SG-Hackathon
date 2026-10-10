import { useCallback, useEffect, useMemo, useState } from "react";
import type { CaseAnalysis, DisputeCase, PartyStatement, PartyContradiction } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { IntakeCase } from "../types/intake";
import type { ResolutionRunResult } from "../services/caseService";
import { intakeService } from "../services/intakeService";
import { AdvocateArguments } from "./AdvocateRun";
import { CrossExaminationPanel, TrustSequence } from "./CrossExaminationPanel";
import { ExplanationPanel } from "./ExplanationPanel";
import { IntakeChatPanel } from "./IntakeChatPanel";
import { ReplayBadge, ReplayInvalidBadge } from "./ReplayBadge";
import { AuditTrail, DeterministicOutcomePanel, JudgeAdvisoryPanel } from "./JudgePanel";
import { DeterministicAnalysisPanel } from "./DeterministicAnalysis";
import { StepPending, WorkflowRail, WorkflowStep, type StepState } from "./WorkflowStep";
import { bothSettledOk, isSettledNotOk, isSettledOk } from "../types/workflow";
import type { SideStages, StageState } from "../types/workflow";
import { labelize, money } from "../utils/format";

/**
 * The case workspace.
 *
 * This is a numbered workflow rather than a report. The previous version was a
 * single scroll of roughly sixteen sections: the answer — outcome, refund,
 * confidence — sat at the bottom, after the timeline, the evidence table and an
 * audit log, and the same numbers appeared in up to three places. An operator
 * triaging a queue could not tell what to read first, or which of the two run
 * buttons was the one they wanted.
 *
 * The rules this file follows:
 *
 *  - One primary action. "Run full analysis" appears exactly once, in the
 *    toolbar. The cheaper advocates-only path is a subordinate ghost button
 *    beside it and disappears once a full run exists.
 *  - Every number has one home. The deterministic outcome is stated in the
 *    decision step and nowhere else; confidence is stated in the facts step
 *    and nowhere else.
 *  - No internal stage names. "Stage 4", "Stage 5", "Stage 6" and "the panel
 *    above" are developer vocabulary; the steps are named after what the
 *    operator is looking at.
 *  - The Judge never looks authoritative. It gets its own step, framed as
 *    advisory, immediately before the step that states what code will do.
 */

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

const STEPS: { title: string; short: string; summary: string }[] = [
  {
    title: "AI intake interviews",
    short: "AI intake",
    summary: "The AI interviews the rider and driver to gather each side's account."
  },
  {
    title: "Review the case",
    short: "The case",
    summary: "What the rider and the driver each say, and the trip record."
  },
  {
    title: "Examine the evidence",
    short: "Evidence",
    summary: "The timeline and every item on file."
  },
  {
    title: "Check the facts",
    short: "The facts",
    summary: "What the numbers and the policy rules actually show."
  },
  {
    title: "Read the arguments",
    short: "Arguments",
    summary: "Each side's claims, with code checking every one of them."
  },
  {
    title: "Cross-examination",
    short: "Cross-exam",
    summary: "One bounded round of responses to the verified claims."
  },
  {
    title: "The Judge's view",
    short: "Judge",
    summary: "A recommendation only. It cannot change the outcome."
  },
  {
    title: "The decision",
    short: "Decision",
    summary: "What code will actually do — the refund, and whether a human is needed."
  },
  {
    title: "Why this decision",
    short: "Why",
    summary: "The decisive facts, the policy thresholds, and the audit trail."
  }
];

const TOTAL_STEPS = STEPS.length;

/** Where the full-analysis result is produced, so a finished run can jump here. */
const FIRST_LIVE_STEP = 5;

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

/** Extract the full intake analysis object from the intake case, if present. */
function extractIntakeAnalysis(intakeCase: IntakeCase | null): Record<string, unknown> | null {
  if (!intakeCase?.finalAnalysis) return null;
  return intakeCase.finalAnalysis as Record<string, unknown>;
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
                    <code>{f.key}</code>
                    <span>{f.value}</span>
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
        The AI intake interview detected that the rider and driver gave conflicting accounts on
        the following points. This has lowered the confidence score and forced the case to human
        review.
      </p>
      <div className="contradiction-list">
        {contradictions.map((c, i) => (
          <div className="contradiction-row" key={i}>
            <code>{c.factKey}</code>
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

/** Summary of the intake analysis — shown right after the AI interviews complete.
 *  Surfaces the dispute type, confidence, resolution mode, party statements,
 *  and contradictions so the operator sees the intake contribution immediately. */
function IntakeAnalysisSummary({
  intakeAnalysis,
  partyStatements,
  partyContradictions,
}: {
  intakeAnalysis: Record<string, unknown> | null;
  partyStatements: PartyStatement[];
  partyContradictions: PartyContradiction[];
}) {
  if (!intakeAnalysis) return null;

  const confidence = intakeAnalysis.confidence as Record<string, number> | undefined;
  const resolutionMode = intakeAnalysis.resolutionMode as string | undefined;
  const disputeType = intakeAnalysis.disputeType as string | undefined;
  const escalationReasons = (intakeAnalysis.escalationReasons as string[]) ?? [];
  const overallConfidence = confidence?.overallConfidence;
  const isHumanReview = resolutionMode === "HUMAN_REVIEW";

  return (
    <section className={`intake-analysis-summary ${isHumanReview ? "review" : "auto"}`}>
      <div className="intake-analysis-header">
        <span className="eyebrow">AI intake analysis</span>
        <h3>What the intake interviews found</h3>
      </div>

      <div className="intake-analysis-stats">
        {disputeType && (
          <div>
            <small>Detected dispute</small>
            <b>{labelize(disputeType)}</b>
          </div>
        )}
        {overallConfidence !== undefined && (
          <div>
            <small>Confidence</small>
            <b>{Math.round(overallConfidence * 100)}%</b>
          </div>
        )}
        {resolutionMode && (
          <div>
            <small>Resolution mode</small>
            <b className={isHumanReview ? "review" : "auto"}>
              {isHumanReview ? "Human review" : "Auto resolve"}
            </b>
          </div>
        )}
        {partyStatements.length > 0 && (
          <div>
            <small>Party facts extracted</small>
            <b>{partyStatements.reduce((n, ps) => n + ps.facts.length, 0)}</b>
          </div>
        )}
      </div>

      {escalationReasons.length > 0 && (
        <p className="review-reason">
          Escalation: {escalationReasons.map((r) => r.replace(/_/g, " ").toLowerCase()).join(" · ")}
        </p>
      )}

      <PartyStatementsPanel statements={partyStatements} />
      <PartyContradictionBanner contradictions={partyContradictions} />
    </section>
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
  onRunResolution
}: CaseDetailProps) {
  const [openStep, setOpenStep] = useState(1);
  const [intakeCase, setIntakeCase] = useState<IntakeCase | null>(null);

  const intakeComplete = !!(
    intakeCase?.lifecycle === "READY_FOR_ANALYSIS" ||
    intakeCase?.lifecycle === "AUTO_RESOLVED" ||
    intakeCase?.lifecycle === "HUMAN_REVIEW"
  );

  const handleIntakeChange = useCallback((ic: IntakeCase) => {
    setIntakeCase(ic);
  }, []);

  // Extract intake analysis data (party statements + contradictions) for display
  const partyStatements = useMemo(() => extractPartyStatements(intakeCase), [intakeCase]);
  const partyContradictions = useMemo(() => extractPartyContradictions(intakeCase), [intakeCase]);
  const intakeAnalysis = useMemo(() => extractIntakeAnalysis(intakeCase), [intakeCase]);

  // When the intake interviews are complete (READY_FOR_ANALYSIS), auto-run the
  // intake analysis so party statements and contradiction detection are
  // computed and saved to finalAnalysis.
  useEffect(() => {
    if (!intakeCase || intakeCase.lifecycle !== "READY_FOR_ANALYSIS") return;
    let cancelled = false;
    void intakeService.analyse(intakeCase.id).then(() => {
      if (cancelled) return;
      // The analysis is saved server-side; refresh the intake case to load
      // finalAnalysis with party statements and contradictions.
      void intakeService.getCase(intakeCase.id).then((refreshed) => {
        if (!cancelled) setIntakeCase(refreshed);
      });
    }).catch(() => {
      // Analysis failure is non-fatal — the case workspace still works.
    });
    return () => { cancelled = true; };
  }, [intakeCase?.id, intakeCase?.lifecycle]);

  // A new case is a new workflow: start it from the top.
  useEffect(() => {
    setOpenStep(1);
    setIntakeCase(null);
  }, [caseData.id]);

  // When a run lands, open the first step that has new content.
  useEffect(() => {
    if (resolutionRun) setOpenStep(FIRST_LIVE_STEP);
  }, [resolutionRun]);

  // When intake completes, auto-advance to step 2 (Review the case)
  useEffect(() => {
    if (intakeComplete && openStep === 1) setOpenStep(2);
  }, [intakeComplete, openStep]);

  const hasArguments = resolutionRun !== null || advocateRun !== null;

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
    // Step 1: AI intake
    if (index === 1) return intakeComplete ? "done" : "ready";
    // Steps 2-3: case review, evidence — always available (static data)
    if (index <= 3) return "done";
    // Step 4: deterministic analysis (facts)
    if (index === 4) return fromStage(workflow?.deterministic, "done");
    // Step 5: advocates
    if (index === 5) {
      return workflow ? fromPair(workflow.advocates, "ready") : hasArguments ? "done" : "ready";
    }
    // Without a state machine there is no evidence that a later stage ran, so
    // the honest answer is "not run" rather than the optimistic "done".
    if (!workflow) return "waiting";
    // Step 6: rebuttals
    if (index === 6) return fromPair(workflow.rebuttals, "waiting");
    // Step 7: judge
    if (index === 7) return fromStage(workflow.judge, "waiting");
    // Step 8: final resolution / decision
    if (index === 8) return fromStage(workflow.finalResolution, "waiting");
    // Step 9: explanation
    return fromStage(workflow.explanation, "waiting");
  }

  const stepStates = STEPS.map((_, position) => stateFor(position + 1));
  const doneCount = stepStates.filter((state) => state === "done").length;
  const failedCount = stepStates.filter((state) => state === "failed").length;
  const blockedCount = stepStates.filter((state) => state === "blocked").length;

  /**
   * Pill text for the two steps whose label describes a condition, not a state.
   */
  const startLabel = stateFor(5) === "ready" ? "Start here" : undefined;
  const judgeLabel = stateFor(7) === "done" ? "Advisory" : undefined;
  const statusTone =
    caseData.status === "Auto Resolved" ? "auto" : caseData.status === "Human Review" ? "review" : "neutral";

  const toggle = (index: number) => setOpenStep((current) => (current === index ? 0 : index));

  return (
    <main className="content">
      <header className="case-header">
        <div>
          <div className="breadcrumb">Disputes / {caseData.id}</div>
          <h1>{caseData.title}</h1>
          <p>{caseData.description}</p>
        </div>
        <div className="case-header-side">
          <span className={`mode ${statusTone}`}>{caseData.status}</span>
          <small>{labelize(caseData.disputeType)}</small>
        </div>
      </header>

      <section className="workflow-toolbar">
        <div className="workflow-toolbar-left">
          <strong>Dispute workflow</strong>
          <span className="workflow-progress">
            <b>{doneCount}</b> of {TOTAL_STEPS} steps complete
            {failedCount > 0 && (
              <em className="workflow-flag failed">{failedCount} failed</em>
            )}
            {blockedCount > 0 && (
              <em className="workflow-flag blocked">{blockedCount} blocked</em>
            )}
          </span>
        </div>
        <div className="workflow-toolbar-actions">
          {/*
            Provenance is stated once, here, where the button is — so it applies
            to whatever the steps below are currently showing. A replayed run
            never renders the model badge: `agentRun.mode` describes the
            configured provider, not whether a model was actually called, and a
            FULL_AI replay can legitimately report "real" while spending nothing.
          */}
          {resolutionRun ? (
            <>
              <ReplayBadge metadata={resolutionRun.replayMetadata} />
              <ReplayInvalidBadge metadata={resolutionRun.replayMetadata} />
              {!resolutionRun.replayMetadata.replayed && (
                <span className={`mode-badge ${resolutionRun.agentRun.mode}`}>
                  {resolutionRun.replayMetadata.source === "MOCK_DEMO" ? "MOCK DEMO" : "LIVE"}
                </span>
              )}
            </>
          ) : advocateRun ? (
            <span className={`mode-badge ${advocateRun.agentRun.mode}`}>
              {advocateRun.agentRun.mode === "real" ? "REAL MODEL" : "MOCK"}
            </span>
          ) : null}

          <button type="button" className="wf-run" onClick={onRunResolution} disabled={resolutionRunning}>
            {resolutionRunning ? "Running analysis…" : resolutionRun ? "Retry / reuse completed work" : "Run full analysis"}
          </button>

          {/* The cheaper two-call path. Subordinate, and only offered before a
              full run exists — after that it would be a second, weaker button. */}
          {!resolutionRun && (
            <button
              type="button"
              className="wf-run-ghost"
              onClick={onRunAdvocates}
              disabled={advocateRunning || resolutionRunning}
            >
              {advocateRunning ? "Running advocates…" : "Advocates only"}
            </button>
          )}
        </div>
      </section>

      {(resolutionError || advocateError) && (
        <p className="workflow-alert">{resolutionError ?? advocateError}</p>
      )}

      {/*
        One plain-language statement of what the AI half of the pipeline could
        not do. Without it, an operator reading a case whose advocates failed
        sees an empty arguments panel and an empty Judge panel and has to infer
        the cause from two separate step bodies.
      */}
      {aiIncomplete && (
        <p className="workflow-notice" role="status">
          <b>The AI stages did not complete for this case.</b> Cross-examination and the
          Judge&rsquo;s view could not run, so there is no AI recommendation. The decision and
          the explanation below are calculated by code and are unaffected.
        </p>
      )}

      <WorkflowRail steps={STEPS} stateFor={stateFor} current={openStep} onSelect={setOpenStep} />

      <div className="workflow">
        {/* Step 1: AI intake interviews */}
        <WorkflowStep
          index={1}
          total={TOTAL_STEPS}
          title={STEPS[0].title}
          summary={STEPS[0].summary}
          state={stateFor(1)}
          stateLabel={intakeComplete ? undefined : "Start here"}
          open={openStep === 1}
          onToggle={() => toggle(1)}
          onNext={() => setOpenStep(2)}
        >
          <IntakeChatPanel
            sourceCaseId={caseData.id}
            onLifecycleChange={handleIntakeChange}
          />
          <IntakeAnalysisSummary
            intakeAnalysis={intakeAnalysis}
            partyStatements={partyStatements}
            partyContradictions={partyContradictions}
          />
        </WorkflowStep>

        {/* Step 2: Review the case */}
        <WorkflowStep
          index={2}
          total={TOTAL_STEPS}
          title={STEPS[1].title}
          summary={STEPS[1].summary}
          state={stateFor(2)}
          open={openStep === 2}
          onToggle={() => toggle(2)}
          onPrev={() => setOpenStep(1)}
          onNext={() => setOpenStep(3)}
        >
          <section className="two-col">
            <article className="panel">
              <span className="eyebrow">Rider complaint</span>
              <p>"{caseData.riderComplaint}"</p>
              <small>
                {caseData.rider.name} · {caseData.rider.id}
              </small>
            </article>
            <article className="panel">
              <span className="eyebrow">Driver response</span>
              <p>"{caseData.driverResponse}"</p>
              <small>
                {caseData.driver.name} · {caseData.driver.id}
              </small>
            </article>
          </section>
          <section className="summary-grid">
            <article>
              <span>Trip</span>
              <b>{caseData.trip.tripId}</b>
              <small>
                {caseData.trip.pickup} → {caseData.trip.destination}
              </small>
            </article>
            <article>
              <span>Fare difference</span>
              <b>{money(caseData.fare.difference, caseData.fare.currency)}</b>
              <small>
                Quoted {money(caseData.fare.quoted, caseData.fare.currency)} · final{" "}
                {money(caseData.fare.actual, caseData.fare.currency)}
              </small>
            </article>
            <article>
              <span>Case metadata</span>
              <b>{caseData.metadata.priority} priority</b>
              <small>
                Policy {caseData.metadata.policyVersion} · updated {caseData.metadata.lastUpdated}
              </small>
            </article>
          </section>
          <PartyStatementsPanel statements={partyStatements} />
          <PartyContradictionBanner contradictions={partyContradictions} />
        </WorkflowStep>

        {/* Step 3: Examine the evidence */}
        <WorkflowStep
          index={3}
          total={TOTAL_STEPS}
          title={STEPS[2].title}
          summary={STEPS[2].summary}
          state={stateFor(3)}
          open={openStep === 3}
          onToggle={() => toggle(3)}
          onPrev={() => setOpenStep(2)}
          onNext={() => setOpenStep(4)}
        >
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Timeline</span>
                <h3>What happened, in order</h3>
              </div>
            </div>
            <div className="timeline">
              {caseData.timeline.map((event) => (
                <div key={event.id} className={`timeline-event ${event.severity ?? "normal"}`}>
                  <time>{event.timestamp}</time>
                  <i />
                  <div>
                    <b>{event.type}</b>
                    <p>{event.description}</p>
                    <code>{event.evidenceIds.join(" · ")}</code>
                  </div>
                </div>
              ))}
            </div>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Evidence</span>
                <h3>Case record</h3>
              </div>
              <span className="evidence-count">{caseData.evidence.length} items</span>
            </div>
            <div className="evidence-table">
              <div className="table-head">
                <span>ID</span>
                <span>Type</span>
                <span>Source</span>
                <span>Summary</span>
                <span>Status</span>
              </div>
              {caseData.evidence.map((item) => (
                <div className="evidence-row" key={item.id}>
                  <code>{item.id}</code>
                  <span>{item.type}</span>
                  <span>{item.source}</span>
                  <span>{item.summary}</span>
                  <b className={`evidence-status ${item.status.toLowerCase()}`}>{item.status}</b>
                </div>
              ))}
            </div>
          </section>
        </WorkflowStep>

        {/* Step 4: Check the facts */}
        <WorkflowStep
          index={4}
          total={TOTAL_STEPS}
          title={STEPS[3].title}
          summary={STEPS[3].summary}
          state={stateFor(4)}
          open={openStep === 4}
          onToggle={() => toggle(4)}
          onPrev={() => setOpenStep(3)}
          onNext={() => setOpenStep(5)}
        >
          {analysis ? (
            <DeterministicAnalysisPanel value={analysis} />
          ) : (
            <StepPending title="Not available">
              The calculated facts for this case could not be loaded.
            </StepPending>
          )}
        </WorkflowStep>

        {/* Step 5: Read the arguments */}
        <WorkflowStep
          index={5}
          total={TOTAL_STEPS}
          title={STEPS[4].title}
          summary={STEPS[4].summary}
          state={stateFor(5)}
          stateLabel={startLabel}
          open={openStep === 5}
          onToggle={() => toggle(5)}
          onPrev={() => setOpenStep(4)}
          onNext={() => setOpenStep(6)}
        >
          {resolutionRun ? (
            <AdvocateArguments
              rider={resolutionRun.rider}
              driver={resolutionRun.driver}
              pipeline={resolutionRun.pipeline}
              verificationSummary={resolutionRun.verificationSummary}
            />
          ) : advocateRun ? (
            <>
              <p className="judge-boundary-note">
                Advocates only. Cross-examination, the Judge and the decision have not run yet —
                use <b>Run full analysis</b> above to complete the workflow.
              </p>
              <AdvocateArguments
                rider={advocateRun.rider}
                driver={advocateRun.driver}
                pipeline={advocateRun.pipeline}
                verificationSummary={advocateRun.verificationSummary}
              />
            </>
          ) : (
            <StepPending title="No arguments yet">
              Running the analysis asks a Rider advocate and a Driver advocate to argue from the
              facts code has already calculated. Every claim they make is then checked against the
              case record, and anything that fails the check is thrown away before the Judge sees
              it. Use <b>Run full analysis</b> above to produce them.
            </StepPending>
          )}
        </WorkflowStep>

        {/* Step 6: Cross-examination */}
        <WorkflowStep
          index={6}
          total={TOTAL_STEPS}
          title={STEPS[5].title}
          summary={STEPS[5].summary}
          state={stateFor(6)}
          open={openStep === 6}
          onToggle={() => toggle(6)}
          onPrev={() => setOpenStep(5)}
          onNext={() => setOpenStep(7)}
        >
          {resolutionRun ? (
            <CrossExaminationPanel rebuttals={resolutionRun.rebuttals} />
          ) : (
            <StepPending title="Not run yet">
              Cross-examination is part of a full analysis run. It gives each side exactly one
              chance to answer the other's verified claims, then checks those answers the same way.
            </StepPending>
          )}
        </WorkflowStep>

        {/* Step 7: The Judge's view */}
        <WorkflowStep
          index={7}
          total={TOTAL_STEPS}
          title={STEPS[6].title}
          summary={STEPS[6].summary}
          state={stateFor(7)}
          stateLabel={judgeLabel}
          open={openStep === 7}
          onToggle={() => toggle(7)}
          onPrev={() => setOpenStep(6)}
          onNext={() => setOpenStep(8)}
        >
          {resolutionRun ? (
            <JudgeAdvisoryPanel judge={resolutionRun.judge} />
          ) : (
            <StepPending title="Not run yet">
              The Judge only sees material that has passed verification in the steps above. Its
              recommendation is advisory — the authoritative outcome is calculated by code in the
              next step.
            </StepPending>
          )}
        </WorkflowStep>

        {/* Step 8: The decision */}
        <WorkflowStep
          index={8}
          total={TOTAL_STEPS}
          title={STEPS[7].title}
          summary={STEPS[7].summary}
          state={stateFor(8)}
          open={openStep === 8}
          onToggle={() => toggle(8)}
          onPrev={() => setOpenStep(7)}
          onNext={() => setOpenStep(9)}
        >
          {resolutionRun ? (
            <>
              <PartyContradictionBanner contradictions={partyContradictions} />
              <DeterministicOutcomePanel resolution={resolutionRun.deterministicResolution} />
              {/*
                Not `caseData.humanReviewSummary`. That field is the server's
                "Deterministic escalation reasons: A, B, C" string, which is the
                same list the outcome panel above already renders — and renders
                in readable words rather than raw enum codes. Showing both was
                the duplicate this redesign set out to remove. What is genuinely
                missing for an operator is the consequence, so that is what this
                states instead.
              */}
              {resolutionRun.deterministicResolution.resolutionMode === "HUMAN_REVIEW" && (
                <section className="human-review">
                  <span className="eyebrow">Action required</span>
                  <p>
                    The deterministic engine will not act on this case by itself. It is escalated
                    for a human decision, for the reasons listed above.
                  </p>
                </section>
              )}
              <TrustSequence />
            </>
          ) : (
            <StepPending title="Not run yet">
              The refund, the confidence and whether a human is needed are all calculated by code
              once the steps above have run. Nothing the Judge recommends moves these values.
            </StepPending>
          )}
        </WorkflowStep>

        {/* Step 9: Why this decision */}
        <WorkflowStep
          index={9}
          total={TOTAL_STEPS}
          title={STEPS[8].title}
          summary={STEPS[8].summary}
          state={stateFor(9)}
          open={openStep === 9}
          onToggle={() => toggle(9)}
          onPrev={() => setOpenStep(8)}
          nextLabel="Back to the case"
          onNext={() => setOpenStep(1)}
        >
          {resolutionRun ? (
            <>
              <ExplanationPanel
                explanation={resolutionRun.explanation}
                counterfactual={resolutionRun.counterfactual}
              />
              <AuditTrail events={resolutionRun.audit} />
            </>
          ) : (
            <StepPending title="Not run yet">
              This is assembled from the run: which facts were decisive, which policy thresholds
              were met, and what would have had to be different for the outcome to change.
            </StepPending>
          )}
        </WorkflowStep>
      </div>
    </main>
  );
}
