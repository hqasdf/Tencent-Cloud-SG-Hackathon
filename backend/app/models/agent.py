from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentModel(BaseModel):
    """Base model for agent-facing contracts.

    Agent contracts reuse the camelCase aliases used by the rest of the API so
    the whole surface keeps one naming convention. Both aliases are declared so
    a context serialized to camelCase can be validated back losslessly, which is
    what the mock provider does.
    """

    model_config = ConfigDict(populate_by_name=True)


AdvocateSide = Literal["RIDER", "DRIVER"]
ClaimImportance = Literal["HIGH", "MEDIUM", "LOW"]
AdvocateStatus = Literal["COMPLETE", "FAILED"]
AgentMode = Literal["mock", "real"]


class ContextTimelineEvent(AgentModel):
    id: str
    timestamp: str
    type: str
    description: str
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")
    severity: str | None = None


class ContextEvidence(AgentModel):
    id: str
    type: str
    timestamp: str
    source: str
    summary: str
    status: str


class ContextPolicyRule(AgentModel):
    rule_id: str = Field(serialization_alias="ruleId", validation_alias="ruleId")
    description: str


class ContextPolicy(AgentModel):
    """The policy rules an advocate is allowed to cite."""

    policy_id: str = Field(serialization_alias="policyId", validation_alias="policyId")
    policy_version: str = Field(serialization_alias="policyVersion", validation_alias="policyVersion")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(serialization_alias="disputeType", validation_alias="disputeType")
    rules: list[ContextPolicyRule]


class ContextRuleEvaluation(AgentModel):
    rule_id: str = Field(serialization_alias="ruleId", validation_alias="ruleId")
    description: str
    passed: bool
    actual_value: str | float | int | bool = Field(serialization_alias="actualValue", validation_alias="actualValue")
    required_value: str | float | int | bool = Field(serialization_alias="requiredValue", validation_alias="requiredValue")
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")


class ContextPolicyEvaluation(AgentModel):
    """PolicyTwin output. Describes which rules passed, never the recommendation."""

    policy_id: str = Field(serialization_alias="policyId", validation_alias="policyId")
    policy_version: str = Field(serialization_alias="policyVersion", validation_alias="policyVersion")
    evaluated_rules: list[ContextRuleEvaluation] = Field(serialization_alias="evaluatedRules", validation_alias="evaluatedRules")
    passed_rules: list[str] = Field(serialization_alias="passedRules", validation_alias="passedRules")
    failed_rules: list[str] = Field(serialization_alias="failedRules", validation_alias="failedRules")
    supporting_evidence_ids: list[str] = Field(serialization_alias="supportingEvidenceIds", validation_alias="supportingEvidenceIds")
    overall_outcome: str = Field(serialization_alias="overallOutcome", validation_alias="overallOutcome")


class ContextRouteCondition(AgentModel):
    type: str
    explained_distance_km: float = Field(serialization_alias="explainedDistanceKm", validation_alias="explainedDistanceKm")
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")


class RouteDeviationFacts(AgentModel):
    """Trusted, already-calculated route deviation facts. Read-only for agents."""

    analysis_type: Literal["route_deviation"] = Field(default="route_deviation", serialization_alias="analysisType", validation_alias="analysisType")
    expected_route_distance_km: float = Field(serialization_alias="expectedRouteDistanceKm", validation_alias="expectedRouteDistanceKm")
    actual_route_distance_km: float = Field(serialization_alias="actualRouteDistanceKm", validation_alias="actualRouteDistanceKm")
    distance_difference_km: float = Field(serialization_alias="distanceDifferenceKm", validation_alias="distanceDifferenceKm")
    deviation_percentage: float = Field(serialization_alias="deviationPercentage", validation_alias="deviationPercentage")
    expected_trip_duration_minutes: float = Field(serialization_alias="expectedTripDurationMinutes", validation_alias="expectedTripDurationMinutes")
    actual_trip_duration_minutes: float = Field(serialization_alias="actualTripDurationMinutes", validation_alias="actualTripDurationMinutes")
    duration_difference_minutes: float = Field(serialization_alias="durationDifferenceMinutes", validation_alias="durationDifferenceMinutes")
    expected_fare: float = Field(serialization_alias="expectedFare", validation_alias="expectedFare")
    actual_fare: float = Field(serialization_alias="actualFare", validation_alias="actualFare")
    fare_difference: float = Field(serialization_alias="fareDifference", validation_alias="fareDifference")
    explained_deviation_distance_km: float = Field(serialization_alias="explainedDeviationDistanceKm", validation_alias="explainedDeviationDistanceKm")
    unexplained_deviation_distance_km: float = Field(serialization_alias="unexplainedDeviationDistanceKm", validation_alias="unexplainedDeviationDistanceKm")
    verified_conditions: list[ContextRouteCondition] = Field(serialization_alias="verifiedConditions", validation_alias="verifiedConditions")
    supporting_evidence_ids: list[str] = Field(serialization_alias="supportingEvidenceIds", validation_alias="supportingEvidenceIds")


class NoShowFacts(AgentModel):
    """Trusted, already-calculated no-show facts. Read-only for agents."""

    analysis_type: Literal["no_show_charge"] = Field(default="no_show_charge", serialization_alias="analysisType", validation_alias="analysisType")
    driver_distance_to_pickup_meters: float = Field(serialization_alias="driverDistanceToPickupMeters", validation_alias="driverDistanceToPickupMeters")
    driver_within_pickup_radius: bool = Field(serialization_alias="driverWithinPickupRadius", validation_alias="driverWithinPickupRadius")
    driver_arrival_timestamp: str = Field(serialization_alias="driverArrivalTimestamp", validation_alias="driverArrivalTimestamp")
    cancellation_timestamp: str = Field(serialization_alias="cancellationTimestamp", validation_alias="cancellationTimestamp")
    waiting_duration_seconds: int = Field(serialization_alias="waitingDurationSeconds", validation_alias="waitingDurationSeconds")
    cancellation_charge_amount: float = Field(serialization_alias="cancellationChargeAmount", validation_alias="cancellationChargeAmount")
    rider_message_evidence_ids: list[str] = Field(serialization_alias="riderMessageEvidenceIds", validation_alias="riderMessageEvidenceIds")
    driver_message_evidence_ids: list[str] = Field(serialization_alias="driverMessageEvidenceIds", validation_alias="driverMessageEvidenceIds")
    supporting_evidence_ids: list[str] = Field(serialization_alias="supportingEvidenceIds", validation_alias="supportingEvidenceIds")


class AgentCaseContext(AgentModel):
    """The ONLY information an advocate agent may ever see.

    Produced by an explicit allow-list projection. It never contains the
    deterministic recommendation, refund, confidence, escalation state, raw
    calculation inputs, other cases, or any historical signal.

    The advocates receive: facts + evidence + policy.
    They do not receive: the answer.
    """

    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(serialization_alias="disputeType", validation_alias="disputeType")
    rider_complaint: str = Field(serialization_alias="riderComplaint", validation_alias="riderComplaint")
    driver_response: str = Field(serialization_alias="driverResponse", validation_alias="driverResponse")
    currency: str
    timeline: list[ContextTimelineEvent]
    evidence: list[ContextEvidence]
    facts: RouteDeviationFacts | NoShowFacts
    policy: ContextPolicy
    policy_evaluation: ContextPolicyEvaluation = Field(serialization_alias="policyEvaluation", validation_alias="policyEvaluation")
    missing_evidence_ids: list[str] = Field(serialization_alias="missingEvidenceIds", validation_alias="missingEvidenceIds")
    conflicting_evidence_ids: list[str] = Field(serialization_alias="conflictingEvidenceIds", validation_alias="conflictingEvidenceIds")


class AgentRunMetadata(AgentModel):
    """Execution metadata for a single advocate run. Never contains secrets."""

    mode: AgentMode
    provider: str
    model: str | None = None
    prompt_version: str = Field(serialization_alias="promptVersion", validation_alias="promptVersion")
    duration_ms: int = Field(serialization_alias="durationMs", validation_alias="durationMs")
