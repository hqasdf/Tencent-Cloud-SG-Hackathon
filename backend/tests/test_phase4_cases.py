"""All supported fixtures and policy-permitted edge variants; injected transport only."""
import json

import pytest

from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.providers.mock import MockLlmProvider
from app.data.cases import MOCK_CASES
from app.models.analysis import RouteConditionInput, GeoPoint
from app.repositories.case_repository import MockCaseRepository
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.case_service import CaseService
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

# Expectations established from current structured fixtures + existing policy:
# raw case display resolutions are placeholders, not a golden policy oracle.
EXPECTED = {
    "DISP-002": ("UPHOLD_CANCELLATION_CHARGE", 0, "AUTO_RESOLVE"),
    "DISP-003": ("HUMAN_REVIEW", 0, "HUMAN_REVIEW"),
    "DISP-004": ("UPHOLD_CANCELLATION_CHARGE", 0, "AUTO_RESOLVE"),
    "DISP-005": ("NO_REFUND", 0, "AUTO_RESOLVE"),
    "DISP-006": ("HUMAN_REVIEW", 0, "HUMAN_REVIEW"),
    "DISP-007": ("FULL_FARE_DIFFERENCE_REFUND", 8.5, "AUTO_RESOLVE"),
    "DISP-008": ("NO_REFUND", 0, "AUTO_RESOLVE"),
    "DISP-009": ("HUMAN_REVIEW", 0, "HUMAN_REVIEW"),
}


class RecordingMock(MockLlmProvider):
    def __init__(self): self.requests = []
    def complete(self, request):
        self.requests.append(request)
        return super().complete(request)


def run(case, analysis=None, provider=None):
    analysis = analysis or DisputeAnalysisService().analyze(case)
    provider = provider or RecordingMock()
    result = ResolutionOrchestratorService(settings=AgentSettings(mode="mock", provider="mock"),
        provider=provider).run(case, analysis)
    return analysis, result, provider


@pytest.mark.parametrize("case_id", [c.id for c in MOCK_CASES])
def test_all_supported_cases_through_the_five_agent_pipeline(case_id, tmp_path):
    service = CaseService(MockCaseRepository(), intake_repository=IntakeCaseRepository(tmp_path / "intake.db"))
    case = service._load_case(case_id); analysis = service.get_analysis(case_id)
    before = (case.model_dump_json(), analysis.model_dump_json())
    _, result, provider = run(case, analysis)
    expected_action, expected_refund, expected_mode = EXPECTED[case_id]
    decision = result.deterministic_resolution
    assert (decision.recommended_action, decision.refund_amount, decision.resolution_mode) == EXPECTED[case_id]
    assert decision.refund_amount == analysis.resolution_recommendation.refund_amount
    assert len(provider.requests) == 5
    assert all(side.status == "COMPLETE" for side in [result.rider, result.driver, result.rebuttals.rider, result.rebuttals.driver])
    assert result.judge.status == ("PENDING_HUMAN_REVIEW" if expected_mode == "HUMAN_REVIEW" else "COMPLETE")
    assert result.judge.validation_issues == []
    assert any(event.event == "JUDGE_OUTPUT_VALIDATED" for event in result.audit)
    assert result.replay_metadata.source == "MOCK_DEMO"
    assert before == (case.model_dump_json(), analysis.model_dump_json())
    trusted = AdvocateContextBuilder().build(case, analysis)
    for request in provider.requests[:2]:
        context = json.loads(request.metadata["context_json"])
        assert context["caseId"] == case_id
        assert context["facts"] == trusted.facts.model_dump(by_alias=True)
    for side in [result.rider, result.driver]:
        assert side.verified_claims
        assert all(p.status in {"VERIFIED", "ARGUMENT_ONLY"} for c in side.verified_claims for p in c.propositions)
    judge_context = json.loads(provider.requests[-1].metadata["context_json"])
    assert "intakeContext" not in judge_context
    for side in [result.rider, result.driver]:
        for rejected in side.rejected_claims:
            assert rejected.claim_id not in [c["claimId"] for c in judge_context[side.side.lower()]["verifiedClaims"]]


@pytest.mark.parametrize("variant", ["fully_explained", "unexplained", "partial", "no_fare_difference", "missing", "conflicting"])
def test_route_policy_edge_cases_without_changing_production_policy(variant):
    service = CaseService(MockCaseRepository())
    case = service._load_case("DISP-005")
    source = case.analysis_input.route_deviation
    expected = ("NO_REFUND", 0)
    if variant in {"unexplained", "partial"}:
        distance = 0 if variant == "unexplained" else 1.6
        conditions = [RouteConditionInput(type="road_closure", explainedDistanceKm=distance, evidenceIds=["E06"])]
        case = case.model_copy(update={"analysis_input": case.analysis_input.model_copy(update={
            "route_deviation": source.model_copy(update={"conditions": conditions})})})
        expected = ("FULL_FARE_DIFFERENCE_REFUND", 4.3) if variant == "unexplained" else ("PARTIAL_REFUND", 2.15)
    if variant == "no_fare_difference":
        case = case.model_copy(update={"fare": case.fare.model_copy(update={"charged": case.fare.quoted})})
    if variant in {"missing", "conflicting"}:
        case = case.model_copy(update={"evidence": [e.model_copy(update={"status": "Missing" if variant == "missing" else "Conflicting"})
            if e.id == "E03" else e for e in case.evidence]})
        expected = ("HUMAN_REVIEW", 0)
    analysis, result, _ = run(case)
    assert (result.deterministic_resolution.recommended_action, result.deterministic_resolution.refund_amount) == expected
    if variant in {"missing", "conflicting"}:
        assert result.deterministic_resolution.resolution_mode == "HUMAN_REVIEW"
        assert result.deterministic_resolution.escalation_reasons


@pytest.mark.parametrize("outside,short_wait", [(False,False), (False,True), (True,False), (True,True)])
def test_no_show_radius_and_wait_have_independent_policy_authority(outside, short_wait):
    case = CaseService(MockCaseRepository())._load_case("DISP-002")
    values = {}
    if outside: values["driver_arrival_coordinates"] = GeoPoint(latitude=1.288, longitude=103.827)
    if short_wait: values["cancellation_timestamp"] = "08:44:00"
    case = case.model_copy(update={"analysis_input": case.analysis_input.model_copy(update={
        "no_show": case.analysis_input.no_show.model_copy(update=values)})})
    analysis, result, _ = run(case)
    refund = outside or short_wait
    assert result.deterministic_resolution.recommended_action == ("REFUND_CANCELLATION_CHARGE" if refund else "UPHOLD_CANCELLATION_CHARGE")
    assert result.deterministic_resolution.refund_amount == (5 if refund else 0)
    assert result.judge.status == "COMPLETE"
    assert result.judge.recommended_outcome == ("REFUND_CANCELLATION_CHARGE" if refund else "UPHOLD_CANCELLATION_CHARGE")
    assert result.judge.validation_issues == []


@pytest.mark.parametrize("required_id", ["E03", "E04", "E05"])
@pytest.mark.parametrize("availability", ["missing", "absent", "available"])
def test_required_route_evidence_availability_controls_existing_review_gate(required_id, availability):
    case = CaseService(MockCaseRepository())._load_case("DISP-005")
    if availability == "missing":
        case = case.model_copy(update={"evidence": [
            evidence.model_copy(update={"status": "Missing"}) if evidence.id == required_id else evidence
            for evidence in case.evidence]})
    elif availability == "absent":
        case = case.model_copy(update={"evidence": [e for e in case.evidence if e.id != required_id]})
    analysis, result, _ = run(case)
    unavailable = availability != "available"
    assert analysis.analysis.missing_evidence_ids == ([required_id] if unavailable else [])
    assert result.deterministic_resolution.resolution_mode == ("HUMAN_REVIEW" if unavailable else "AUTO_RESOLVE")
    assert result.deterministic_resolution.recommended_action == ("HUMAN_REVIEW" if unavailable else "NO_REFUND")
    assert result.deterministic_resolution.refund_amount == 0
    if unavailable:
        assert "MISSING_CRITICAL_EVIDENCE" in result.deterministic_resolution.escalation_reasons
        assert not result.judge.executable
    else:
        assert result.judge.status == "COMPLETE"
        assert result.judge.executable
