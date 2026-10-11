from __future__ import annotations

from app.data.trip_mapping import get_source_case
from app.models.analysis import (
    CaseAnalysisResponse,
    PartyContradiction,
    PartyStatement,
)
from app.models.intake import PartyFact
from app.policies import CONFIDENCE_POLICY_V1
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.case_replay import CaseReplayService
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.evidence_validation import EvidenceValidationService


# How much extra confidence penalty to apply per party contradiction
_PARTY_CONTRADICTION_PENALTY = 0.15


class IntakeAnalysisError(Exception):
    """Raised when analysis cannot proceed (e.g. interviews not complete)."""


class IntakeAnalysisService:
    """Verifies both interviews are complete, loads the selected synthetic source
    case, invokes the current DisputeAnalysisService, and saves the returned
    analysis.

    After the deterministic engine produces its result, this service:

    * Attaches the rider and driver party statements (B) so a human reviewer
      or the Judge step can see what each party actually said during intake.
    * Detects contradictions between rider and driver claims on the same fact
      key (C). When contradictions are found, the confidence score is lowered
      and the case is forced to HUMAN_REVIEW with a ``PARTY_CONTRADICTION``
      escalation reason.
    """

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

        # --- B: Attach party statements from the intake interviews ---
        party_statements = _build_party_statements(case.rider_state, case.driver_state)

        # --- C: Detect contradictions between rider and driver claims ---
        contradictions = _detect_contradictions(case.rider_state, case.driver_state)

        if contradictions:
            # Lower the confidence score
            penalty = _PARTY_CONTRADICTION_PENALTY * len(contradictions)
            adjusted_confidence = max(
                0.0,
                analysis.confidence.overall_confidence - penalty,
            )
            analysis = analysis.model_copy(update={
                "confidence": analysis.confidence.model_copy(update={
                    "overall_confidence": round(adjusted_confidence, 2),
                    "contradiction_penalty": round(
                        analysis.confidence.contradiction_penalty + penalty, 2
                    ),
                }),
                # Force human review when parties contradict each other
                "resolution_mode": "HUMAN_REVIEW",
                "escalation_reasons": analysis.escalation_reasons + ["PARTY_CONTRADICTION"],
            })

        # Attach the party statements and contradictions to the response
        analysis = analysis.model_copy(update={
            "party_statements": party_statements,
            "party_contradictions": contradictions,
        })

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _facts_to_dicts(facts: list[PartyFact]) -> list[dict]:
    """Expose only active, party-attributed allegations to the legacy response."""
    return [
        {"key": f.key, "value": f.value, "statedBy": f.stated_by}
        for f in facts
        if f.active
    ]


def _build_party_statements(
    rider_state: object | None,
    driver_state: object | None,
) -> list[PartyStatement]:
    """Build PartyStatement objects from the intake interview states."""
    statements: list[PartyStatement] = []
    if rider_state is not None and rider_state.facts:
        statements.append(PartyStatement(
            party="rider",
            facts=_facts_to_dicts(rider_state.facts),
        ))
    if driver_state is not None and driver_state.facts:
        statements.append(PartyStatement(
            party="driver",
            facts=_facts_to_dicts(driver_state.facts),
        ))
    return statements


def _build_fact_map(state: object | None) -> dict[str, str]:
    """Map fact key → fact value for one party's interview state."""
    if state is None or not state.facts:
        return {}
    return {f.key.lower(): f.value for f in state.facts if f.active}


def _detect_contradictions(
    rider_state: object | None,
    driver_state: object | None,
) -> list[PartyContradiction]:
    """Find fact keys where both parties gave a value but they disagree.

    A contradiction is when both parties stated a fact with the same key, but
    the values differ (case-insensitive comparison). Facts only stated by one
    party are NOT contradictions — they're just one-sided claims.
    """
    rider_facts = _build_fact_map(rider_state)
    driver_facts = _build_fact_map(driver_state)

    contradictions: list[PartyContradiction] = []
    for key in rider_facts:
        if key in driver_facts:
            rider_val = rider_facts[key]
            driver_val = driver_facts[key]
            if rider_val.strip().lower() != driver_val.strip().lower():
                contradictions.append(PartyContradiction(
                    fact_key=key,
                    rider_claim=rider_val,
                    driver_claim=driver_val,
                ))
    return contradictions
