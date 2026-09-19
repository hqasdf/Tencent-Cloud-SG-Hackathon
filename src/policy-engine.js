import { round } from "./route-metrics.js";

export class RouteDeviationPolicyEngine {
  constructor(policy) {
    this.policy = policy;
    this.policyRefs = new Set(policy.rules.map((rule) => rule.id));
  }

  evaluate({ replay, evidenceCatalog }) {
    const presentEvidenceTypes = new Set(
      [...evidenceCatalog.values()].filter((item) => item.valid !== false).map((item) => item.type)
    );
    const missingEvidenceTypes = this.policy.required_evidence_types.filter((type) => !presentEvidenceTypes.has(type));
    const justificationEvidence = [...evidenceCatalog.values()].filter((item) => {
      return item.valid !== false && this.policy.justification_evidence_types.includes(item.type);
    });
    const distanceExceeded = replay.metrics.distance_deviation_pct_exact >= this.policy.thresholds.distance_deviation_pct;
    const durationExceeded = replay.metrics.duration_deviation_pct_exact >= this.policy.thresholds.duration_deviation_pct;
    const significantDeviation = distanceExceeded || durationExceeded;
    const justifiedDeviation = justificationEvidence.length > 0;
    const policyClear = missingEvidenceTypes.length === 0;
    const refundAmount = policyClear && significantDeviation && !justifiedDeviation
      ? round(Math.min(replay.metrics.fare_difference, this.policy.thresholds.maximum_refund), 2)
      : 0;

    let outcome = "NO_SIGNIFICANT_DEVIATION";
    let recommendedAction = "UPHOLD_FARE";
    if (!policyClear) {
      outcome = "INSUFFICIENT_EVIDENCE";
      recommendedAction = "HUMAN_REVIEW";
    } else if (significantDeviation && justifiedDeviation) {
      outcome = "JUSTIFIED_DEVIATION";
    } else if (significantDeviation) {
      outcome = "UNJUSTIFIED_DEVIATION";
      recommendedAction = refundAmount > 0 ? "PARTIAL_REFUND" : "NO_MONETARY_ADJUSTMENT";
    }

    return {
      policy_id: this.policy.id,
      policy_version: this.policy.version,
      policy_refs: significantDeviation
        ? ["ROUTE_POLICY_01", "ROUTE_POLICY_02", "ROUTE_POLICY_03"]
        : ["ROUTE_POLICY_01"],
      thresholds: { ...this.policy.thresholds },
      checks: {
        distance_threshold_exceeded: distanceExceeded,
        duration_threshold_exceeded: durationExceeded,
        significant_deviation: significantDeviation,
        justified_deviation: justifiedDeviation,
        policy_clear: policyClear
      },
      justification_evidence_ids: justificationEvidence.map((item) => item.id),
      missing_evidence_types: missingEvidenceTypes,
      outcome,
      recommended_action: recommendedAction,
      refund_amount: refundAmount,
      currency: replay.metrics.currency
    };
  }
}
