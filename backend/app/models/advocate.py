from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import AdvocateSide, AgentRunMetadata, ClaimImportance


class AdvocateModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class AssertedFact(AdvocateModel):
    """A structured, machine-checkable factual assertion made by an agent.

    This is the HARD verification layer. Each asserted fact is compared against
    the already-computed trusted value. Agents never recalculate anything.
    """

    fact: str
    value: float | int | bool | str


class AdvocateClaim(AdvocateModel):
    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    claim: str
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")
    policy_refs: list[str] = Field(serialization_alias="policyRefs", validation_alias="policyRefs")
    reasoning_summary: str = Field(serialization_alias="reasoningSummary", validation_alias="reasoningSummary")
    importance: ClaimImportance
    asserted_facts: list[AssertedFact] = Field(default_factory=list, serialization_alias="assertedFacts", validation_alias="assertedFacts")
    disputed_evidence_ids: list[str] = Field(default_factory=list, serialization_alias="disputedEvidenceIds", validation_alias="disputedEvidenceIds")


class AdvocateOutput(AdvocateModel):
    """Shared output schema. Rider and Driver use the EXACT same schema.

    There is deliberately no `position` field: the side already expresses who is
    advocating, so a separate supports/opposes axis would be redundant.
    """

    side: AdvocateSide
    summary: str
    claims: list[AdvocateClaim]
    requested_outcome: str = Field(serialization_alias="requestedOutcome", validation_alias="requestedOutcome")
    context_acknowledged: bool = Field(serialization_alias="contextAcknowledged", validation_alias="contextAcknowledged")


class ClaimRejection(AdvocateModel):
    """A rejected claim, kept visible for audit and debugging.

    Rejected claims are NEVER repaired, rewritten, or dropped. A hallucinated
    evidence ID such as E99 stays rejected exactly as produced.
    """

    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    claim: str
    reason: str
    detail: str
    evidence_ids: list[str] = Field(default_factory=list, serialization_alias="evidenceIds", validation_alias="evidenceIds")
    policy_refs: list[str] = Field(default_factory=list, serialization_alias="policyRefs", validation_alias="policyRefs")


class ClaimWarning(AdvocateModel):
    """A non-blocking observation. Prose contradictions are warnings only."""

    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    code: str
    detail: str


class AdvocateSideResult(AdvocateModel):
    side: AdvocateSide
    status: Literal["COMPLETE", "FAILED"]
    summary: str
    requested_outcome: str | None = Field(default=None, serialization_alias="requestedOutcome", validation_alias="requestedOutcome")
    context_acknowledged: bool = Field(default=False, serialization_alias="contextAcknowledged", validation_alias="contextAcknowledged")
    verified_claims: list[AdvocateClaim] = Field(default_factory=list, serialization_alias="verifiedClaims", validation_alias="verifiedClaims")
    rejected_claims: list[ClaimRejection] = Field(default_factory=list, serialization_alias="rejectedClaims", validation_alias="rejectedClaims")
    warnings: list[ClaimWarning] = Field(default_factory=list)
    failure_reason: str | None = Field(default=None, serialization_alias="failureReason", validation_alias="failureReason")


class VerificationSummary(AdvocateModel):
    verified_count: int = Field(serialization_alias="verifiedCount", validation_alias="verifiedCount")
    rejected_count: int = Field(serialization_alias="rejectedCount", validation_alias="rejectedCount")
    rejection_reasons: list[str] = Field(default_factory=list, serialization_alias="rejectionReasons", validation_alias="rejectionReasons")


class PipelineStage(AdvocateModel):
    stage: str
    status: Literal["COMPLETE", "FAILED", "NOT_RUN"]


class AdvocateRunResponse(AdvocateModel):
    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(serialization_alias="disputeType", validation_alias="disputeType")
    rider: AdvocateSideResult
    driver: AdvocateSideResult
    agent_run: AgentRunMetadata = Field(serialization_alias="agentRun", validation_alias="agentRun")
    verification_summary: VerificationSummary = Field(serialization_alias="verificationSummary", validation_alias="verificationSummary")
    pipeline: list[PipelineStage]
