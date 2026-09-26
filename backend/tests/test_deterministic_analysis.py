from app.data.cases import MOCK_CASES
from app.models.analysis import GeoPoint
from app.policies import CONFIDENCE_POLICY_V1, NO_SHOW_POLICY_V1
from app.services.confidence import ConfidenceEngine
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.escalation import EscalationEngine
from app.services.evidence_validation import EvidenceValidationResult, ValidationIssue
from app.services.no_show_analysis import NoShowAnalysisService
from app.services.policy_evaluation import PolicyEvaluationService
from app.services.resolution import ResolutionEngine


def case(case_id: str):
    return next(item for item in MOCK_CASES if item.id == case_id)


def test_no_show_calculates_driver_radius_wait_time_and_cancellation_timing() -> None:
    analysis = NoShowAnalysisService().analyze(case("DISP-002"), NO_SHOW_POLICY_V1)
    assert analysis.driver_distance_to_pickup_meters == 0.0
    assert analysis.driver_within_pickup_radius is True
    assert analysis.waiting_duration_seconds == 480
    assert analysis.cancellation_charge_amount == 5.0
    assert analysis.rider_message_evidence_ids == []
    assert analysis.driver_message_evidence_ids == ["E04"]


def test_no_show_outside_radius_or_below_wait_threshold_fails_policy() -> None:
    base = case("DISP-002")
    changed_input = base.analysis_input.model_copy(update={
        "no_show": base.analysis_input.no_show.model_copy(update={
            "driver_arrival_coordinates": GeoPoint(latitude=1.288, longitude=103.827),
            "cancellation_timestamp": "08:44:00",
        })
    })
    changed_case = base.model_copy(update={"analysis_input": changed_input})
    analysis = NoShowAnalysisService().analyze(changed_case, NO_SHOW_POLICY_V1)
    policy = PolicyEvaluationService().evaluate_no_show(analysis, NO_SHOW_POLICY_V1)
    recommendation = ResolutionEngine().recommend_no_show(analysis, policy, "SGD")
    assert analysis.driver_within_pickup_radius is False
    assert analysis.waiting_duration_seconds == 60
    assert policy.overall_outcome == "REFUND_CANCELLATION_CHARGE"
    assert recommendation.recommended_action == "REFUND_CANCELLATION_CHARGE"
    assert recommendation.refund_amount == 5.0


def test_confidence_penalties_and_escalation_are_deterministic() -> None:
    analysis = NoShowAnalysisService().analyze(case("DISP-002"), NO_SHOW_POLICY_V1)
    policy = PolicyEvaluationService().evaluate_no_show(analysis, NO_SHOW_POLICY_V1)
    complete = ConfidenceEngine().calculate(analysis, policy, EvidenceValidationResult(True, []), CONFIDENCE_POLICY_V1)
    assert complete.overall_confidence == 0.95

    degraded = analysis.model_copy(update={"missing_evidence_ids": ["E05"], "contradictory_evidence": True})
    degraded_policy = PolicyEvaluationService().evaluate_no_show(degraded, NO_SHOW_POLICY_V1)
    invalid = EvidenceValidationResult(False, [ValidationIssue("INVALID_CLAIM_EVIDENCE", "Invalid reference", "C01")])
    low = ConfidenceEngine().calculate(degraded, degraded_policy, invalid, CONFIDENCE_POLICY_V1)
    mode, reasons = EscalationEngine().decide(degraded, degraded_policy, low, invalid, NO_SHOW_POLICY_V1.auto_resolve_confidence_threshold)
    assert low.overall_confidence < NO_SHOW_POLICY_V1.auto_resolve_confidence_threshold
    assert low.invalid_evidence_penalty == 0.25
    assert mode == "HUMAN_REVIEW"
    assert {"MISSING_CRITICAL_EVIDENCE", "CONTRADICTORY_EVIDENCE", "INVALID_EVIDENCE_REFERENCES", "LOW_CONFIDENCE"}.issubset(reasons)


def test_disp002_case_has_upheld_cancellation_charge_outcome() -> None:
    service = DisputeAnalysisService()
    result = service.analyze(MOCK_CASES[0])
    assert result.resolution_recommendation.recommended_action == "UPHOLD_CANCELLATION_CHARGE"
    assert result.resolution_mode == "AUTO_RESOLVE"
