from __future__ import annotations

from app.data.trip_mapping import get_source_case
from app.models.analysis import (
    CaseAnalysisResponse,
    IntakeContext,
    PartyContradiction,
    PartyStatement,
)
from app.models.intake import IntakeCase, InterviewState, PartyFact
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

    * Attaches attributed allegations for advocates and human reviewers.
      Raw allegations are excluded from the advisory Judge's context.
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
        if case.lifecycle == "RIDER_REBUTTAL":
            raise IntakeAnalysisError("Complete or skip the rider clarification before analysis.")

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

        analysis = attach_intake_context(analysis, case)

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

def attach_intake_context(analysis: CaseAnalysisResponse, case: IntakeCase) -> CaseAnalysisResponse:
    """Project persisted allegations; never replace calculation inputs or evidence."""
    from app.agents.context_builder import AdvocateContextBuilder
    from app.agents.claim_verification import fact_values, values_match
    from app.agents.semantic_registry import supporting_evidence_ids

    if case.source_case_id != analysis.case_id:
        raise IntakeAnalysisError("Intake belongs to a different source case.")
    source = get_source_case(case.trip_id)
    if source is None or source.id != analysis.case_id:
        raise IntakeAnalysisError("Intake trip does not match the source case.")
    rider_done = bool(case.rider_state and case.rider_state.interview_complete)
    driver_done = bool(case.driver_state and case.driver_state.interview_complete)
    comparison_allowed = rider_done and driver_done and case.lifecycle in {
        "READY_FOR_ANALYSIS", "ANALYSING", "AUTO_RESOLVED", "HUMAN_REVIEW",
    }
    user_messages = {m.id: m.party for m in case.messages if m.sender == "user"}
    def attributed_state(state: InterviewState | None, party: str) -> InterviewState | None:
        if state is None:
            return None
        return state.model_copy(update={"facts": [f for f in state.facts if f.stated_by == party
            and (not f.source_message_id or user_messages.get(f.source_message_id) == party)]})
    rider_state = attributed_state(case.rider_state, "rider")
    driver_state = attributed_state(case.driver_state, "driver")
    statements = _build_party_statements(rider_state, driver_state)
    context = AdvocateContextBuilder().build(source, analysis)
    trusted = fact_values(context.facts)
    for statement in statements:
        for fact in statement.facts:
            key = fact["key"].upper()
            evidence = supporting_evidence_ids(context, key) if not analysis.analysis.contradictory_evidence else []
            fact["evidenceStatus"] = "ALLEGED"
            if key in trusted and evidence:
                expected = trusted[key]
                value = _typed_allegation(fact["value"], expected)
                if value is not None:
                    fact["evidenceStatus"] = "SUPPORTED_BY_EVIDENCE" if values_match(expected, value) else "CONTRADICTED_BY_EVIDENCE"
                    fact["supportedValue"] = expected
                    fact["evidenceIds"] = evidence
    contradictions = _detect_contradictions(rider_state, driver_state) if comparison_allowed else []
    for contradiction in contradictions:
        key = contradiction.fact_key.upper()
        evidence = supporting_evidence_ids(context, key) if not analysis.analysis.contradictory_evidence else []
        if key in trusted and evidence and all(_typed_allegation(v, trusted[key]) is not None
                                              for v in (contradiction.rider_claim, contradiction.driver_claim)):
            contradiction.status = "RESOLVED_BY_EVIDENCE"
            contradiction.supported_value = trusted[key]
            contradiction.evidence_ids = evidence
    unresolved = [c for c in contradictions if c.status == "DISPUTED"]
    updates = {
        "party_statements": statements, "party_contradictions": contradictions,
        "intake_context": IntakeContext(
            intake_case_id=case.id, lifecycle=case.lifecycle, rider_complete=rider_done,
            driver_complete=driver_done, comparison_allowed=comparison_allowed,
            party_statements=statements, party_contradictions=contradictions,
        ),
    }
    if unresolved:
        penalty = _PARTY_CONTRADICTION_PENALTY * len(unresolved)
        updates.update({
            "confidence": analysis.confidence.model_copy(update={
                "overall_confidence": round(max(0.0, analysis.confidence.overall_confidence - penalty), 2),
                "contradiction_penalty": round(analysis.confidence.contradiction_penalty + penalty, 2),
            }),
            "resolution_mode": "HUMAN_REVIEW",
            "escalation_reasons": list(dict.fromkeys([*analysis.escalation_reasons, "PARTY_CONTRADICTION"])),
        })
    return analysis.model_copy(update=updates)


def _typed_allegation(value: str, expected: object) -> object | None:
    """Compare exact registered keys only; never infer facts from interview prose."""
    if isinstance(expected, bool):
        return {"true": True, "false": False}.get(value.strip().lower())
    if isinstance(expected, (int, float)):
        import math
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except ValueError:
            return None
    return value


def _facts_to_dicts(facts: list[PartyFact]) -> list[dict]:
    """Expose only active, party-attributed allegations to the legacy response."""
    return [
        f.model_dump(by_alias=True)
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
                    rider_source_message_id=next(f.source_message_id for f in reversed(rider_state.facts) if f.active and f.key.lower() == key),
                    driver_source_message_id=next(f.source_message_id for f in reversed(driver_state.facts) if f.active and f.key.lower() == key),
                ))
    return contradictions
