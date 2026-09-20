/**
 * Stage 4 advocate contracts.
 *
 * These mirror the FastAPI response for POST /api/cases/{caseId}/advocates/run
 * exactly. Rider and Driver share one schema and are discriminated only by
 * `side`; there is deliberately no `position` field.
 */

export type AdvocateSide = "RIDER" | "DRIVER";
export type ClaimImportance = "HIGH" | "MEDIUM" | "LOW";
export type AdvocateStatus = "COMPLETE" | "FAILED";
export type PipelineStatus = "COMPLETE" | "FAILED" | "NOT_RUN";

export interface AssertedFact {
  fact: string;
  value: number | boolean | string;
}

export interface AdvocateClaim {
  claimId: string;
  claim: string;
  evidenceIds: string[];
  policyRefs: string[];
  reasoningSummary: string;
  importance: ClaimImportance;
  assertedFacts: AssertedFact[];
  disputedEvidenceIds: string[];
}

/** A claim the deterministic verifier refused. Kept visible for audit. */
export interface RejectedClaim {
  claimId: string;
  claim: string;
  reason: string;
  detail: string;
  evidenceIds: string[];
  policyRefs: string[];
}

export interface ClaimWarning {
  claimId: string;
  code: string;
  detail: string;
}

export interface AdvocateSideResult {
  side: AdvocateSide;
  status: AdvocateStatus;
  summary: string;
  requestedOutcome: string | null;
  contextAcknowledged: boolean;
  verifiedClaims: AdvocateClaim[];
  rejectedClaims: RejectedClaim[];
  warnings: ClaimWarning[];
  failureReason: string | null;
}

export interface AgentRunMetadata {
  mode: "mock" | "real";
  provider: string;
  model: string | null;
  promptVersion: string;
  durationMs: number;
}

export interface VerificationSummary {
  verifiedCount: number;
  rejectedCount: number;
  rejectionReasons: string[];
}

export interface PipelineStage {
  stage: string;
  status: PipelineStatus;
}

export interface AdvocateRunResult {
  caseId: string;
  disputeType: "route_deviation" | "no_show_charge";
  rider: AdvocateSideResult;
  driver: AdvocateSideResult;
  agentRun: AgentRunMetadata;
  verificationSummary: VerificationSummary;
  pipeline: PipelineStage[];
}
