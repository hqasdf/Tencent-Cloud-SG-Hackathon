from __future__ import annotations

from app.models.analysis import CaseAnalysisResponse, ResolutionRecommendation
from app.models.case import DisputeCase
from app.policies import CONFIDENCE_POLICY_V1, NO_SHOW_POLICY_V1, ROUTE_DEVIATION_POLICY_V1
from app.services.confidence import ConfidenceEngine
from app.services.escalation import EscalationEngine
from app.services.evidence_validation import EvidenceValidationResult, EvidenceValidationService
from app.services.no_show_analysis import NoShowAnalysisService
from app.services.policy_evaluation import PolicyEvaluationService
from app.services.resolution import ResolutionEngine
from app.services.route_deviation import RouteDeviationService


class DisputeAnalysisService:
    def __init__(
        self,
        evidence_validation: EvidenceValidationService | None = None,
        route_service: RouteDeviationService | None = None,
        no_show_service: NoShowAnalysisService | None = None,
        policy_evaluation: PolicyEvaluationService | None = None,
        resolution_engine: ResolutionEngine | None = None,
        confidence_engine: ConfidenceEngine | None = None,
        escalation_engine: EscalationEngine | None = None,
    ) -> None:
        self._evidence_validation = evidence_validation or EvidenceValidationService()
        self._route_service = route_service or RouteDeviationService()
        self._no_show_service = no_show_service or NoShowAnalysisService()
        self._policy_evaluation = policy_evaluation or PolicyEvaluationService()
        self._resolution_engine = resolution_engine or ResolutionEngine()
        self._confidence_engine = confidence_engine or ConfidenceEngine()
        self._escalation_engine = escalation_engine or EscalationEngine()

    def analyze(self, case: DisputeCase) -> CaseAnalysisResponse:
        evidence_result = self._combined_evidence_validation(case)
        if case.dispute_type == "route_deviation":
            analysis = self._route_service.analyze(case, ROUTE_DEVIATION_POLICY_V1)
            policy_evaluation = self._policy_evaluation.evaluate_route(analysis, ROUTE_DEVIATION_POLICY_V1)
            recommendation = self._resolution_engine.recommend_route(analysis, policy_evaluation, case.fare.currency)
            threshold = ROUTE_DEVIATION_POLICY_V1.auto_resolve_confidence_threshold
        else:
            analysis = self._no_show_service.analyze(case, NO_SHOW_POLICY_V1)
            policy_evaluation = self._policy_evaluation.evaluate_no_show(analysis, NO_SHOW_POLICY_V1)
            recommendation = self._resolution_engine.recommend_no_show(analysis, policy_evaluation, case.fare.currency)
            threshold = NO_SHOW_POLICY_V1.auto_resolve_confidence_threshold

        confidence = self._confidence_engine.calculate(analysis, policy_evaluation, evidence_result, CONFIDENCE_POLICY_V1)
        mode, reasons = self._escalation_engine.decide(analysis, policy_evaluation, confidence, evidence_result, threshold)
        if mode == "HUMAN_REVIEW" and recommendation.recommended_action != "HUMAN_REVIEW":
            recommendation = recommendation.model_copy(update={
                "ruling": "Pending human review",
                "recommended_action": "HUMAN_REVIEW",
                "refund_amount": 0,
                "explanation": "A deterministic review gate requires human handling before any recommendation can be actioned.",
            })
        return CaseAnalysisResponse(
            case_id=case.id,
            dispute_type=case.dispute_type,
            analysis=analysis,
            policy_evaluation=policy_evaluation,
            resolution_recommendation=recommendation,
            confidence=confidence,
            resolution_mode=mode,
            escalation_reasons=reasons,
        )

    def _combined_evidence_validation(self, case: DisputeCase) -> EvidenceValidationResult:
        evidence = self._evidence_validation.validate_evidence(case.evidence)
        claims = self._evidence_validation.validate_advocate_references([case.rider_case, case.driver_case], case.evidence)
        return EvidenceValidationResult(valid=evidence.valid and claims.valid, issues=[*evidence.issues, *claims.issues])
