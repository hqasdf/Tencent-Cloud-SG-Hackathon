/**
 * ⚠️ LEGACY — the Judge is deliberately NOT implemented in the active backend.
 * The Stage 4 pipeline reports JUDGE as NOT_RUN. This file is frozen for reference.
 */
export class JudgeAgent {
  decide({ verifiedClaims, policyResult, replay }) {
    if (!verifiedClaims.every((claim) => claim.verified === true)) {
      throw new Error("Judge received an unverified claim");
    }

    const evidenceIds = [...new Set(verifiedClaims.flatMap((claim) => claim.evidence_ids))];
    let ruling;
    let explanation;
    let counterfactual;

    switch (policyResult.outcome) {
      case "INSUFFICIENT_EVIDENCE":
        ruling = "INSUFFICIENT_EVIDENCE";
        explanation = `Required evidence is missing: ${policyResult.missing_evidence_types.join(", ")}.`;
        counterfactual = "Providing all required route and fare evidence could allow an automatic ruling.";
        break;
      case "JUSTIFIED_DEVIATION":
        ruling = "DRIVER_FAVORED";
        explanation = `The route deviation met the policy threshold, but valid justification evidence (${policyResult.justification_evidence_ids.join(", ")}) supports the deviation.`;
        counterfactual = "Without valid justification evidence, the same measured deviation would qualify for the configured fare adjustment.";
        break;
      case "UNJUSTIFIED_DEVIATION":
        ruling = "RIDER_FAVORED";
        explanation = `The route exceeded policy thresholds (${replay.metrics.distance_deviation_pct}% distance; ${replay.metrics.duration_deviation_pct}% duration) and no valid justification evidence was found.`;
        counterfactual = "Valid evidence of traffic, road closure, a rider request, or safety conditions would change the deviation to justified.";
        break;
      default:
        ruling = "NO_ROUTE_DEVIATION";
        explanation = "The measured route and duration differences did not reach the configured policy thresholds.";
        counterfactual = `A distance deviation of at least ${policyResult.thresholds.distance_deviation_pct}% or duration deviation of at least ${policyResult.thresholds.duration_deviation_pct}% would trigger route-deviation review.`;
    }

    return {
      agent: "judge",
      input_claim_ids: verifiedClaims.map((claim) => claim.id),
      evidence_ids: evidenceIds,
      policy_refs: policyResult.policy_refs,
      ruling,
      recommended_action: policyResult.recommended_action,
      refund_amount: policyResult.refund_amount,
      explanation,
      counterfactual_explanation: counterfactual
    };
  }
}
