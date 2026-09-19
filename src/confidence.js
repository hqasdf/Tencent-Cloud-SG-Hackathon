import { round } from "./route-metrics.js";

export function evaluateConfidence({ policy, policyResult, verifiedClaims, rejectedClaims, contradictions }) {
  const requiredCount = policy.required_evidence_types.length;
  const completeness = requiredCount === 0
    ? 1
    : (requiredCount - policyResult.missing_evidence_types.length) / requiredCount;
  const totalClaims = verifiedClaims.length + rejectedClaims.length;
  const claimSupportRatio = totalClaims === 0 ? 0 : verifiedClaims.length / totalClaims;
  const contradictionScore = contradictions.length === 0 ? 1 : Math.max(0, 1 - contradictions.length * 0.5);
  const policyClarity = policyResult.checks.policy_clear ? 1 : 0;
  const missingDataScore = policyResult.missing_evidence_types.length === 0 ? 1 : 0;
  const riderPositions = new Set(verifiedClaims.filter((claim) => claim.actor === "rider").map((claim) => claim.position));
  const driverPositions = new Set(verifiedClaims.filter((claim) => claim.actor === "driver").map((claim) => claim.position));
  const advocateDisagreement = riderPositions.has("supports") && driverPositions.has("opposes");

  let score = (0.3 * completeness)
    + (0.25 * policyClarity)
    + (0.2 * claimSupportRatio)
    + (0.15 * contradictionScore)
    + (0.1 * missingDataScore);
  if (advocateDisagreement) score -= 0.05;
  score = round(Math.max(0, Math.min(1, score)), 2);

  const escalationReasons = [];
  if (policyResult.missing_evidence_types.length > 0) escalationReasons.push("MISSING_REQUIRED_EVIDENCE");
  if (contradictions.length > 0) escalationReasons.push("CONTRADICTORY_EVIDENCE");
  if (rejectedClaims.length > 0) escalationReasons.push("UNSUPPORTED_OR_INVALID_CLAIMS");
  if (verifiedClaims.length === 0) escalationReasons.push("NO_VERIFIED_CLAIMS");
  if (!policyResult.checks.policy_clear) escalationReasons.push("POLICY_NOT_DETERMINATE");
  if (verifiedClaims.length === 0) score = Math.min(score, policy.thresholds.auto_resolve_confidence - 0.01);
  score = round(Math.max(0, score), 2);
  if (score < policy.thresholds.auto_resolve_confidence) escalationReasons.push("LOW_CONFIDENCE");

  return {
    score,
    inputs: {
      evidence_completeness: round(completeness, 2),
      policy_clarity: policyClarity,
      missing_data_score: missingDataScore,
      claim_support_ratio: round(claimSupportRatio, 2),
      contradiction_score: round(contradictionScore, 2),
      advocate_disagreement: advocateDisagreement
    },
    escalation_reasons: [...new Set(escalationReasons)],
    resolution_mode: escalationReasons.length === 0 ? "AUTO_RESOLVE" : "HUMAN_REVIEW"
  };
}
