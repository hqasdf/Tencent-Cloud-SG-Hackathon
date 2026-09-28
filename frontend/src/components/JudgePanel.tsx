import type { JudgeResult, DeterministicResolution, AuditEvent } from "../types/judge";
import { labelize, money } from "../utils/format";

/**
 * Stage 5 Judge panel.
 *
 * The whole point of this component is the visual separation between two
 * columns that a user could otherwise conflate:
 *
 *   left  — the AI Judge's advisory recommendation and its justification
 *   right — what deterministic code is actually willing to do
 *
 * They are given different headings, different accents and an explicit note,
 * because presenting an advisory recommendation with the same weight as an
 * authorised action is the most dangerous presentation error this stage could
 * make. Nothing on the left can move anything on the right.
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

function AdvisoryColumn({ judge }: { judge: JudgeResult }) {
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
        escalation state — those are calculated by code, shown on the right.
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

function DeterministicColumn({ resolution }: { resolution: DeterministicResolution }) {
  const humanReview = resolution.resolutionMode === "HUMAN_REVIEW";
  return (
    <article className="panel judge-panel deterministic">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Deterministic execution · CODE</span>
          <h3>Authoritative outcome</h3>
        </div>
        <span className={`resolution-mode ${resolution.resolutionMode.toLowerCase()}`}>
          {labelize(resolution.resolutionMode)}
        </span>
      </div>

      <p className="judge-boundary-note">
        Computed by the resolution, confidence and escalation engines without reference to the
        Judge. This is what actually happens.
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
          Escalation: {resolution.escalationReasons.map((reason) => labelize(reason)).join(", ")}
        </p>
      )}
    </article>
  );
}

function AuditStrip({ events }: { events: AuditEvent[] }) {
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

export interface JudgePanelProps {
  judge: JudgeResult | null;
  resolution: DeterministicResolution | null;
  audit: AuditEvent[];
}

export function JudgePanel({ judge, resolution, audit }: JudgePanelProps) {
  if (!judge || !resolution) return null;
  return (
    <>
      <section className="judge-grid">
        <AdvisoryColumn judge={judge} />
        <DeterministicColumn resolution={resolution} />
      </section>
      <AuditStrip events={audit} />
    </>
  );
}
