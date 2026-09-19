import { findEvidenceByType } from "./evidence.js";

class AdvocateAgent {
  constructor(actor) {
    this.actor = actor;
  }

  claim(id, position, text, evidenceIds, policyRefs) {
    return {
      id,
      actor: this.actor,
      position,
      claim: text,
      evidence_ids: evidenceIds,
      policy_refs: policyRefs
    };
  }

  caseResult(summary, claims) {
    return {
      actor: this.actor,
      output_schema: "advocate_case_v1",
      summary,
      claims
    };
  }
}

export class RiderAdvocateAgent extends AdvocateAgent {
  constructor() {
    super("rider");
  }

  async argue({ replay, evidenceCatalog }) {
    const actualRoute = findEvidenceByType(evidenceCatalog, "actual_route");
    const baselineRoute = findEvidenceByType(evidenceCatalog, "baseline_route");
    const fare = findEvidenceByType(evidenceCatalog, "fare");
    const claims = [];

    if (actualRoute && baselineRoute) {
      claims.push(this.claim(
        "RIDER_ROUTE_DEVIATION",
        "supports",
        `The actual route exceeded the expected route by ${replay.metrics.distance_deviation_pct}%.`,
        [actualRoute.id, baselineRoute.id],
        ["ROUTE_POLICY_01"]
      ));
    }
    if (fare && replay.metrics.fare_difference > 0) {
      claims.push(this.claim(
        "RIDER_FARE_IMPACT",
        "supports",
        `The actual fare exceeded the expected fare by ${replay.metrics.currency} ${replay.metrics.fare_difference.toFixed(2)}.`,
        [fare.id],
        ["ROUTE_POLICY_03"]
      ));
    }

    return this.caseResult(
      claims.length > 0 ? "The rider case is based on measured route and fare differences." : "The rider case lacks required route evidence.",
      claims
    );
  }
}

export class DriverAdvocateAgent extends AdvocateAgent {
  constructor() {
    super("driver");
  }

  async argue({ evidenceCatalog, policy }) {
    const actualRoute = findEvidenceByType(evidenceCatalog, "actual_route");
    const baselineRoute = findEvidenceByType(evidenceCatalog, "baseline_route");
    const justification = [...evidenceCatalog.values()].find((item) => {
      return policy.justification_evidence_types.includes(item.type) && item.valid !== false;
    });
    const claims = [];

    if (justification) {
      claims.push(this.claim(
        "DRIVER_JUSTIFICATION",
        "opposes",
        `The route deviation has recorded ${justification.type.replaceAll("_", " ")} justification.`,
        [justification.id, ...(actualRoute ? [actualRoute.id] : [])],
        ["ROUTE_POLICY_02"]
      ));
    } else if (actualRoute && baselineRoute) {
      claims.push(this.claim(
        "DRIVER_NO_INTENT_INFERENCE",
        "neutral",
        "The route records establish a deviation but do not by themselves establish driver intent or a valid justification.",
        [actualRoute.id, baselineRoute.id],
        ["ROUTE_POLICY_02"]
      ));
    }

    return this.caseResult(
      justification ? "The driver case relies on recorded justification evidence." : "No verified justification evidence is available for the driver case.",
      claims
    );
  }
}
