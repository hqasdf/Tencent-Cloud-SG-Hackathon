from __future__ import annotations

from app.data.trip_mapping import get_source_case
from app.models.analysis import CaseAnalysisResponse
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.case_replay import CaseReplayService
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.evidence_validation import EvidenceValidationService


class IntakeAnalysisError(Exception):
    """Raised when analysis cannot proceed (e.g. interviews not complete)."""


class IntakeAnalysisService:
    """Verifies both interviews are complete, loads the selected synthetic source
    case, invokes the current DisputeAnalysisService, and saves the returned
    analysis. It does NOT treat party statements as verified evidence."""

    def __init__(
        self,
        repository: IntakeCaseRepository,
        dispute_analysis: DisputeAnalysisService | None = None,
    ) -> None:
        self._repository = repository
        self._dispute_analysis = dispute_analysis or DisputeAnalysisService()

    def analyse(self, intake_case_id: str) -> CaseAnalysisResponse:
        case = self._repository.get_case(intake_case_id)
        if case is None:
            raise LookupError(f"Intake case {intake_case_id} not found")

        rider_done = case.rider_state is not None and case.rider_state.interview_complete
        driver_done = case.driver_state is not None and case.driver_state.interview_complete
        if not (rider_done and driver_done):
            raise IntakeAnalysisError(
                "Both rider and driver interviews must be complete before analysis."
            )

        # Update lifecycle to ANALYSING
        self._repository.update_lifecycle(
            intake_case_id=intake_case_id,
            lifecycle="ANALYSING",
        )

        # Load the source case (deterministic synthetic data — NOT party statements)
        source_case = get_source_case(case.trip_id)
        if source_case is None:
            raise IntakeAnalysisError(
                f"Could not load source case for trip {case.trip_id}"
            )

        # Validate the source case (same validation as CaseService)
        evidence_validation = EvidenceValidationService()
        replay_service = CaseReplayService(evidence_validation)

        evidence_result = evidence_validation.validate_evidence(source_case.evidence)
        if not evidence_result.valid:
            raise IntakeAnalysisError("Source case contains duplicate evidence IDs")

        canonical_timeline = replay_service.canonical_timeline(
            source_case.timeline, source_case.evidence
        )
        claim_result = evidence_validation.validate_advocate_references(
            [source_case.rider_case, source_case.driver_case], source_case.evidence
        )
        if not claim_result.valid:
            raise IntakeAnalysisError(
                "Source case contains invalid evidence references in active advocate claims"
            )

        validated_case = source_case.model_copy(update={"timeline": canonical_timeline})

        # Run the existing deterministic analysis
        analysis = self._dispute_analysis.analyze(validated_case)

        # Save the analysis
        analysis_dict = analysis.model_dump(by_alias=True, mode="json")
        self._repository.save_analysis(
            intake_case_id=intake_case_id,
            analysis=analysis_dict,
        )

        # Update lifecycle based on resolution mode
        if analysis.resolution_mode == "AUTO_RESOLVE":
            self._repository.update_lifecycle(
                intake_case_id=intake_case_id,
                lifecycle="AUTO_RESOLVED",
            )
        else:
            self._repository.update_lifecycle(
                intake_case_id=intake_case_id,
                lifecycle="HUMAN_REVIEW",
            )

        return analysis
