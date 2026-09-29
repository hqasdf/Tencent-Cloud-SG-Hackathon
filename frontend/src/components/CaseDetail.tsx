import { useEffect, useState } from "react";
import type { CaseAnalysis, DisputeCase } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { ResolutionRunResult } from "../services/caseService";
import { AdvocateArguments } from "./AdvocateRun";
import { CrossExaminationPanel, TrustSequence } from "./CrossExaminationPanel";
import { ExplanationPanel } from "./ExplanationPanel";
import { ReplayBadge, ReplayInvalidBadge } from "./ReplayBadge";
import { AuditTrail, DeterministicOutcomePanel, JudgeAdvisoryPanel } from "./JudgePanel";
import { DeterministicAnalysisPanel } from "./DeterministicAnalysis";
import { StepPending, WorkflowRail, WorkflowStep, type StepState } from "./WorkflowStep";
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
 *  - Every number has one home. The deterministic outcome is stated in step 7
 *    and nowhere else; confidence is stated in step 3 and nowhere else. The
 *    fixture's pre-computed `resolution`, `confidence` and `activity` fields are
 *    deliberately NOT rendered — the live pipeline supersedes them, and showing
 *    both is what created the duplicates.
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
const FIRST_LIVE_STEP = 4;

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

  // A new case is a new workflow: start it from the top.
  useEffect(() => {
    setOpenStep(1);
  }, [caseData.id]);

  // When a run lands, open the first step that has new content. Keyed on the
  // result object, so this fires once per completed run and never fights an
  // operator who has since navigated elsewhere.
  useEffect(() => {
    if (resolutionRun) setOpenStep(FIRST_LIVE_STEP);
  }, [resolutionRun]);

  const hasArguments = resolutionRun !== null || advocateRun !== null;
  const advocatesOnly = resolutionRun === null && advocateRun !== null;

  function stateFor(index: number): StepState {
    if (index <= 3) return "done";
    if (index === FIRST_LIVE_STEP) return hasArguments ? "done" : "ready";
    return resolutionRun ? "done" : "waiting";
  }

  const doneCount = STEPS.filter((_, position) => stateFor(position + 1) === "done").length;
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
            <b>{doneCount}</b> of {TOTAL_STEPS} steps ready
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
                  {resolutionRun.agentRun.mode === "real" ? "REAL MODEL" : "MOCK"}
                </span>
              )}
            </>
          ) : advocateRun ? (
            <span className={`mode-badge ${advocateRun.agentRun.mode}`}>
              {advocateRun.agentRun.mode === "real" ? "REAL MODEL" : "MOCK"}
            </span>
          ) : null}

          <button type="button" className="wf-run" onClick={onRunResolution} disabled={resolutionRunning}>
            {resolutionRunning ? "Running analysis…" : resolutionRun ? "Run again" : "Run full analysis"}
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

      <WorkflowRail steps={STEPS} stateFor={stateFor} current={openStep} onSelect={setOpenStep} />

      <div className="workflow">
        <WorkflowStep
          index={1}
          total={TOTAL_STEPS}
          title={STEPS[0].title}
          summary={STEPS[0].summary}
          state={stateFor(1)}
          open={openStep === 1}
          onToggle={() => toggle(1)}
          onNext={() => setOpenStep(2)}
        >
          <section className="two-col">
            <article className="panel">
              <span className="eyebrow">Rider complaint</span>
              <p>“{caseData.riderComplaint}”</p>
              <small>
                {caseData.rider.name} · {caseData.rider.id}
              </small>
            </article>
            <article className="panel">
              <span className="eyebrow">Driver response</span>
              <p>“{caseData.driverResponse}”</p>
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
        </WorkflowStep>

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
          {analysis ? (
            <DeterministicAnalysisPanel value={analysis} />
          ) : (
            <StepPending title="Not available">
              The calculated facts for this case could not be loaded.
            </StepPending>
          )}
        </WorkflowStep>

        <WorkflowStep
          index={4}
          total={TOTAL_STEPS}
          title={STEPS[3].title}
          summary={STEPS[3].summary}
          state={stateFor(4)}
          stateLabel={hasArguments ? undefined : "Start here"}
          open={openStep === 4}
          onToggle={() => toggle(4)}
          onPrev={() => setOpenStep(3)}
          onNext={() => setOpenStep(5)}
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

        <WorkflowStep
          index={5}
          total={TOTAL_STEPS}
          title={STEPS[4].title}
          summary={STEPS[4].summary}
          state={stateFor(5)}
          open={openStep === 5}
          onToggle={() => toggle(5)}
          onPrev={() => setOpenStep(4)}
          onNext={() => setOpenStep(6)}
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

        <WorkflowStep
          index={6}
          total={TOTAL_STEPS}
          title={STEPS[5].title}
          summary={STEPS[5].summary}
          state={stateFor(6)}
          stateLabel={resolutionRun ? "Advisory" : undefined}
          open={openStep === 6}
          onToggle={() => toggle(6)}
          onPrev={() => setOpenStep(5)}
          onNext={() => setOpenStep(7)}
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

        <WorkflowStep
          index={7}
          total={TOTAL_STEPS}
          title={STEPS[6].title}
          summary={STEPS[6].summary}
          state={stateFor(7)}
          open={openStep === 7}
          onToggle={() => toggle(7)}
          onPrev={() => setOpenStep(6)}
          onNext={() => setOpenStep(8)}
        >
          {resolutionRun ? (
            <>
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

        <WorkflowStep
          index={8}
          total={TOTAL_STEPS}
          title={STEPS[7].title}
          summary={STEPS[7].summary}
          state={stateFor(8)}
          open={openStep === 8}
          onToggle={() => toggle(8)}
          onPrev={() => setOpenStep(7)}
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
