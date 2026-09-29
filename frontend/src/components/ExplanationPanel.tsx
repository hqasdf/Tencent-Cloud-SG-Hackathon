import type { DecisionCounterfactual, DecisionExplanation } from "../types/rebuttal";
import { labelize } from "../utils/format";

/**
 * Stage 6 explainability panel.
 *
 * Both halves of this panel are produced by code, and the component says so. The
 * distinction matters because a reader who assumes an LLM wrote "what would have
 * changed the decision?" would reasonably suspect the thresholds were invented.
 * They are not: every threshold shown is a policy rule's own `requiredValue`.
 *
 * Nothing here is generated. The panel is a projection of the verified claims,
 * the verified rebuttals and the PolicyTwin evaluation.
 */

function WhyThisDecision({ explanation }: { explanation: DecisionExplanation }) {
  return (
    <article className="panel explanation-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Why this decision? · CODE</span>
          <h3>{explanation.ruling}</h3>
        </div>
        <span className="explain-badge">Deterministic</span>
      </div>

      <p className="judge-boundary-note">
        Assembled from the verified claims, the verified rebuttals and the policy evaluation.
        No part of this panel was written by a model.
      </p>

      {explanation.decisiveFacts.length > 0 && (
        <div className="explain-block">
          <span className="eyebrow">Decisive facts</span>
          {explanation.decisiveFacts.map((fact) => (
            <div className="decisive-fact" key={fact.ruleId}>
              <div className="decisive-head">
                <code>{fact.fact}</code>
                <b>{String(fact.value)}</b>
                {fact.threshold !== null && <small>threshold {String(fact.threshold)}</small>}
                <span className={`rule-flag ${fact.passed ? "passed" : "failed"}`}>
                  {fact.passed ? "Met" : "Not met"}
                </span>
              </div>
              <p>{fact.description}</p>
            </div>
          ))}
        </div>
      )}

      {explanation.acceptedClaims.length > 0 && (
        <div className="explain-block">
          <span className="eyebrow">Verified claims</span>
          <div className="explain-claims">
            {explanation.acceptedClaims.map((claim) => (
              <div className={`explain-claim ${claim.accepted ? "accepted" : "not-accepted"}`} key={claim.claimId}>
                <code>{claim.claimId}</code>
                <p>{claim.claim}</p>
                <span className="claim-flag">
                  {claim.accepted ? "Relied on by Judge" : "Verified, not relied on"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {explanation.relevantRebuttals.length > 0 && (
        <div className="explain-block">
          <span className="eyebrow">Relevant rebuttals</span>
          <div className="explain-claims">
            {explanation.relevantRebuttals.map((rebuttal) => (
              <div className="explain-claim" key={rebuttal.rebuttalId}>
                <code>{rebuttal.rebuttalId}</code>
                <p>
                  <b>{labelize(rebuttal.stance)}</b> · {rebuttal.targetClaimId} — {rebuttal.reasoningSummary}
                </p>
                <span className="claim-flag">
                  {rebuttal.consideredByJudge ? "Considered by Judge" : "Verified, not cited by Judge"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="explain-block">
        <span className="eyebrow">Policy rules applied</span>
        <div className="rule-list">
          {explanation.policyRules.map((rule) => (
            <div className={`rule-row ${rule.passed ? "passed" : "failed"}`} key={rule.ruleId}>
              <code>{rule.ruleId}</code>
              <span>{rule.description}</span>
              <b>
                {String(rule.actualValue)} / {String(rule.requiredValue)}
              </b>
            </div>
          ))}
        </div>
      </div>

      <p className="explain-basis">{explanation.deterministicBasis}</p>

      {explanation.judgeAdvisoryDiffers && (
        <p className="review-reason">
          The Judge recommended <b>{labelize(explanation.judgeAdvisoryOutcome ?? "")}</b>, which
          differs from the deterministic action{" "}
          <b>{labelize(explanation.finalDeterministicAction)}</b>. The deterministic action stands;
          the disagreement is recorded for review.
        </p>
      )}
    </article>
  );
}

function WhatWouldChangeIt({ counterfactual }: { counterfactual: DecisionCounterfactual }) {
  return (
    <article className="panel explanation-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">What would change the decision? · CODE</span>
          <h3>Policy thresholds only</h3>
        </div>
        <span className="explain-badge">Deterministic</span>
      </div>

      <p className="judge-boundary-note">
        The Judge is never asked what would change its mind. Each threshold below is a policy
        rule's own required value, and each direction is derived from how that rule is evaluated.
      </p>

      {counterfactual.thresholds.length > 0 ? (
        <div className="counterfactual-list">
          {counterfactual.thresholds.map((item) => (
            <div className={`counterfactual-row ${item.passed ? "passed" : "failed"}`} key={item.ruleId}>
              <div className="counterfactual-head">
                <code>{item.fact}</code>
                <span className={`direction direction-${item.direction.toLowerCase()}`}>
                  {item.direction === "BELOW" ? "Would need to fall below" : "Would need to rise above"}
                </span>
                <b>{String(item.threshold)}</b>
              </div>
              <p>{item.statement}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="judge-empty">{counterfactual.statement}</p>
      )}

      <p className="explain-basis">{counterfactual.deterministicBasis}</p>
    </article>
  );
}

export interface ExplanationPanelProps {
  explanation: DecisionExplanation;
  counterfactual: DecisionCounterfactual;
}

export function ExplanationPanel({ explanation, counterfactual }: ExplanationPanelProps) {
  return (
    <section className="explain-grid stage6">
      <WhyThisDecision explanation={explanation} />
      <WhatWouldChangeIt counterfactual={counterfactual} />
    </section>
  );
}
