from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RouteConditionInput(AnalysisModel):
    type: Literal["road_closure", "traffic_diversion", "rider_requested_stop", "pickup_adjustment", "dropoff_adjustment", "emergency_diversion"]
    explained_distance_km: float = Field(ge=0, serialization_alias="explainedDistanceKm", validation_alias="explainedDistanceKm")
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")


class RouteDeviationInput(AnalysisModel):
    expected_route_distance_km: float = Field(gt=0, serialization_alias="expectedRouteDistanceKm", validation_alias="expectedRouteDistanceKm")
    actual_route_distance_km: float = Field(gt=0, serialization_alias="actualRouteDistanceKm", validation_alias="actualRouteDistanceKm")
    expected_duration_minutes: float = Field(gt=0, serialization_alias="expectedDurationMinutes", validation_alias="expectedDurationMinutes")
    actual_duration_minutes: float = Field(gt=0, serialization_alias="actualDurationMinutes", validation_alias="actualDurationMinutes")
    conditions: list[RouteConditionInput] = Field(default_factory=list)


class GeoPoint(AnalysisModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class NoShowInput(AnalysisModel):
    pickup_coordinates: GeoPoint = Field(serialization_alias="pickupCoordinates", validation_alias="pickupCoordinates")
    driver_arrival_coordinates: GeoPoint = Field(serialization_alias="driverArrivalCoordinates", validation_alias="driverArrivalCoordinates")
    driver_arrival_timestamp: str = Field(serialization_alias="driverArrivalTimestamp", validation_alias="driverArrivalTimestamp")
    cancellation_timestamp: str = Field(serialization_alias="cancellationTimestamp", validation_alias="cancellationTimestamp")
    rider_message_evidence_ids: list[str] = Field(default_factory=list, serialization_alias="riderMessageEvidenceIds", validation_alias="riderMessageEvidenceIds")
    driver_message_evidence_ids: list[str] = Field(default_factory=list, serialization_alias="driverMessageEvidenceIds", validation_alias="driverMessageEvidenceIds")
    cancellation_charge_amount: float = Field(ge=0, serialization_alias="cancellationChargeAmount", validation_alias="cancellationChargeAmount")


class AnalysisInput(AnalysisModel):
    route_deviation: RouteDeviationInput | None = Field(default=None, serialization_alias="routeDeviation", validation_alias="routeDeviation")
    no_show: NoShowInput | None = Field(default=None, serialization_alias="noShow", validation_alias="noShow")


class RouteCondition(AnalysisModel):
    type: str
    explained_distance_km: float = Field(serialization_alias="explainedDistanceKm")
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds")


class RouteDeviationAnalysis(AnalysisModel):
    analysis_type: Literal["route_deviation"] = Field(default="route_deviation", serialization_alias="analysisType")
    expected_route_distance_km: float = Field(serialization_alias="expectedRouteDistanceKm")
    actual_route_distance_km: float = Field(serialization_alias="actualRouteDistanceKm")
    distance_difference_km: float = Field(serialization_alias="distanceDifferenceKm")
    deviation_percentage: float = Field(serialization_alias="deviationPercentage")
    expected_trip_duration_minutes: float = Field(serialization_alias="expectedTripDurationMinutes")
    actual_trip_duration_minutes: float = Field(serialization_alias="actualTripDurationMinutes")
    duration_difference_minutes: float = Field(serialization_alias="durationDifferenceMinutes")
    expected_fare: float = Field(serialization_alias="expectedFare")
    actual_fare: float = Field(serialization_alias="actualFare")
    fare_difference: float = Field(serialization_alias="fareDifference")
    explained_deviation_distance_km: float = Field(serialization_alias="explainedDeviationDistanceKm")
    unexplained_deviation_distance_km: float = Field(serialization_alias="unexplainedDeviationDistanceKm")
    conditions: list[RouteCondition]
    required_evidence_ids: list[str] = Field(serialization_alias="requiredEvidenceIds")
    missing_evidence_ids: list[str] = Field(serialization_alias="missingEvidenceIds")
    contradictory_evidence: bool = Field(serialization_alias="contradictoryEvidence")
    supporting_evidence_ids: list[str] = Field(serialization_alias="supportingEvidenceIds")


class NoShowAnalysis(AnalysisModel):
    analysis_type: Literal["no_show_charge"] = Field(default="no_show_charge", serialization_alias="analysisType")
    driver_distance_to_pickup_meters: float = Field(serialization_alias="driverDistanceToPickupMeters")
    driver_within_pickup_radius: bool = Field(serialization_alias="driverWithinPickupRadius")
    driver_arrival_timestamp: str = Field(serialization_alias="driverArrivalTimestamp")
    cancellation_timestamp: str = Field(serialization_alias="cancellationTimestamp")
    waiting_duration_seconds: int = Field(serialization_alias="waitingDurationSeconds")
    rider_message_evidence_ids: list[str] = Field(serialization_alias="riderMessageEvidenceIds")
    driver_message_evidence_ids: list[str] = Field(serialization_alias="driverMessageEvidenceIds")
    cancellation_charge_amount: float = Field(serialization_alias="cancellationChargeAmount")
    required_evidence_ids: list[str] = Field(serialization_alias="requiredEvidenceIds")
    missing_evidence_ids: list[str] = Field(serialization_alias="missingEvidenceIds")
    contradictory_evidence: bool = Field(serialization_alias="contradictoryEvidence")
    supporting_evidence_ids: list[str] = Field(serialization_alias="supportingEvidenceIds")


class RuleEvaluation(AnalysisModel):
    rule_id: str = Field(serialization_alias="ruleId")
    description: str
    passed: bool
    actual_value: str | float | int | bool = Field(serialization_alias="actualValue")
    required_value: str | float | int | bool = Field(serialization_alias="requiredValue")
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds")


class PolicyEvaluation(AnalysisModel):
    policy_id: str = Field(serialization_alias="policyId")
    policy_version: str = Field(serialization_alias="policyVersion")
    evaluated_rules: list[RuleEvaluation] = Field(serialization_alias="evaluatedRules")
    passed_rules: list[str] = Field(serialization_alias="passedRules")
    failed_rules: list[str] = Field(serialization_alias="failedRules")
    supporting_evidence_ids: list[str] = Field(serialization_alias="supportingEvidenceIds")
    overall_outcome: str = Field(serialization_alias="overallOutcome")


class ResolutionCalculation(AnalysisModel):
    fare_difference: float = Field(serialization_alias="fareDifference")
    explained_fare_impact: float = Field(serialization_alias="explainedFareImpact")
    unexplained_fare_impact: float = Field(serialization_alias="unexplainedFareImpact")
    ratio_applied: float = Field(serialization_alias="ratioApplied")


class ResolutionRecommendation(AnalysisModel):
    ruling: str
    recommended_action: str = Field(serialization_alias="recommendedAction")
    refund_amount: float = Field(serialization_alias="refundAmount")
    currency: str
    calculation: ResolutionCalculation
    explanation: str
    counterfactual_explanation: str = Field(serialization_alias="counterfactualExplanation")


class DeterministicConfidence(AnalysisModel):
    evidence_completeness: float = Field(serialization_alias="evidenceCompleteness")
    policy_clarity: float = Field(serialization_alias="policyClarity")
    factual_consistency: float = Field(serialization_alias="factualConsistency")
    invalid_evidence_penalty: float = Field(serialization_alias="invalidEvidencePenalty")
    contradiction_penalty: float = Field(serialization_alias="contradictionPenalty")
    missing_data_penalty: float = Field(serialization_alias="missingDataPenalty")
    overall_confidence: float = Field(serialization_alias="overallConfidence")
    prototype_notice: str = Field(serialization_alias="prototypeNotice")


class CaseAnalysisResponse(AnalysisModel):
    case_id: str = Field(serialization_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(serialization_alias="disputeType")
    analysis: RouteDeviationAnalysis | NoShowAnalysis
    policy_evaluation: PolicyEvaluation = Field(serialization_alias="policyEvaluation")
    resolution_recommendation: ResolutionRecommendation = Field(serialization_alias="resolutionRecommendation")
    confidence: DeterministicConfidence
    resolution_mode: Literal["AUTO_RESOLVE", "HUMAN_REVIEW"] = Field(serialization_alias="resolutionMode")
    escalation_reasons: list[str] = Field(serialization_alias="escalationReasons")
