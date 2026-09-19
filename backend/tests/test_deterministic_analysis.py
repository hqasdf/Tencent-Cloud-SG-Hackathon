from app.data.cases import MOCK_CASES
from app.models.analysis import GeoPoint
from app.policies import CONFIDENCE_POLICY_V1, NO_SHOW_POLICY_V1, ROUTE_DEVIATION_POLICY_V1
from app.services.confidence import ConfidenceEngine
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.escalation import EscalationEngine
from app.services.evidence_validation import EvidenceValidationResult, ValidationIssue
from app.services.no_show_analysis import NoShowAnalysisService
from app.services.policy_evaluation import PolicyEvaluationService
from app.services.resolution import ResolutionEngine
from app.services.route_deviation import RouteDeviationService


def case(case_id: str):
    return next(item for item in MOCK_CASES if item.id == case_id)


def test_route_deviation_calculates_distance_duration_fare_and_explanations() -> None:
    analysis = RouteDeviationService().analyze(case("CASE-2026-1041"), ROUTE_DEVIATION_POLICY_V1)
    assert analysis.distance_difference_km == 1.3
    assert analysis.deviation_percentage == 22.41
    assert analysis.duration_difference_minutes == 8
    assert analysis.fare_difference == 2.3
    assert analysis.explained_deviation_distance_km == 0.5
    assert analysis.unexplained_deviation_distance_km == 0.8


def test_route_deviation_with_full_explanation_has_zero_unexplained_distance_and_no_refund() -> None:
    route_case = case("CASE-2026-1042")
    analysis = RouteDeviationService().analyze(route_case, ROUTE_DEVIATION_POLICY_V1)
    policy = PolicyEvaluationService().evaluate_route(analysis, ROUTE_DEVIATION_POLICY_V1)
    recommendation = ResolutionEngine().recommend_route(analysis, policy, "SGD")
    assert analysis.unexplained_deviation_distance_km == 0
    assert policy.overall_outcome == "NO_ELIGIBLE_UNEXPLAINED_DEVIATION"
    assert recommendation.recommended_action == "NO_REFUND"
    assert recommendation.refund_amount == 0


def test_route_deviation_partial_refund_is_proportional_to_unexplained_distance() -> None:
    route_case = case("CASE-2026-1041")
    analysis = RouteDeviationService().analyze(route_case, ROUTE_DEVIATION_POLICY_V1)
    policy = PolicyEvaluationService().evaluate_route(analysis, ROUTE_DEVIATION_POLICY_V1)
    recommendation = ResolutionEngine().recommend_route(analysis, policy, "SGD")
    assert policy.overall_outcome == "ELIGIBLE_ROUTE_ADJUSTMENT"
    assert recommendation.recommended_action == "PARTIAL_REFUND"
    assert recommendation.refund_amount == 1.42
    assert recommendation.calculation.explained_fare_impact == 0.88
    assert recommendation.calculation.unexplained_fare_impact == 1.42


def test_no_show_calculates_driver_radius_wait_time_and_cancellation_timing() -> None:
    analysis = NoShowAnalysisService().analyze(case("CASE-2026-1043"), NO_SHOW_POLICY_V1)
    assert analysis.driver_distance_to_pickup_meters < NO_SHOW_POLICY_V1.pickup_radius_meters
    assert analysis.driver_within_pickup_radius is True
    assert analysis.waiting_duration_seconds == 372


def test_no_show_outside_radius_or_below_wait_threshold_fails_policy() -> None:
    base = case("CASE-2026-1043")
    changed_input = base.analysis_input.model_copy(update={
        "no_show": base.analysis_input.no_show.model_copy(update={
            "driver_arrival_coordinates": GeoPoint(latitude=1.288, longitude=103.827),
            "cancellation_timestamp": "09:11:00",
        })
    })
    changed_case = base.model_copy(update={"analysis_input": changed_input})
    analysis = NoShowAnalysisService().analyze(changed_case, NO_SHOW_POLICY_V1)
    policy = PolicyEvaluationService().evaluate_no_show(analysis, NO_SHOW_POLICY_V1)
    recommendation = ResolutionEngine().recommend_no_show(analysis, policy, "SGD")
    assert analysis.driver_within_pickup_radius is False
    assert analysis.waiting_duration_seconds == 120
    assert policy.overall_outcome == "REFUND_CANCELLATION_CHARGE"
    assert recommendation.recommended_action == "REFUND_CANCELLATION_CHARGE"
    assert recommendation.refund_amount == 6


def test_confidence_penalties_and_escalation_are_deterministic() -> None:
    route_analysis = RouteDeviationService().analyze(case("CASE-2026-1041"), ROUTE_DEVIATION_POLICY_V1)
    policy = PolicyEvaluationService().evaluate_route(route_analysis, ROUTE_DEVIATION_POLICY_V1)
    complete = ConfidenceEngine().calculate(route_analysis, policy, EvidenceValidationResult(True, []), CONFIDENCE_POLICY_V1)
    assert complete.overall_confidence == 0.95

    degraded = route_analysis.model_copy(update={"missing_evidence_ids": ["E05"], "contradictory_evidence": True})
    degraded_policy = PolicyEvaluationService().evaluate_route(degraded, ROUTE_DEVIATION_POLICY_V1)
    invalid = EvidenceValidationResult(False, [ValidationIssue("INVALID_CLAIM_EVIDENCE", "Invalid reference", "C01")])
    low = ConfidenceEngine().calculate(degraded, degraded_policy, invalid, CONFIDENCE_POLICY_V1)
    mode, reasons = EscalationEngine().decide(degraded, degraded_policy, low, invalid, ROUTE_DEVIATION_POLICY_V1.auto_resolve_confidence_threshold)
    assert low.overall_confidence < ROUTE_DEVIATION_POLICY_V1.auto_resolve_confidence_threshold
    assert low.invalid_evidence_penalty == 0.25
    assert mode == "HUMAN_REVIEW"
    assert {"MISSING_CRITICAL_EVIDENCE", "CONTRADICTORY_EVIDENCE", "INVALID_EVIDENCE_REFERENCES", "LOW_CONFIDENCE"}.issubset(reasons)


def test_all_four_cases_have_calculated_outcomes_without_case_id_branches() -> None:
    service = DisputeAnalysisService()
    results = {item.id: service.analyze(item) for item in MOCK_CASES}
    assert results["CASE-2026-1041"].resolution_recommendation.recommended_action == "PARTIAL_REFUND"
    assert results["CASE-2026-1041"].resolution_mode == "AUTO_RESOLVE"
    assert results["CASE-2026-1042"].resolution_recommendation.recommended_action == "NO_REFUND"
    assert results["CASE-2026-1042"].resolution_mode == "AUTO_RESOLVE"
    assert results["CASE-2026-1043"].resolution_recommendation.recommended_action == "UPHOLD_CANCELLATION_CHARGE"
    assert results["CASE-2026-1043"].resolution_mode == "AUTO_RESOLVE"
    assert results["CASE-2026-1044"].resolution_recommendation.recommended_action == "HUMAN_REVIEW"
    assert results["CASE-2026-1044"].resolution_mode == "HUMAN_REVIEW"
