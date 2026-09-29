import type { RebuttalRunResult, RebuttalSideResult, VerifiedRebuttal, RejectedRebuttal } from "../types/rebuttal";
import { labelize } from "../utils/format";

/**
 * Stage 6 cross-examination panel.
 *
 * This sits between the advocates and the Judge, and its job is to make the
 * bounded rebuttal round legible without letting it look like a second Judge.
 *
 * Two things the UI must not blur:
 *
 *  1. VERIFIED versus REJECTED. Both are shown, because a rejected rebuttal is
 *     exactly what a reviewer needs to see — but rejected ones are visually
 *     demoted and labelled "not used by Judge", so nobody reads a failed
 *     argument as one that shaped the outcome.
 *
 *  2. One round, not a debate. The round counter is rendered explicitly. There
 *     is no "continue" affordance because there is no second round to continue.
 */

/** A compact view of one side's response to one opposing claim. */
function VerifiedRow({ rebuttal }: { rebuttal: VerifiedRebuttal }) {
  return (
    <div className="rebuttal-row verified">
      <div className="rebuttal-head">
        <code className="rebuttal-id">{rebuttal.rebuttalId}</code>
        <span className={`stance stance-${rebuttal.stance.toLowerCase()}`}>
          {labelize(rebuttal.stance)}
        </span>
        <span className="rebuttal-verdict verified">Verified</span>
      </div>
      <p className="rebuttal-target">
        Responds to <code>{rebuttal.targetClaimId}</code>
      </p>
      <p className="rebuttal-reasoning">{rebuttal.reasoningSummary}</p>
      {(rebuttal.evidenceIds.length > 0 || rebuttal.policyRuleIds.length > 0) && (
        <div className="rebuttal-refs">
          {rebuttal.evidenceIds.length > 0 && (
            <span>
              <small>Evidence</small>
              {rebuttal.evidenceIds.map((id) => (
                <code key={id}>{id}</code>
              ))}
            </span>
          )}
          {rebuttal.policyRuleIds.length > 0 && (
            <span>
              <small>Policy</small>
              {rebuttal.policyRuleIds.map((id) => (
                <code key={id}>{id}</code>
              ))}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * A rejected rebuttal.
 *
 * Marked loudly rather than hidden. The point of showing it is that the audience
 * can see the model produced something and code refused it — which is the whole
 * argument for having the verification layer.
 */
function RejectedRow({ rebuttal }: { rebuttal: RejectedRebuttal }) {
  return (
    <div className="rebuttal-row rejected">
      <div className="rebuttal-head">
        {rebuttal.targetClaimId ? (
          <code className="rebuttal-id">{rebuttal.targetClaimId}</code>
        ) : (
          <code className="rebuttal-id">No target</code>
        )}
        {rebuttal.stance && <span className="stance">{labelize(rebuttal.stance)}</span>}
        <span className="rebuttal-verdict rejected">Rejected — not used by Judge</span>
      </div>
      <div className="rebuttal-rejection">
        <code className="judge-issue-code">{rebuttal.reason}</code>
        <p>{rebuttal.detail}</p>
      </div>
    </div>
  );
}

function SideColumn({ result }: { result: RebuttalSideResult }) {
  const notRun = result.status === "NOT_RUN";
  const failed = result.status === "FAILED";

  return (
    <article className="panel rebuttal-column">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{labelize(result.side)} rebuttal</span>
          <h3>
            {result.verifiedRebuttals.length} verified
            {result.rejectedRebuttals.length > 0 && ` · ${result.rejectedRebuttals.length} rejected`}
          </h3>
        </div>
        <span className={`rebuttal-status rebuttal-status-${result.status.toLowerCase()}`}>
          {labelize(result.status)}
        </span>
      </div>

      {result.execution && (
        <div className="call-meta">
          <span>{result.execution.provider}</span>
          {result.execution.model && <span>{result.execution.model}</span>}
          <span>{result.execution.durationMs} ms</span>
          {result.execution.totalTokens !== null && <span>{result.execution.totalTokens} tokens</span>}
        </div>
      )}

      {notRun && <p className="review-reason">{result.failureReason ?? "This rebuttal did not run."}</p>}
      {failed && <p className="review-reason">{result.failureReason ?? "This rebuttal failed."}</p>}

      {!notRun && !failed && (
        <>
          {result.overallSummary && <p className="rebuttal-summary">{result.overallSummary}</p>}

          {result.verifiedRebuttals.length === 0 && result.rejectedRebuttals.length === 0 && (
            <p className="judge-empty">
              This side produced no response to the opposing claims. The Judge still runs — a
              missing rebuttal is a side that declined to respond, not a gap to fill.
            </p>
          )}

          {result.verifiedRebuttals.map((rebuttal) => (
            <VerifiedRow key={rebuttal.rebuttalId} rebuttal={rebuttal} />
          ))}

          {result.rejectedRebuttals.map((rebuttal, index) => (
            <RejectedRow key={`rejected-${index}`} rebuttal={rebuttal} />
          ))}

          {result.concededTargetIds.length > 0 && (
            <div className="rebuttal-concessions">
              <span className="eyebrow">Conceded claims</span>
              <div className="judge-ids">
                {result.concededTargetIds.map((id) => (
                  <code key={id}>{id}</code>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </article>
  );
}

/**
 * The trust sequence.
 *
 * Rendered because the pipeline's central claim — that AI output is checked by
 * code repeatedly, not once — is invisible in the output itself. Every step is
 * labelled with which layer owns it, so "CODE VERIFIED" appearing twice reads as
 * deliberate rather than redundant.
 *
 * Laid out as an evenly-flowing numbered grid rather than a single row with
 * arrows: seven steps do not fit on one line at any realistic width, and a row
 * that wraps leaves the arrows pointing at nothing.
 */
export function TrustSequence() {
  const steps: { label: string; owner: "AI" | "CODE" }[] = [
    { label: "Advocates", owner: "AI" },
    { label: "Claim verification", owner: "CODE" },
    { label: "Cross-examination", owner: "AI" },
    { label: "Rebuttal verification", owner: "CODE" },
    { label: "Judge", owner: "AI" },
    { label: "Judge validation", owner: "CODE" },
    { label: "Deterministic execution", owner: "CODE" }
  ];
  return (
    <section className="panel trust-sequence">
      <span className="eyebrow">How this decision was produced</span>
      <p className="trust-note">
        Seven steps, in order. The AI steps propose; the CODE step that follows each one is the
        layer that can refuse.
      </p>
      <ol className="trust-steps">
        {steps.map((step, index) => (
          <li className={`trust-step ${step.owner.toLowerCase()}`} key={step.label}>
            <span className="trust-head">
              <span className="trust-index" aria-hidden="true">
                {index + 1}
              </span>
              <span className="trust-owner">{step.owner}</span>
            </span>
            <b>{step.label}</b>
          </li>
        ))}
      </ol>
    </section>
  );
}

export interface CrossExaminationPanelProps {
  rebuttals: RebuttalRunResult | null;
}

export function CrossExaminationPanel({ rebuttals }: CrossExaminationPanelProps) {
  if (!rebuttals) return null;
  return (
    <section className="cross-examination">
      <div className="panel cross-exam-heading">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Cross-examination · one bounded round</span>
            <h2>Responses to verified claims</h2>
          </div>
          <span className="round-badge">
            Round {rebuttals.round} of {rebuttals.maxRounds}
          </span>
        </div>
        <p className="judge-boundary-note">
          Each side had exactly one opportunity to respond to the other's verified claims. No
          response introduces new evidence or policy, and only responses that passed
          deterministic verification were given to the Judge.
        </p>
      </div>
      <div className="two-col">
        <SideColumn result={rebuttals.rider} />
        <SideColumn result={rebuttals.driver} />
      </div>
    </section>
  );
}
