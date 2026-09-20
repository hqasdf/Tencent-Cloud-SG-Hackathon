"""Tests for the advocate orchestrator.

Two properties matter most here:

  * failure isolation — one advocate failing must not corrupt the other advocate
    or the deterministic analysis
  * the pipeline must report JUDGE as NOT_RUN, because Milestone 4 does not
    implement the Judge
"""

import json

import pytest

from app.agents.config import AgentSettings
from app.agents.provider import AgentCompletionRequest, LlmCompletion, LlmProviderError
from app.agents.providers.mock import MockLlmProvider
from app.data.cases import MOCK_CASES
from app.services.advocate_orchestrator import AdvocateOrchestratorService
from app.services.dispute_analysis import DisputeAnalysisService

ALL_CASES = ("CASE-2026-1041", "CASE-2026-1042", "CASE-2026-1043", "CASE-2026-1044")


def load(case_id: str):
    case = next(item for item in MOCK_CASES if item.id == case_id)
    return case, DisputeAnalysisService().analyze(case)


def orchestrator(provider=None) -> AdvocateOrchestratorService:
    return AdvocateOrchestratorService(
        settings=AgentSettings(mode="mock", provider="mock"),
        provider=provider or MockLlmProvider(),
    )


class FailOneSideProvider:
    """Fails only for the driver, to prove isolation."""

    name = "fail-driver"

    def __init__(self, fail_side: str) -> None:
        self._fail_side = fail_side
        self._inner = MockLlmProvider()

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        if request.metadata.get("side") == self._fail_side:
            raise LlmProviderError("simulated outage", provider=self.name)
        return self._inner.complete(request)


class ExplodingProvider:
    name = "exploding"

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        raise LlmProviderError("total outage", provider=self.name)


# ---------------------------------------------------------------------------
# Happy path across every fixture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_id", ALL_CASES)
def test_both_advocates_complete_for_every_case(case_id: str) -> None:
    case, analysis = load(case_id)
    result = orchestrator().run(case, analysis)
    assert result.case_id == case_id
    assert result.rider.status == "COMPLETE"
    assert result.driver.status == "COMPLETE"
    assert result.rider.verified_claims
    assert result.driver.verified_claims
    assert result.rider.rejected_claims == []
    assert result.driver.rejected_claims == []


@pytest.mark.parametrize("case_id", ALL_CASES)
def test_pipeline_reports_judge_as_not_run(case_id: str) -> None:
    """Milestone 4 does not implement the Judge."""
    case, analysis = load(case_id)
    result = orchestrator().run(case, analysis)
    stages = {item.stage: item.status for item in result.pipeline}
    assert stages["JUDGE"] == "NOT_RUN"
    assert stages["CASE_RECEIVED"] == "COMPLETE"
    assert stages["EVIDENCE_VALIDATED"] == "COMPLETE"
    assert stages["DETERMINISTIC_ANALYSIS"] == "COMPLETE"
    assert stages["RIDER_ADVOCATE"] == "COMPLETE"
    assert stages["DRIVER_ADVOCATE"] == "COMPLETE"
    assert stages["CLAIM_VERIFICATION"] == "COMPLETE"


def test_every_verified_claim_cites_evidence_and_policy() -> None:
    """The verified set must be clean by construction."""
    for case_id in ALL_CASES:
        case, analysis = load(case_id)
        result = orchestrator().run(case, analysis)
        evidence_ids = {item.id for item in case.evidence}
        for side_result in (result.rider, result.driver):
            for claim in side_result.verified_claims:
                assert claim.evidence_ids, f"{case_id} verified a claim with no evidence"
                assert set(claim.evidence_ids) <= evidence_ids
                assert claim.policy_refs


def test_advocates_are_independent_and_may_disagree() -> None:
    """Rider and Driver receive the same facts but are not forced to agree."""
    case, analysis = load("CASE-2026-1041")
    result = orchestrator().run(case, analysis)
    assert result.rider.requested_outcome == "PARTIAL_REFUND"
    assert result.driver.requested_outcome == "NO_REFUND"
    assert result.rider.requested_outcome != result.driver.requested_outcome


def test_each_side_argues_its_own_case_independently() -> None:
    """Rider output is never fed to the Driver and vice versa."""
    case, analysis = load("CASE-2026-1041")
    result = orchestrator().run(case, analysis)

    rider_claim_ids = {claim.claim_id for claim in result.rider.verified_claims}
    driver_claim_ids = {claim.claim_id for claim in result.driver.verified_claims}
    # Rider uses R*, Driver uses D* — no cross-contamination.
    assert all(cid.startswith("R") for cid in rider_claim_ids)
    assert all(cid.startswith("D") for cid in driver_claim_ids)


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

def test_driver_failure_does_not_break_the_rider() -> None:
    case, analysis = load("CASE-2026-1041")
    result = orchestrator(FailOneSideProvider("DRIVER")).run(case, analysis)

    assert result.rider.status == "COMPLETE"
    assert result.rider.verified_claims
    assert result.driver.status == "FAILED"
    assert result.driver.verified_claims == []

    stages = {item.stage: item.status for item in result.pipeline}
    assert stages["RIDER_ADVOCATE"] == "COMPLETE"
    assert stages["DRIVER_ADVOCATE"] == "FAILED"
    # The deterministic stages are untouched.
    assert stages["DETERMINISTIC_ANALYSIS"] == "COMPLETE"
    assert stages["EVIDENCE_VALIDATED"] == "COMPLETE"


def test_rider_failure_does_not_break_the_driver() -> None:
    case, analysis = load("CASE-2026-1043")
    result = orchestrator(FailOneSideProvider("RIDER")).run(case, analysis)

    assert result.rider.status == "FAILED"
    assert result.driver.status == "COMPLETE"
    assert result.driver.verified_claims


def test_total_provider_failure_degrades_but_does_not_raise() -> None:
    """The case must stay readable when the provider is completely down."""
    case, analysis = load("CASE-2026-1041")
    result = orchestrator(ExplodingProvider()).run(case, analysis)

    assert result.rider.status == "FAILED"
    assert result.driver.status == "FAILED"
    assert result.rider.verified_claims == []
    assert result.driver.verified_claims == []
    # Structured failure reason on both sides, and verification marked failed.
    assert result.rider.failure_reason
    assert result.driver.failure_reason
    stages = {item.stage: item.status for item in result.pipeline}
    assert stages["CLAIM_VERIFICATION"] == "FAILED"
    # The analysis the caller passed in is still intact and unchanged.
    assert analysis.policy_evaluation is not None


def test_failure_reason_never_leaks_credentials_or_internals() -> None:
    case, analysis = load("CASE-2026-1041")
    result = orchestrator(ExplodingProvider()).run(case, analysis)

    for side_result in (result.rider, result.driver):
        reason = (side_result.failure_reason or "").lower()
        assert "bearer" not in reason
        assert "authorization" not in reason
        assert "api_key" not in reason
        assert "apikey" not in reason
        assert "traceback" not in reason
        assert "sk-" not in reason


def test_verification_summary_totals_match_the_side_results() -> None:
    case, analysis = load("CASE-2026-1041")
    result = orchestrator().run(case, analysis)
    expected_verified = len(result.rider.verified_claims) + len(result.driver.verified_claims)
    expected_rejected = len(result.rider.rejected_claims) + len(result.driver.rejected_claims)
    assert result.verification_summary.verified_count == expected_verified
    assert result.verification_summary.rejected_count == expected_rejected


# ---------------------------------------------------------------------------
# The orchestrator never mutates deterministic data
# ---------------------------------------------------------------------------

def test_orchestrator_does_not_mutate_the_case_or_analysis() -> None:
    case, analysis = load("CASE-2026-1041")
    case_before = case.model_dump_json(by_alias=True)
    analysis_before = analysis.model_dump_json(by_alias=True)

    orchestrator().run(case, analysis)

    assert case.model_dump_json(by_alias=True) == case_before
    assert analysis.model_dump_json(by_alias=True) == analysis_before


def test_run_metadata_describes_mock_mode_without_model_or_secrets() -> None:
    case, analysis = load("CASE-2026-1041")
    result = orchestrator().run(case, analysis)
    assert result.agent_run.mode == "mock"
    assert result.agent_run.provider == "mock"
    assert result.agent_run.model is None  # no model in mock mode
    assert result.agent_run.prompt_version
    assert result.agent_run.duration_ms >= 0


def test_response_serialises_with_camel_case_keys() -> None:
    case, analysis = load("CASE-2026-1041")
    serialized = orchestrator().run(case, analysis).model_dump_json(by_alias=True)
    payload = json.loads(serialized)

    # Top-level contract.
    assert "caseId" in payload
    assert "disputeType" in payload
    assert "verificationSummary" in payload
    assert "pipeline" in payload
    assert "agentRun" in payload

    # Nested per-side contract.
    for side in ("rider", "driver"):
        assert "verifiedClaims" in payload[side]
        assert "rejectedClaims" in payload[side]
        assert "requestedOutcome" in payload[side]
        assert "contextAcknowledged" in payload[side]

    assert "verified_claims" not in serialized
    assert "case_id" not in serialized


def test_orchestrator_is_deterministic_across_runs() -> None:
    case, analysis = load("CASE-2026-1041")
    first = orchestrator().run(case, analysis)
    second = orchestrator().run(case, analysis)
    assert first.rider.model_dump() == second.rider.model_dump()
    assert first.driver.model_dump() == second.driver.model_dump()
