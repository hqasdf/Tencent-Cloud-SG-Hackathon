from __future__ import annotations

from app.models.advocate import AdvocateRunResponse
from app.models.analysis import CaseAnalysisResponse
from app.models.case import CaseMetadata, CaseSummary, ConfidenceBreakdown, DisputeCase, PolicyResult, Resolution
from app.models.judge import CaseResolutionResponse
from app.models.replay import NO_REPLAY, ReplayMode
from app.repositories.case_repository import CaseRepository
from app.repositories.intake_repository import IntakeCaseRepository
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
        intake_repository: IntakeCaseRepository | None = None,
    ) -> None:
        self._repository = repository
        self._evidence_validation = evidence_validation or EvidenceValidationService()
        self._replay_service = replay_service or CaseReplayService(self._evidence_validation)
        self._analysis_service = analysis_service or DisputeAnalysisService(self._evidence_validation)
        self._intake_repository = intake_repository

    def list_case_summaries(self) -> list[CaseSummary]:
        summaries = []
        for raw_case in self._repository.list_cases():
            validated_case = self._validated_case(raw_case)
            case = self._with_calculated_display(validated_case, self._analysis_with_intake(validated_case))
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
        analysis = self._analysis_with_intake(case)
        return self._with_calculated_display(case, analysis)

    def get_analysis(self, case_id: str) -> CaseAnalysisResponse:
        return self._analysis_with_intake(self._load_case(case_id))

    def _analysis_with_intake(self, case: DisputeCase) -> CaseAnalysisResponse:
        analysis = self._analysis_service.analyze(case)
        if self._intake_repository is not None:
            intake = self._intake_repository.get_case_by_source(case.id)
            if intake is not None:
                from app.services.intake_analysis_service import attach_intake_context
                analysis = attach_intake_context(analysis, intake)
        return analysis

    def get_advocate_run(self, case_id: str) -> AdvocateRunResponse:
        """Run the Stage 4 advocates for a case.

        The case and its deterministic analysis are computed through the same
        canonical path used by get_case and get_analysis, so advocate results can
        never disagree with the deterministic record. The orchestrator receives
        them read-only and cannot mutate them.
        """
        from app.services.advocate_orchestrator import AdvocateOrchestratorService

        case = self._load_case(case_id)
        analysis = self._analysis_with_intake(case)
        from app.cloud_storage import storage_backend
        if storage_backend() == 'turso':
            from app.agents.config import AgentConfigurationError, AgentSettings
            if AgentSettings.from_environment().mode == 'real':
                raise AgentConfigurationError('Cloud live advocates must use the checkpointed full resolution endpoint')
        orchestrator = AdvocateOrchestratorService()
        from app.services.agent_checkpoints import AgentCheckpointStore
        # Same case lock as full resolution; the standalone endpoint must not
        # purchase overlapping calls that bypass the full-run request guard.
        with AgentCheckpointStore().request_lock(case_id):
            return orchestrator.run(case, analysis)

    def get_resolution_run(
        self, case_id: str, replay_mode: ReplayMode = NO_REPLAY
    ) -> CaseResolutionResponse:
        """Run the full Stage 6 pipeline: advocates, rebuttals, Judge, remedy.

        Kept separate from ``get_advocate_run`` so the Stage 4 endpoint's response
        contract is untouched. The deterministic analysis is computed once here
        and passed read-only to the orchestrator, exactly as in Stage 4.

        ``replay_mode`` defaults to ``NONE``, so every existing caller keeps the
        live behaviour. A replayed run raises ``ReplayRefused`` rather than
        falling back to a provider call, and it does **not** change the
        deterministic analysis computed here — that is always live.
        """
        from app.services.resolution_orchestrator import ResolutionOrchestratorService

        case = self._load_case(case_id)
        analysis = self._analysis_with_intake(case)
        orchestrator = ResolutionOrchestratorService()
        from app.cloud_storage import storage_backend
        if storage_backend() == 'turso' and replay_mode != NO_REPLAY:
            from app.services.agent_checkpoints import AgentCheckpointStore
            with AgentCheckpointStore().request_lock(case_id):
                return orchestrator.run(case, analysis, replay_mode=replay_mode, resume=False)
        return orchestrator.run(case, analysis, replay_mode=replay_mode, resume=replay_mode == NO_REPLAY)

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
            "status": "Human Review" if analysis.intake_context and analysis.resolution_mode == "HUMAN_REVIEW" else case.status,
            "confidence": frontend_confidence,
            "policy_result": PolicyResult(policy_id=policy.policy_id, name="Deterministic prototype policy", rule_summary="PolicyTwin evaluates centralized prototype rules.", outcome=policy.overall_outcome),
            "resolution": frontend_resolution,
            "human_review_summary": "Deterministic escalation reasons: " + ", ".join(analysis.escalation_reasons) if analysis.resolution_mode == "HUMAN_REVIEW" else None,
        })
