from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.analysis import AnalysisInput


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda value: value)


DisputeType = Literal["route_deviation", "no_show_charge"]
CaseStatus = Literal["Pending", "Investigating", "Auto Resolved", "Human Review"]
EvidenceStatus = Literal["Verified", "Pending", "Conflicting", "Missing"]
ClaimStatus = Literal["Verified", "Rejected", "Pending"]
ResolutionMode = Literal["AUTO_RESOLVE", "HUMAN_REVIEW"]
ActivityState = Literal["complete", "active", "queued"]
Severity = Literal["normal", "attention", "conflict"]


class Person(ApiModel):
    name: str
    id: str


class TripInfo(ApiModel):
    trip_id: str = Field(serialization_alias="tripId", validation_alias="tripId")
    pickup: str
    destination: str
    booked_at: str = Field(serialization_alias="bookedAt", validation_alias="bookedAt")
    distance: str
    duration: str


class FareInfo(ApiModel):
    currency: str
    quoted: float
    actual: float
    difference: float


class CaseMetadata(ApiModel):
    submitted_at: str = Field(serialization_alias="submittedAt", validation_alias="submittedAt")
    last_updated: str = Field(serialization_alias="lastUpdated", validation_alias="lastUpdated")
    priority: Literal["Standard", "High"]
    policy_version: str = Field(serialization_alias="policyVersion", validation_alias="policyVersion")


class TimelineEvent(ApiModel):
    id: str
    timestamp: str
    type: str
    description: str
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")
    severity: Severity | None = None


class Evidence(ApiModel):
    id: str
    type: Literal["GPS", "Chat", "Fare", "Trip event", "Policy", "Traffic", "System"]
    timestamp: str
    source: str
    summary: str
    status: EvidenceStatus


class AdvocateClaim(ApiModel):
    id: str
    claim: str
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")
    policy_refs: list[str] = Field(serialization_alias="policyRefs", validation_alias="policyRefs")
    status: ClaimStatus
    reason: str | None = None


class AdvocateCase(ApiModel):
    party: Literal["Rider", "Driver"]
    summary: str
    claims: list[AdvocateClaim]


class PolicyResult(ApiModel):
    policy_id: str = Field(serialization_alias="policyId", validation_alias="policyId")
    name: str
    rule_summary: str = Field(serialization_alias="ruleSummary", validation_alias="ruleSummary")
    outcome: str


class ConfidenceBreakdown(ApiModel):
    evidence_completeness: int = Field(serialization_alias="evidenceCompleteness", validation_alias="evidenceCompleteness", ge=0, le=100)
    contradictory_evidence: int = Field(serialization_alias="contradictoryEvidence", validation_alias="contradictoryEvidence", ge=0, le=100)
    policy_clarity: int = Field(serialization_alias="policyClarity", validation_alias="policyClarity", ge=0, le=100)
    missing_information: int = Field(serialization_alias="missingInformation", validation_alias="missingInformation", ge=0, le=100)
    advocate_disagreement: int = Field(serialization_alias="advocateDisagreement", validation_alias="advocateDisagreement", ge=0, le=100)
    overall: int = Field(ge=0, le=100)


class Resolution(ApiModel):
    ruling: str
    recommended_action: str = Field(serialization_alias="recommendedAction", validation_alias="recommendedAction")
    refund_amount: float = Field(serialization_alias="refundAmount", validation_alias="refundAmount", ge=0)
    currency: str
    accepted_claim_ids: list[str] = Field(serialization_alias="acceptedClaimIds", validation_alias="acceptedClaimIds")
    rejected_claim_ids: list[str] = Field(serialization_alias="rejectedClaimIds", validation_alias="rejectedClaimIds")
    mode: ResolutionMode
    explanation: str
    counterfactual_explanation: str = Field(serialization_alias="counterfactualExplanation", validation_alias="counterfactualExplanation")
    escalation_reason: str | None = Field(default=None, serialization_alias="escalationReason", validation_alias="escalationReason")


class AgentActivity(ApiModel):
    id: str
    label: str
    state: ActivityState
    detail: str


class DisputeCase(ApiModel):
    id: str
    dispute_type: DisputeType = Field(serialization_alias="disputeType", validation_alias="disputeType")
    title: str
    status: CaseStatus
    description: str
    rider: Person
    driver: Person
    trip: TripInfo
    fare: FareInfo
    rider_complaint: str = Field(serialization_alias="riderComplaint", validation_alias="riderComplaint")
    driver_response: str = Field(serialization_alias="driverResponse", validation_alias="driverResponse")
    metadata: CaseMetadata
    timeline: list[TimelineEvent]
    evidence: list[Evidence]
    rider_case: AdvocateCase = Field(serialization_alias="riderCase", validation_alias="riderCase")
    driver_case: AdvocateCase = Field(serialization_alias="driverCase", validation_alias="driverCase")
    activity: list[AgentActivity]
    policy_result: PolicyResult = Field(serialization_alias="policyResult", validation_alias="policyResult")
    confidence: ConfidenceBreakdown
    resolution: Resolution
    human_review_summary: str | None = Field(default=None, serialization_alias="humanReviewSummary", validation_alias="humanReviewSummary")
    analysis_input: AnalysisInput = Field(exclude=True)


class CaseSummary(ApiModel):
    id: str
    dispute_type: DisputeType = Field(serialization_alias="disputeType")
    title: str
    status: CaseStatus
    rider: Person
    driver: Person
    confidence: ConfidenceBreakdown
    resolution: Resolution
