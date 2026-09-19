from __future__ import annotations

from app.models.analysis import CaseAnalysisResponse
from app.models.case import CaseMetadata, CaseSummary, ConfidenceBreakdown, DisputeCase, PolicyResult, Resolution
from app.repositories.case_repository import CaseRepository
from app.services.case_replay import CaseReplayService
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.evidence_validation import EvidenceValidationService


class CaseNotFoundError(LookupError):
    pass


class CaseService:
    def __init__(
        self,
        repository: CaseRepository,
        replay_service: CaseReplayService | None = None,
        evidence_validation: EvidenceValidationService | None = None,
        analysis_service: DisputeAnalysisService | None = None,
    ) -> None:
        self._repository = repository
        self._evidence_validation = evidence_validation or EvidenceValidationService()
        self._replay_service = replay_service or CaseReplayService(self._evidence_validation)
        self._analysis_service = analysis_service or DisputeAnalysisService(self._evidence_validation)

    def list_case_summaries(self) -> list[CaseSummary]:
        summaries = []
        for raw_case in self._repository.list_cases():
            case = self._with_calculated_display(self._validated_case(raw_case), self._analysis_service.analyze(self._validated_case(raw_case)))
            summaries.append(CaseSummary(
                id=case.id,
                dispute_type=case.dispute_type,
                title=case.title,
                status=case.status,
                rider=case.rider,
                driver=case.driver,
                confidence=case.confidence,
                resolution=case.resolution,
            ))
        return summaries

    def get_case(self, case_id: str) -> DisputeCase:
        case = self._load_case(case_id)
        analysis = self._analysis_service.analyze(case)
        return self._with_calculated_display(case, analysis)

    def get_analysis(self, case_id: str) -> CaseAnalysisResponse:
        return self._analysis_service.analyze(self._load_case(case_id))

    def _load_case(self, case_id: str) -> DisputeCase:
        case = self._repository.get_case(case_id)
        if case is None:
            raise CaseNotFoundError(case_id)
        return self._validated_case(case)

    def _validated_case(self, case: DisputeCase) -> DisputeCase:
        evidence_result = self._evidence_validation.validate_evidence(case.evidence)
        if not evidence_result.valid:
            raise ValueError("Case contains duplicate evidence IDs")
        canonical_timeline = self._replay_service.canonical_timeline(case.timeline, case.evidence)
        claim_result = self._evidence_validation.validate_advocate_references([case.rider_case, case.driver_case], case.evidence)
        if not claim_result.valid:
            raise ValueError("Case contains invalid evidence references in active advocate claims")
        return case.model_copy(update={"timeline": canonical_timeline})

    @staticmethod
    def _with_calculated_display(case: DisputeCase, analysis: CaseAnalysisResponse) -> DisputeCase:
        confidence = analysis.confidence
        policy = analysis.policy_evaluation
        recommendation = analysis.resolution_recommendation
        frontend_confidence = ConfidenceBreakdown(
            evidence_completeness=round(confidence.evidence_completeness * 100),
            contradictory_evidence=round(confidence.factual_consistency * 100),
            policy_clarity=round(confidence.policy_clarity * 100),
            missing_information=round((1 - confidence.missing_data_penalty) * 100),
            advocate_disagreement=round((1 - confidence.invalid_evidence_penalty) * 100),
            overall=round(confidence.overall_confidence * 100),
        )
        frontend_resolution = Resolution(
            ruling=recommendation.ruling,
            recommended_action=recommendation.recommended_action,
            refund_amount=recommendation.refund_amount,
            currency=recommendation.currency,
            accepted_claim_ids=[claim.id for claim in [*case.rider_case.claims, *case.driver_case.claims] if claim.status == "Verified"],
            rejected_claim_ids=[claim.id for claim in [*case.rider_case.claims, *case.driver_case.claims] if claim.status == "Rejected"],
            mode=analysis.resolution_mode,
            explanation=recommendation.explanation,
            counterfactual_explanation=recommendation.counterfactual_explanation,
            escalation_reason="; ".join(analysis.escalation_reasons) if analysis.escalation_reasons else None,
        )
        return case.model_copy(update={
            "confidence": frontend_confidence,
            "policy_result": PolicyResult(policy_id=policy.policy_id, name="Deterministic prototype policy", rule_summary="PolicyTwin evaluates centralized prototype rules.", outcome=policy.overall_outcome),
            "resolution": frontend_resolution,
            "human_review_summary": "Deterministic escalation reasons: " + ", ".join(analysis.escalation_reasons) if analysis.resolution_mode == "HUMAN_REVIEW" else None,
        })
