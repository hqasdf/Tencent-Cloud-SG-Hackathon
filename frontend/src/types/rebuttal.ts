/**
 * Stage 6 cross-examination contracts.
 *
 * These mirror the `rebuttals` block of the FastAPI response for
 * POST /api/cases/{caseId}/resolution/run.
 *
 * The distinction that matters most here is `verifiedRebuttals` versus
 * `rejectedRebuttals`. Both are returned, because a rejected rebuttal is exactly
 * what a reviewer needs to see — but only the verified ones were ever given to
 * the Judge, and the UI must not blur that.
 */

export type RebuttalStance = "CHALLENGE" | "CONCEDE" | "PARTIALLY_CONCEDE";

export type RebuttalStatus = "COMPLETE" | "FAILED" | "NOT_RUN";

/** One side's verified response to one opposing claim. */
export interface VerifiedRebuttal {
  /** Assigned by code, namespaced by side: RIDER-RB1 / DRIVER-RB1. */
  rebuttalId: string;
  side: "RIDER" | "DRIVER";
  targetClaimId: string;
  targetClaimSide: "RIDER" | "DRIVER";
  stance: RebuttalStance;
  reasoningSummary: string;
  evidenceIds: string[];
  policyRuleIds: string[];
  assertedFacts: { fact: string; value: number | boolean | string }[];
  verificationStatus: "VERIFIED";
}

/**
 * A rebuttal that failed deterministic verification.
 *
 * Never repaired and never used by the Judge. It is surfaced so a human can see
 * what the model tried and why code refused it.
 */
export interface RejectedRebuttal {
  targetClaimId: string | null;
  stance: string | null;
  reason: string;
  detail: string;
  evidenceIds: string[];
  policyRuleIds: string[];
}

export interface RebuttalSideResult {
  side: "RIDER" | "DRIVER";
  status: RebuttalStatus;
  overallSummary: string;
  verifiedRebuttals: VerifiedRebuttal[];
  rejectedRebuttals: RejectedRebuttal[];
  /** Derived by code from the verified stances, not from the model's own list. */
  concededTargetIds: string[];
  failureReason: string | null;
  execution: {
    provider: string;
    model: string | null;
    durationMs: number;
    inputTokens: number | null;
    outputTokens: number | null;
    totalTokens: number | null;
    generatedClaimCount: number;
    verifiedClaimCount: number;
    rejectedClaimCount: number;
    rejectionReasons: string[];
  } | null;
}

export interface RebuttalRunResult {
  caseId: string;
  disputeType: "route_deviation" | "no_show_charge";
  /** Always 1. Rendered so a reviewer can see no further round exists. */
  round: number;
  maxRounds: number;
  rider: RebuttalSideResult;
  driver: RebuttalSideResult;
  verificationSummary: {
    verifiedCount: number;
    rejectedCount: number;
    rejectionReasons: string[];
    generatedCount: number;
  };
  pipeline: { stage: string; status: "COMPLETE" | "FAILED" | "NOT_RUN" }[];
}

/** Deterministic "why this decision?" — assembled by code, not by a model. */
export interface DecisionExplanation {
  acceptedClaims: {
    claimId: string;
    side: "RIDER" | "DRIVER";
    claim: string;
    accepted: boolean;
  }[];
  relevantRebuttals: {
    rebuttalId: string;
    side: "RIDER" | "DRIVER";
    targetClaimId: string;
    stance: string;
    reasoningSummary: string;
    consideredByJudge: boolean;
  }[];
  decisiveFacts: {
    fact: string;
    value: number | boolean | string;
    ruleId: string;
    threshold: number | boolean | string | null;
    passed: boolean;
    description: string;
  }[];
  policyRules: {
    ruleId: string;
    description: string;
    passed: boolean;
    actualValue: number | boolean | string;
    requiredValue: number | boolean | string;
    evidenceIds: string[];
  }[];
  finalDeterministicAction: string;
  ruling: string;
  deterministicBasis: string;
  judgeAdvisoryOutcome: string | null;
  judgeAdvisoryDiffers: boolean;
}

/** Deterministic "what would have changed it?" — thresholds only, never a story. */
export interface DecisionCounterfactual {
  thresholds: {
    ruleId: string;
    fact: string;
    actualValue: number | boolean | string;
    threshold: number | boolean | string;
    direction: "BELOW" | "ABOVE";
    passed: boolean;
    statement: string;
  }[];
  statement: string;
  deterministicBasis: string;
  generatedBy: "DETERMINISTIC_ENGINE";
}
