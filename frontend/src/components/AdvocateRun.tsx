import type { AdvocateClaim, AdvocateSideResult, PipelineStage, RejectedClaim, VerificationSummary } from "../types/advocate";
import { humanize, labelize } from "../utils/format";

/**
 * The two advocates' arguments, as the case workflow renders them.
 *
 * These replace the old hardcoded `riderCase` / `driverCase` panels entirely.
 * The visible arguments are the live, code-verified output of the two
 * advocates, and rejected claims stay on screen rather than being hidden.
 *
 * Note what is NOT here: a run button, a provider badge, or a "Stage 4" label.
 * The action that produces this content lives once, in the workflow toolbar, and
 * provenance is stated once, beside it. Rendering a second run button here is
 * what previously made the page ambiguous about which button to press.
 */

function ClaimCard({ claim }: { claim: AdvocateClaim }) {
  return (
    <article className="claim">
      <div className="claim-top">
        <b className="verified">VERIFIED</b>
        <code>{claim.claimId}</code>
        <span className={`importance ${claim.importance.toLowerCase()}`}>{claim.importance}</span>
      </div>
      <p>{claim.claim}</p>
      {claim.assertedFacts.length > 0 && (
        <div className="asserted-facts">
          {claim.assertedFacts.map((fact) => (
            <span key={fact.fact}>
              <code>{fact.fact}</code>
              <b>{String(fact.value)}</b>
            </span>
          ))}
        </div>
      )}
      <p className="reasoning">{claim.reasoningSummary}</p>
      <div className="citation">
        <span>Evidence: {claim.evidenceIds.join(", ")}</span>
        <span>Policy: {claim.policyRefs.join(", ")}</span>
      </div>
    </article>
  );
}

function RejectedCard({ claim }: { claim: RejectedClaim }) {
  return (
    <article className="claim rejected-claim">
      <div className="claim-top">
        <b className="rejected">REJECTED BY VERIFIER</b>
        <code>{claim.claimId}</code>
      </div>
      <p>{claim.claim}</p>
      <small className="rejected">{claim.reason}</small>
      <p className="reasoning">{claim.detail}</p>
      {claim.evidenceIds.length > 0 && (
        <div className="citation">
          <span>Cited evidence: {claim.evidenceIds.join(", ")}</span>
        </div>
      )}
    </article>
  );
}

function AdvocatePanel({ side }: { side: AdvocateSideResult }) {
  const failed = side.status === "FAILED";
  const execution = side.execution;
  return (
    <section className={`panel advocate ${failed ? "advocate-failed" : ""}`}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{labelize(side.side)} Advocate</span>
          <h3>{failed ? "Advocate unavailable" : "Structured argument"}</h3>
        </div>
        <span className={`party-badge ${failed ? "failed" : ""}`}>{side.status}</span>
      </div>

      {/* Per-call cost signal. Latency and tokens belong to this advocate's own
          model call, not to the run as a whole, so they sit next to the result. */}
      {execution && (
        <div className="call-meta">
          <span>{execution.provider}</span>
          {execution.model && <span>{execution.model}</span>}
          <span>{execution.durationMs} ms</span>
          {execution.totalTokens !== null && <span>{execution.totalTokens} tokens</span>}
          {execution.generatedClaimCount > 0 && (
            <span>{execution.generatedClaimCount} claims generated</span>
          )}
          {execution.malformedOutput && <span className="call-meta-bad">unusable output</span>}
        </div>
      )}

      {failed ? (
        <p className="review-reason">{side.failureReason ?? "The advocate could not produce an argument."}</p>
      ) : (
        <>
          <p>{side.summary}</p>
          <div className="outcome-row">
            <span className="eyebrow">Requested outcome</span>
            <b>{side.requestedOutcome ? labelize(side.requestedOutcome) : "None"}</b>
            {side.contextAcknowledged && <small className="acknowledged">Context acknowledged</small>}
          </div>
          {side.verifiedClaims.map((claim) => <ClaimCard claim={claim} key={claim.claimId} />)}
          {side.rejectedClaims.length > 0 && (
            <div className="rejection-block">
              <span className="eyebrow">
                Rejected {side.rejectedClaims.length} claim{side.rejectedClaims.length === 1 ? "" : "s"}
              </span>
              <p className="rejection-note">
                These assertions failed deterministic verification and were not used. They are shown
                exactly as produced, for audit.
              </p>
              {side.rejectedClaims.map((claim) => <RejectedCard claim={claim} key={claim.claimId} />)}
            </div>
          )}
          {side.warnings.length > 0 && (
            <div className="warning-block">
              {side.warnings.map((warning, index) => (
                // A single claim can raise several warnings of the same code (for
                // example disputing two pieces of evidence), so the claim id and
                // code alone are not unique. Include the index.
                <small key={`${warning.claimId}-${warning.code}-${index}`}>
                  {warning.claimId} · {labelize(warning.code)}: {warning.detail}
                </small>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function PipelineStrip({ pipeline }: { pipeline: PipelineStage[] }) {
  return (
    <div className="pipeline-strip">
      {pipeline.map((stage) => (
        <div className={`pipeline-node ${stage.status.toLowerCase()}`} key={stage.stage}>
          <span>{labelize(stage.stage)}</span>
          <b>{labelize(stage.status)}</b>
        </div>
      ))}
    </div>
  );
}

export interface AdvocateArgumentsProps {
  rider: AdvocateSideResult;
  driver: AdvocateSideResult;
  pipeline: PipelineStage[];
  verificationSummary: VerificationSummary;
}

/**
 * Both sides' arguments, plus the headline verification result.
 *
 * The verified/rejected counts are hoisted above the two columns because that
 * ratio is the single number that tells an operator how much of what the models
 * said survived contact with the case record.
 */
export function AdvocateArguments({ rider, driver, pipeline, verificationSummary }: AdvocateArgumentsProps) {
  return (
    <>
      <div className="advocate-meta">
        <span>
          <small>Claims verified by code</small>
          <b>{verificationSummary.verifiedCount}</b>
        </span>
        <span>
          <small>Claims rejected by code</small>
          <b>{verificationSummary.rejectedCount}</b>
        </span>
        <span>
          <small>Rejection reasons</small>
          <b>{verificationSummary.rejectionReasons.length === 0 ? "None" : verificationSummary.rejectionReasons.map(humanize).join(" · ")}</b>
        </span>
      </div>
      <PipelineStrip pipeline={pipeline} />
      <section className="advocates-grid">
        <AdvocatePanel side={rider} />
        <AdvocatePanel side={driver} />
      </section>
    </>
  );
}
