import type { AdvocateClaim, AdvocateRunResult, AdvocateSideResult, PipelineStage, RejectedClaim } from "../types/advocate";
import { labelize } from "../utils/format";

/**
 * Stage 4 advocate panels.
 *
 * These replace the old hardcoded `riderCase` / `driverCase` panels entirely.
 * The visible arguments are now the live, code-verified output of the two
 * advocates, and rejected claims stay on screen rather than being hidden.
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
  return (
    <section className={`panel advocate ${failed ? "advocate-failed" : ""}`}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{labelize(side.side)} Advocate</span>
          <h3>{failed ? "Advocate unavailable" : "Structured argument"}</h3>
        </div>
        <span className={`party-badge ${failed ? "failed" : ""}`}>{side.status}</span>
      </div>

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

export interface AdvocateRunPanelProps {
  result: AdvocateRunResult | null;
  running: boolean;
  error: string | null;
  onRun: () => void;
}

export function AdvocateRunPanel({ result, running, error, onRun }: AdvocateRunPanelProps) {
  const judgeStage = result?.pipeline.find((stage) => stage.stage === "JUDGE");

  return (
    <>
      <section className="panel advocate-control">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Stage 4 · Advocates</span>
            <h3>Rider and Driver advocates</h3>
          </div>
          <button onClick={onRun} disabled={running} className="run-advocates">
            {running ? "Running advocates…" : result ? "Run again" : "Run advocates"}
          </button>
        </div>
        <p className="prototype-note">
          CODE calculates the facts. The advocates argue from those facts. CODE then verifies every
          claim. The Judge stage is not part of this milestone and is reported as not run.
        </p>
        {error && <p className="review-reason">{error}</p>}
        {result && (
          <>
            <div className="advocate-meta">
              <span><small>Mode</small><b>{result.agentRun.mode}</b></span>
              <span><small>Provider</small><b>{result.agentRun.provider}</b></span>
              <span><small>Model</small><b>{result.agentRun.model ?? "n/a (mock)"}</b></span>
              <span><small>Prompts</small><b>{result.agentRun.promptVersion}</b></span>
              <span><small>Verified</small><b>{result.verificationSummary.verifiedCount}</b></span>
              <span><small>Rejected</small><b>{result.verificationSummary.rejectedCount}</b></span>
            </div>
            {judgeStage?.status === "NOT_RUN" && (
              <p className="judge-not-run">Judge: not run in this milestone.</p>
            )}
          </>
        )}
      </section>

      {result && (
        <>
          <PipelineStrip pipeline={result.pipeline} />
          <section className="advocates-grid">
            <AdvocatePanel side={result.rider} />
            <AdvocatePanel side={result.driver} />
          </section>
        </>
      )}
    </>
  );
}
