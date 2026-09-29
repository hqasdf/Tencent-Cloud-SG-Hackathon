import type { JudgeResult, DeterministicResolution, AuditEvent } from "../types/judge";
import { humanize, labelize, money } from "../utils/format";

/**
 * Judge panel — split into its two halves.
 *
 * These were once two columns of a single `judge-grid`, and the separation
 * existed so a reader could not conflate them:
 *
 *   advisory      — the AI Judge's recommendation and its justification
 *   deterministic — what code is actually willing to do
 *
 * They are now two consecutive steps in the case workflow instead. The
 * separation is preserved — arguably strengthened — because the sequence itself
 * enforces it: the advisory step is explicitly framed as "it cannot change the
 * outcome" and points forward, and the deterministic step follows as the only
 * place an outcome is stated. What must NOT change is the weight: nothing in
 * the advisory half may ever be rendered with the same authority as the
 * deterministic half, and no value from the Judge may appear as a decision.
 */

function StatusBadge({ status }: { status: JudgeResult["status"] }) {
  return <span className={`judge-status judge-status-${status.toLowerCase()}`}>{labelize(status)}</span>;
}

function ClaimIdList({ ids, empty }: { ids: string[]; empty: string }) {
  if (ids.length === 0) return <p className="judge-empty">{empty}</p>;
  return (
    <div className="judge-ids">
      {ids.map((id) => (
        <code key={id}>{id}</code>
      ))}
    </div>
  );
}

export function JudgeAdvisoryPanel({ judge }: { judge: JudgeResult }) {
  const notRun = judge.status === "NOT_RUN";
  const failed = judge.status === "FAILED";
  const pending = judge.status === "PENDING_HUMAN_REVIEW";

  return (
    <article className="panel judge-panel advisory">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Judge Agent · AI reasoning</span>
          <h3>Advisory recommendation</h3>
        </div>
        <StatusBadge status={judge.status} />
      </div>

      <p className="judge-boundary-note">
        This is a recommendation only. It cannot change the refund, the confidence or the
        escalation state — those are calculated by code and are stated in the next step.
      </p>

      {judge.execution && (
        <div className="call-meta">
          <span>{judge.execution.provider}</span>
          {judge.execution.model && <span>{judge.execution.model}</span>}
          <span>{judge.execution.durationMs} ms</span>
          {judge.execution.totalTokens !== null && <span>{judge.execution.totalTokens} tokens</span>}
        </div>
      )}

      {notRun && (
        <p className="review-reason">
          {judge.failureReason ?? "The Judge did not run."}
          {judge.skipReason && <small className="judge-skip"> ({labelize(judge.skipReason)})</small>}
        </p>
      )}

      {failed && (
        <>
          <p className="review-reason">{judge.failureReason ?? "The Judge did not produce a usable result."}</p>
          {judge.validationIssues.length > 0 && (
            <div className="judge-issues">
              <span className="eyebrow">Rejected by deterministic validation</span>
              {judge.validationIssues.map((issue, index) => (
                <div key={`${issue.code}-${index}`}>
                  <code className="judge-issue-code">{issue.code}</code>
                  <p>{issue.detail}</p>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {!notRun && !failed && (
        <>
          <div className="judge-outcome">
            <span className="eyebrow">Recommended outcome</span>
            <b>{judge.recommendedOutcome ? labelize(judge.recommendedOutcome) : "None"}</b>
            {pending && <small className="judge-pending">Not executable — human review required</small>}
          </div>

          {judge.reasoningSummary && <p className="judge-reasoning">{judge.reasoningSummary}</p>}

          <div className="judge-claims-grid">
            <div>
              <span className="eyebrow">Accepted Rider claims</span>
              <ClaimIdList ids={judge.acceptedRiderClaimIds} empty="None accepted" />
            </div>
            <div>
              <span className="eyebrow">Accepted Driver claims</span>
              <ClaimIdList ids={judge.acceptedDriverClaimIds} empty="None accepted" />
            </div>
            <div>
              <span className="eyebrow">Rejected Rider claims</span>
              <ClaimIdList ids={judge.rejectedRiderClaimIds} empty="None rejected" />
            </div>
            <div>
              <span className="eyebrow">Rejected Driver claims</span>
              <ClaimIdList ids={judge.rejectedDriverClaimIds} empty="None rejected" />
            </div>
          </div>

          <div className="judge-citations">
            <div>
              <span className="eyebrow">Evidence used</span>
              <ClaimIdList ids={judge.evidenceIds} empty="No evidence cited" />
            </div>
            <div>
              <span className="eyebrow">Policy rules used</span>
              <ClaimIdList ids={judge.policyRuleIds} empty="No policy cited" />
            </div>
          </div>

          {judge.uncertainties.length > 0 && (
            <div className="judge-uncertainties">
              <span className="eyebrow">Unresolved uncertainty</span>
              <ul>
                {judge.uncertainties.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </article>
  );
}

export function DeterministicOutcomePanel({ resolution }: { resolution: DeterministicResolution }) {
  const humanReview = resolution.resolutionMode === "HUMAN_REVIEW";
  return (
    <article className="panel judge-panel deterministic">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Deterministic execution · CODE</span>
          <h3>Authoritative outcome</h3>
        </div>
        <span className={`resolution-mode ${resolution.resolutionMode.toLowerCase()}`}>
          {humanize(resolution.resolutionMode)}
        </span>
      </div>

      <p className="judge-boundary-note">
        Computed by the resolution, confidence and escalation engines without reference to the
        Judge. The recommendation in the previous step cannot move any value here — this is what
        actually happens.
      </p>

      <div className="decision-stats">
        <span>
          <small>Final action</small>
          <b>{labelize(resolution.recommendedAction)}</b>
        </span>
        <span>
          <small>Refund amount</small>
          <b>{money(resolution.refundAmount, resolution.currency)}</b>
        </span>
        <span>
          <small>Confidence</small>
          <b>{Math.round(resolution.confidence * 100)}%</b>
        </span>
        <span>
          <small>Human review</small>
          <b>{humanReview ? "Required" : "Not required"}</b>
        </span>
      </div>

      <p className="judge-ruling">{resolution.ruling}</p>
      <p>{resolution.explanation}</p>

      {resolution.escalationReasons.length > 0 && (
        <p className="review-reason">
          Escalation: {resolution.escalationReasons.map(humanize).join(" · ")}
        </p>
      )}
    </article>
  );
}

/**
 * The machine-readable trail behind the run.
 *
 * Every AI step and every code step appended to it, in order. It is shown
 * because "the code checked the AI" is a claim, and this is the evidence for
 * it — but it is not the thing an operator reads first, so it lives at the end
 * of the workflow rather than at the top.
 */
export function AuditTrail({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) return null;
  return (
    <section className="panel judge-audit">
      <span className="eyebrow">Audit trail</span>
      <div className="audit-events">
        {events.map((event, index) => (
          <div className="audit-event" key={`${event.event}-${index}`}>
            <code>{event.event}</code>
            <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
          </div>
        ))}
      </div>
    </section>
  );
}
