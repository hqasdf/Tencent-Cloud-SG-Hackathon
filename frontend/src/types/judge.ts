/**
 * Stage 5 Judge contracts.
 *
 * These mirror the FastAPI response for POST /api/cases/{caseId}/resolution/run.
 *
 * The Judge block and the deterministic block are separate types on purpose.
 * `JudgeResult` is advisory — a recommendation with its justification.
 * `DeterministicResolution` is what code is actually willing to do. Merging them
 * would make a suggestion look like an authorisation.
 */

export type JudgeStatus = "NOT_RUN" | "COMPLETE" | "FAILED" | "PENDING_HUMAN_REVIEW";
export type JudgeSkipReason = "ADVOCATE_INPUT_INCOMPLETE";

/** Outcomes the Judge may recommend. Deliberately excludes HUMAN_REVIEW. */
export type JudgeOutcome =
  | "NO_REFUND"
  | "PARTIAL_REFUND"
  | "FULL_FARE_DIFFERENCE_REFUND"
  | "UPHOLD_CANCELLATION_CHARGE"
  | "REFUND_CANCELLATION_CHARGE";

export interface JudgeValidationIssue {
  code: string;
  detail: string;
}

export interface JudgeResult {
  status: JudgeStatus;
  skipReason: JudgeSkipReason | null;
  recommendedOutcome: string | null;
  /** True only when code has already decided this case may be automated. */
  executable: boolean;
  acceptedRiderClaimIds: string[];
  acceptedDriverClaimIds: string[];
  rejectedRiderClaimIds: string[];
  rejectedDriverClaimIds: string[];
  reasoningSummary: string;
  evidenceIds: string[];
  policyRuleIds: string[];
  uncertainties: string[];
  requiresHumanReview: boolean;
  failureReason: string | null;
  validationIssues: JudgeValidationIssue[];
  execution: {
    provider: string;
    model: string | null;
    durationMs: number;
    inputTokens: number | null;
    outputTokens: number | null;
    totalTokens: number | null;
  } | null;
}

/**
 * The authoritative half of the result.
 *
 * Every field is computed by the deterministic engines and is unaffected by the
 * Judge. Note there is no Judge-authored field here: the Judge cannot move
 * `refundAmount`, `confidence`, `resolutionMode` or `escalationReasons`.
 */
export interface DeterministicResolution {
  ruling: string;
  recommendedAction: string;
  refundAmount: number;
  currency: string;
  resolutionMode: "AUTO_RESOLVE" | "HUMAN_REVIEW";
  confidence: number;
  escalationReasons: string[];
  explanation: string;
  counterfactualExplanation: string;
}

export interface AuditEvent {
  event: string;
  timestamp: string;
  metadata: Record<string, unknown>;
}
