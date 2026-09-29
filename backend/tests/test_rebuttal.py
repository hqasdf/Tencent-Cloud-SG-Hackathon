"""Stage 6 bounded-rebuttal tests — entirely offline.

No live Gemini call and no live Ollama call is made anywhere in this file. Three
layers keep that true:

1. ``conftest.py`` forces ``AGENT_MODE=mock`` for the session.
2. Every test injects its own provider through the orchestrator's ``provider``
   seam, so no test can fall back to the environment.
3. The stub is a transport stub only. Prompt assembly, JSON parsing, Pydantic
   validation, claim verification, rebuttal validation and Judge validation are
   all the real production classes.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.judge_context_builder import JudgeContextBuilder
from app.agents.judge_validation import (
    DUPLICATE_REBUTTAL_ID,
    JudgeOutputValidationService,
    REBUTTAL_ID_BELONGS_TO_OTHER_SIDE,
    UNKNOWN_REBUTTAL_ID,
)
from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProviderError,
    ProviderErrorCode,
)
from app.agents.providers.mock import MockLlmProvider
from app.agents.rebuttal_context_builder import RebuttalContextBuilder
from app.agents.rebuttal_validation import (
    CONCESSION_CONTRADICTS_STANCE,
    CONCESSION_NOT_A_TARGET,
    DUPLICATE_TARGET_CLAIM,
    EMPTY_REASONING_SUMMARY,
    EVIDENCE_ID_NOT_FOUND,
    FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS,
    INVENTED_MONETARY_VALUE,
    POLICY_REF_NOT_FOUND,
    RebuttalValidationService,
    TARGET_CLAIM_IS_OWN_SIDE,
    TARGET_CLAIM_NOT_FOUND,
    UNKNOWN_ASSERTED_FACT,
)
from app.benchmarks.cases import case_by_id
from app.main import app
from app.models.rebuttal import (
    MAX_REBUTTAL_ROUNDS,
    RebuttalCaseContext,
    RebuttalOutput,
)
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

ROUTE_CASE = "DISP-005"
NO_SHOW_CASE = "DISP-002"
HUMAN_REVIEW_CASE = "DISP-003"

MOCK_SETTINGS = AgentSettings(mode="mock", provider="mock")


# ---------------------------------------------------------------------------
# Stub transport
# ---------------------------------------------------------------------------


class _StubProvider:
    """Transport stub with per-role scripting.

    Advocate calls are delegated to the real mock provider, so the advocate half
    of every test is exercised by production code. Rebuttal and Judge calls are
    scripted, because those are the layers under test.
    """

    name = "stub"

    def __init__(
        self,
        *,
        rebuttal_payload_factory=None,
        rebuttal_raw: str | None = None,
        rebuttal_error: Exception | None = None,
        fail_rebuttal_side: str | None = None,
        judge_payload_factory=None,
        judge_raw: str | None = None,
        judge_error: Exception | None = None,
        fail_advocate_side: str | None = None,
        extra_driver_claim: bool = False,
    ) -> None:
        self._rebuttal_payload_factory = rebuttal_payload_factory
        self._rebuttal_raw = rebuttal_raw
        self._rebuttal_error = rebuttal_error
        self._fail_rebuttal_side = fail_rebuttal_side
        self._judge_payload_factory = judge_payload_factory
        self._judge_raw = judge_raw
        self._judge_error = judge_error
        self._fail_advocate_side = fail_advocate_side
        self._extra_driver_claim = extra_driver_claim
        self._mock = MockLlmProvider()
        self.requests: list[AgentCompletionRequest] = []

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.requests.append(request)
        role = request.metadata.get("role")

        if role == "rebuttal":
            return self._rebuttal(request)
        if role == "judge":
            return self._judge(request)
        return self._advocate(request)

    # -- roles ------------------------------------------------------------

    def _advocate(self, request: AgentCompletionRequest) -> LlmCompletion:
        side = request.metadata.get("side")
        if self._fail_advocate_side and side == self._fail_advocate_side:
            raise LlmProviderError(
                "stub advocate outage",
                provider=self.name,
                code=ProviderErrorCode.SERVER_ERROR,
            )
        base = self._mock.complete(request)
        if not self._extra_driver_claim or side != "DRIVER":
            return base
        # Append a claim that cannot pass verification: E99 does not exist.
        payload = json.loads(base.raw_text)
        payload["claims"].append(
            {
                "claimId": "D9",
                "claim": "The driver's GPS log at E99 confirms arrival at the pickup point.",
                "evidenceIds": ["E99"],
                "policyRefs": [request.metadata.get("policy_probe", "NO_SHOW_WAIT_TIME")],
                "reasoningSummary": "Rests on evidence that does not exist.",
                "importance": "LOW",
                "assertedFacts": [],
                "disputedEvidenceIds": [],
            }
        )
        return _completion(json.dumps(payload))

    def _rebuttal(self, request: AgentCompletionRequest) -> LlmCompletion:
        side = request.metadata.get("side")
        if self._fail_rebuttal_side and side == self._fail_rebuttal_side:
            raise LlmProviderError(
                "stub rebuttal outage",
                provider=self.name,
                code=ProviderErrorCode.SERVER_ERROR,
            )
        if self._rebuttal_error is not None:
            raise self._rebuttal_error
        if self._rebuttal_raw is not None:
            return _completion(self._rebuttal_raw)
        context = RebuttalCaseContext.model_validate_json(
            request.metadata["context_json"]
        )
        payload = (
            self._rebuttal_payload_factory(context)
            if self._rebuttal_payload_factory is not None
            else _valid_rebuttal(context)
        )
        return _completion(json.dumps(payload))

    def _judge(self, request: AgentCompletionRequest) -> LlmCompletion:
        if self._judge_error is not None:
            raise self._judge_error
        if self._judge_raw is not None:
            return _completion(self._judge_raw)
        from app.models.judge import JudgeCaseContext

        context = JudgeCaseContext.model_validate_json(request.metadata["context_json"])
        payload = (
            self._judge_payload_factory(context)
            if self._judge_payload_factory is not None
            else _valid_judge(context)
        )
        return _completion(json.dumps(payload))

    # -- inspection -------------------------------------------------------

    @property
    def rebuttal_requests(self) -> list[AgentCompletionRequest]:
        return [r for r in self.requests if r.metadata.get("role") == "rebuttal"]

    @property
    def judge_requests(self) -> list[AgentCompletionRequest]:
        return [r for r in self.requests if r.metadata.get("role") == "judge"]

    @property
    def advocate_requests(self) -> list[AgentCompletionRequest]:
        return [r for r in self.requests if r.metadata.get("role") is None]


def _completion(raw_text: str) -> LlmCompletion:
    return LlmCompletion(
        raw_text=raw_text,
        provider_name="stub",
        model_name="stub-model",
        duration_ms=5,
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
    )


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _valid_rebuttal(context: RebuttalCaseContext) -> dict:
    """A rebuttal that passes every validation rule for this context.

    The first opposing claim is challenged and the rest conceded, so both the
    challenge and concession paths are exercised by the default payload.
    """
    rule_ids = [rule.rule_id for rule in context.applicable_policy.rules]
    responses = []
    concessions = []
    for index, claim in enumerate(context.opposing_verified_claims):
        stance = "CHALLENGE" if index == 0 else "CONCEDE"
        if stance != "CHALLENGE":
            concessions.append(claim.claim_id)
        responses.append(
            {
                "targetClaimId": claim.claim_id,
                "stance": stance,
                "reasoningSummary": "Grounded in the supplied facts and policy.",
                "evidenceIds": list(claim.evidence_ids),
                "policyRuleIds": rule_ids[:1],
                "assertedFacts": [],
            }
        )
    return {
        "side": context.own_side,
        "responses": responses,
        "concessions": concessions,
        "overallSummary": "Responded to the opposing verified claims.",
    }


def _valid_judge(context) -> dict:
    outcome = sorted(context.allowed_outcomes)[0]
    pending = context.resolution_mode == "HUMAN_REVIEW"
    return {
        "status": "PENDING_HUMAN_REVIEW" if pending else "COMPLETE",
        "recommendedOutcome": outcome,
        "acceptedRiderClaimIds": [c.claim_id for c in context.rider.verified_claims],
        "acceptedDriverClaimIds": [c.claim_id for c in context.driver.verified_claims],
        "rejectedRiderClaimIds": [],
        "rejectedDriverClaimIds": [],
        "consideredRiderRebuttalIds": [
            item.rebuttal_id for item in context.verified_rider_rebuttals
        ],
        "consideredDriverRebuttalIds": [
            item.rebuttal_id for item in context.verified_driver_rebuttals
        ],
        "reasoningSummary": "Grounded in the supplied facts and policy.",
        "evidenceIds": [item.id for item in context.evidence],
        "policyRuleIds": [rule.rule_id for rule in context.applicable_policy.rules],
        "uncertainties": [],
        "requiresHumanReview": pending,
    }


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _load(case_id: str):
    case = case_by_id(case_id)
    return case, DisputeAnalysisService().analyze(case)


def _advocate_run(case, analysis, provider=None):
    from app.services.advocate_orchestrator import AdvocateOrchestratorService

    return AdvocateOrchestratorService(
        settings=MOCK_SETTINGS, provider=provider or MockLlmProvider()
    ).run(case, analysis)


def _rebuttal_context(case_id: str, side: str, provider=None):
    """Build one side's rebuttal context, plus the pieces it was built from."""
    case, analysis = _load(case_id)
    advocate_context = AdvocateContextBuilder().build(case, analysis)
    advocate_run = _advocate_run(case, analysis, provider)
    context = RebuttalContextBuilder().build(
        case, analysis, advocate_context, advocate_run.rider, advocate_run.driver, side
    )
    return context, advocate_context, advocate_run


def _run(case_id: str, provider: _StubProvider):
    case, analysis = _load(case_id)
    service = ResolutionOrchestratorService(settings=MOCK_SETTINGS, provider=provider)
    return service.run(case, analysis)


def _validate(context: RebuttalCaseContext, payload: dict):
    """Validate a raw payload through the real service."""
    return RebuttalValidationService().validate(
        RebuttalOutput.model_validate(payload), context
    )


def _single_response_payload(context: RebuttalCaseContext, **overrides) -> dict:
    """A payload with exactly one response, targeting the first opposing claim."""
    target = context.opposing_verified_claims[0].claim_id
    response = {
        "targetClaimId": target,
        "stance": "CHALLENGE",
        "reasoningSummary": "Grounded in the supplied facts and policy.",
        "evidenceIds": [],
        "policyRuleIds": [],
        "assertedFacts": [],
    }
    response.update(overrides)
    return {
        "side": context.own_side,
        "responses": [response],
        "concessions": [],
        "overallSummary": "One response.",
    }


# ---------------------------------------------------------------------------
# 1-3. What each rebuttal may see
# ---------------------------------------------------------------------------


def test_rider_sees_only_verified_driver_claims() -> None:
    context, _, advocate_run = _rebuttal_context(ROUTE_CASE, "RIDER")

    assert context.own_side == "RIDER"
    opposing = {claim.claim_id for claim in context.opposing_verified_claims}
    expected = {f"DRIVER-{c.claim_id}" for c in advocate_run.driver.verified_claims}
    assert opposing == expected
    # Every opposing entry is a verified claim, by construction.
    assert all(claim.claim_id.startswith("DRIVER-") for claim in context.opposing_verified_claims)


def test_driver_sees_only_verified_rider_claims() -> None:
    context, _, advocate_run = _rebuttal_context(ROUTE_CASE, "DRIVER")

    assert context.own_side == "DRIVER"
    opposing = {claim.claim_id for claim in context.opposing_verified_claims}
    expected = {f"RIDER-{c.claim_id}" for c in advocate_run.rider.verified_claims}
    assert opposing == expected
    assert all(claim.claim_id.startswith("RIDER-") for claim in context.opposing_verified_claims)


def test_rejected_initial_claims_never_enter_rebuttal_context() -> None:
    """A claim that failed verification is not a rebuttal target and is invisible."""
    provider = _StubProvider(extra_driver_claim=True)
    case, analysis = _load(NO_SHOW_CASE)
    advocate_context = AdvocateContextBuilder().build(case, analysis)
    advocate_run = _advocate_run(case, analysis, provider)

    # The dirty claim was produced and rejected.
    rejected_ids = {claim.claim_id for claim in advocate_run.driver.rejected_claims}
    assert "D9" in rejected_ids

    rider_context = RebuttalContextBuilder().build(
        case, analysis, advocate_context, advocate_run.rider, advocate_run.driver, "RIDER"
    )
    opposing_ids = {claim.claim_id for claim in rider_context.opposing_verified_claims}

    assert "DRIVER-D9" not in opposing_ids
    # And the rejected claim's text is nowhere in the serialized context.
    serialized = rider_context.model_dump_json(by_alias=True)
    assert "E99" not in serialized
    assert "GPS log at E99" not in serialized

    # Targeting it is rejected, exactly as an invented ID would be.
    outcome = _validate(rider_context, _single_response_payload(rider_context, targetClaimId="DRIVER-D9"))
    assert [item.reason for item in outcome.rejected_rebuttals] == [TARGET_CLAIM_NOT_FOUND]
    assert outcome.verified_rebuttals == []


# ---------------------------------------------------------------------------
# 4-6. Target discipline
# ---------------------------------------------------------------------------


def test_unknown_target_claim_is_rejected() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(context, _single_response_payload(context, targetClaimId="DRIVER-D99"))

    assert [item.reason for item in outcome.rejected_rebuttals] == [TARGET_CLAIM_NOT_FOUND]
    assert outcome.verified_rebuttals == []


def test_own_side_target_is_rejected() -> None:
    """Rider cannot rebut RIDER-C1; Driver cannot rebut DRIVER-C1."""
    rider_context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    own_claim_id = rider_context.own_verified_claims[0].claim_id
    assert own_claim_id.startswith("RIDER-")

    outcome = _validate(rider_context, _single_response_payload(rider_context, targetClaimId=own_claim_id))
    assert [item.reason for item in outcome.rejected_rebuttals] == [TARGET_CLAIM_IS_OWN_SIDE]

    driver_context, _, _ = _rebuttal_context(ROUTE_CASE, "DRIVER")
    own_driver_claim = driver_context.own_verified_claims[0].claim_id
    assert own_driver_claim.startswith("DRIVER-")
    outcome = _validate(
        driver_context, _single_response_payload(driver_context, targetClaimId=own_driver_claim)
    )
    assert [item.reason for item in outcome.rejected_rebuttals] == [TARGET_CLAIM_IS_OWN_SIDE]


def test_the_same_claim_cannot_be_targeted_twice() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    target = context.opposing_verified_claims[0].claim_id
    response = {
        "targetClaimId": target,
        "stance": "CHALLENGE",
        "reasoningSummary": "First pass.",
        "evidenceIds": [],
        "policyRuleIds": [],
        "assertedFacts": [],
    }
    payload = {
        "side": "RIDER",
        "responses": [response, {**response, "reasoningSummary": "Second pass."}],
        "concessions": [],
        "overallSummary": "Twice.",
    }
    outcome = _validate(context, payload)
    assert [item.reason for item in outcome.rejected_rebuttals] == [DUPLICATE_TARGET_CLAIM]
    assert len(outcome.verified_rebuttals) == 1


# ---------------------------------------------------------------------------
# 7-10. Evidence, policy and fact discipline
# ---------------------------------------------------------------------------


def test_unknown_evidence_id_is_rejected() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(context, _single_response_payload(context, evidenceIds=["E99"]))
    assert EVIDENCE_ID_NOT_FOUND in [item.reason for item in outcome.rejected_rebuttals]


def test_unknown_policy_rule_is_rejected() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(context, _single_response_payload(context, policyRuleIds=["FAIRNESS_RULE_V1"]))
    assert POLICY_REF_NOT_FOUND in [item.reason for item in outcome.rejected_rebuttals]


def test_wrong_asserted_deterministic_fact_is_rejected() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(
        context,
        _single_response_payload(
            context,
            assertedFacts=[{"fact": "UNEXPLAINED_DEVIATION_KM", "value": 999.0}],
        ),
    )
    assert FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS in [
        item.reason for item in outcome.rejected_rebuttals
    ]


def test_correct_asserted_fact_is_accepted() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    true_value = context.deterministic_facts.unexplained_deviation_distance_km
    outcome = _validate(
        context,
        _single_response_payload(
            context,
            assertedFacts=[{"fact": "UNEXPLAINED_DEVIATION_KM", "value": true_value}],
        ),
    )
    assert outcome.verified_rebuttals, [item.reason for item in outcome.rejected_rebuttals]


def test_wrong_domain_fact_is_rejected() -> None:
    """A no-show fact asserted on a route case is unknown, not merely wrong."""
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(
        context,
        _single_response_payload(
            context,
            assertedFacts=[{"fact": "WAITING_DURATION_SECONDS", "value": 480}],
        ),
    )
    assert UNKNOWN_ASSERTED_FACT in [item.reason for item in outcome.rejected_rebuttals]


def test_no_show_facts_are_verifiable_on_a_no_show_case() -> None:
    """The same fact vocabulary works on the other dispute type.

    Values are read from the context rather than hardcoded: a fixture change
    should not turn this into a failing test about a number.
    """
    context, _, _ = _rebuttal_context(NO_SHOW_CASE, "RIDER")
    facts = context.deterministic_facts
    outcome = _validate(
        context,
        _single_response_payload(
            context,
            assertedFacts=[
                {"fact": "WAITING_DURATION_SECONDS", "value": facts.waiting_duration_seconds},
                {
                    "fact": "DRIVER_PICKUP_DISTANCE_METERS",
                    "value": facts.driver_distance_to_pickup_meters,
                },
            ],
        ),
    )
    assert outcome.verified_rebuttals, [item.reason for item in outcome.rejected_rebuttals]


def test_invented_monetary_amount_in_prose_is_rejected() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(
        context,
        _single_response_payload(
            context, reasoningSummary="The rider should be refunded SGD 250.00 in full."
        ),
    )
    assert INVENTED_MONETARY_VALUE in [item.reason for item in outcome.rejected_rebuttals]


def test_empty_reasoning_summary_is_rejected() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(context, _single_response_payload(context, reasoningSummary="   "))
    assert EMPTY_REASONING_SUMMARY in [item.reason for item in outcome.rejected_rebuttals]


# ---------------------------------------------------------------------------
# Concessions
# ---------------------------------------------------------------------------


def test_valid_rebuttal_is_verified() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(context, _valid_rebuttal(context))

    assert outcome.verified_rebuttals
    assert outcome.rejected_rebuttals == []
    assert all(item.verification_status == "VERIFIED" for item in outcome.verified_rebuttals)
    # IDs are assigned by code, namespaced, and dense from 1.
    ids = [item.rebuttal_id for item in outcome.verified_rebuttals]
    assert ids == [f"RIDER-RB{i}" for i in range(1, len(ids) + 1)]


def test_concession_must_match_the_stance_taken() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    target = context.opposing_verified_claims[0].claim_id
    payload = _single_response_payload(context, stance="CHALLENGE")
    payload["concessions"] = [target]

    outcome = _validate(context, payload)
    assert [item.reason for item in outcome.rejected_rebuttals] == [CONCESSION_CONTRADICTS_STANCE]
    # The response itself verified; only the concession claim is rejected.
    assert len(outcome.verified_rebuttals) == 1


def test_concession_must_name_a_target() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    payload = _single_response_payload(context)
    payload["concessions"] = ["DRIVER-D99"]

    outcome = _validate(context, payload)
    assert [item.reason for item in outcome.rejected_rebuttals] == [CONCESSION_NOT_A_TARGET]


def test_conceded_target_ids_are_derived_from_stances() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    outcome = _validate(context, _valid_rebuttal(context))

    expected = {
        item.target_claim_id
        for item in outcome.verified_rebuttals
        if item.stance in ("CONCEDE", "PARTIALLY_CONCEDE")
    }
    assert set(outcome.conceded_target_ids()) == expected


# ---------------------------------------------------------------------------
# One-round enforcement
# ---------------------------------------------------------------------------


def test_exactly_one_rebuttal_round_is_configured() -> None:
    assert MAX_REBUTTAL_ROUNDS == 1


def test_rebuttal_context_advertises_the_single_round() -> None:
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    assert context.rebuttal_round == 1
    assert context.max_rebuttal_rounds == 1
    assert context.allowed_stances == ["CHALLENGE", "CONCEDE", "PARTIALLY_CONCEDE"]


def test_rebuttal_context_cannot_name_a_rebuttal_as_a_target() -> None:
    """A second round is unrepresentable, not merely disabled.

    The context carries only initial verified claims, so there is no field a
    rebuttal-to-a-rebuttal could be expressed with.
    """
    context, _, _ = _rebuttal_context(ROUTE_CASE, "RIDER")
    fields = set(RebuttalCaseContext.model_fields)

    assert "opposing_rebuttals" not in fields
    assert "own_rebuttals" not in fields
    assert "prior_rounds" not in fields
    # Every target available to this side is an initial claim ID.
    assert all(
        claim.claim_id.startswith("DRIVER-") and "RB" not in claim.claim_id
        for claim in context.opposing_verified_claims
    )


def test_only_one_round_of_rebuttal_requests_is_made() -> None:
    provider = _StubProvider()
    _run(ROUTE_CASE, provider)
    # Two rebuttal calls: one per side. Never four.
    assert len(provider.rebuttal_requests) == 2
    assert {r.metadata["side"] for r in provider.rebuttal_requests} == {"RIDER", "DRIVER"}


# ---------------------------------------------------------------------------
# 12-15, 30-32. Failure behaviour
# ---------------------------------------------------------------------------


def test_zero_verified_rebuttals_still_allows_the_judge() -> None:
    """Every response rejected: the Judge still runs on the verified claims."""
    provider = _StubProvider(
        rebuttal_payload_factory=lambda context: {
            "side": context.own_side,
            "responses": [
                {
                    "targetClaimId": "DRIVER-D99" if context.own_side == "RIDER" else "RIDER-D99",
                    "stance": "CHALLENGE",
                    "reasoningSummary": "Targets a claim that does not exist.",
                    "evidenceIds": [],
                    "policyRuleIds": [],
                    "assertedFacts": [],
                }
            ],
            "concessions": [],
            "overallSummary": "Nothing survived verification.",
        }
    )
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.verification_summary.verified_count == 0
    assert result.rebuttals.verification_summary.rejected_count == 2
    # The Judge ran and had verified claims to work with.
    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")
    assert len(provider.judge_requests) == 1


def test_rider_rebuttal_failure_still_allows_the_judge() -> None:
    provider = _StubProvider(fail_rebuttal_side="RIDER")
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.rider.status == "FAILED"
    assert result.rebuttals.driver.status == "COMPLETE"
    assert result.rebuttals.rider.failure_reason
    assert result.rebuttals.rider.verified_rebuttals == []
    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")
    assert len(provider.judge_requests) == 1


def test_driver_rebuttal_failure_still_allows_the_judge() -> None:
    provider = _StubProvider(fail_rebuttal_side="DRIVER")
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.driver.status == "FAILED"
    assert result.rebuttals.rider.status == "COMPLETE"
    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")


def test_both_rebuttals_failing_still_allows_the_judge() -> None:
    """The Judge falls back to the initial verified claims."""
    provider = _StubProvider(rebuttal_error=LlmProviderError(
        "stub outage", provider="stub", code=ProviderErrorCode.SERVER_ERROR
    ))
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.rider.status == "FAILED"
    assert result.rebuttals.driver.status == "FAILED"
    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")
    # The initial arguments are intact and the Judge saw them.
    assert result.rider.verified_claims
    assert len(provider.judge_requests) == 1
    judge_context = json.loads(provider.judge_requests[0].metadata["context_json"])
    assert judge_context["rider"]["verifiedClaims"]
    assert judge_context["verifiedRiderRebuttals"] == []


def test_initial_rider_failure_blocks_rebuttals_and_judge() -> None:
    provider = _StubProvider(fail_advocate_side="RIDER")
    result = _run(ROUTE_CASE, provider)

    assert result.rider.status == "FAILED"
    assert result.rebuttals.rider.status == "NOT_RUN"
    assert result.rebuttals.driver.status == "NOT_RUN"
    assert result.judge.status == "NOT_RUN"
    assert result.judge.skip_reason == "ADVOCATE_INPUT_INCOMPLETE"
    # Not a single rebuttal or Judge call was spent.
    assert provider.rebuttal_requests == []
    assert provider.judge_requests == []


def test_initial_driver_failure_blocks_rebuttals_and_judge() -> None:
    provider = _StubProvider(fail_advocate_side="DRIVER")
    result = _run(ROUTE_CASE, provider)

    assert result.driver.status == "FAILED"
    assert result.rebuttals.rider.status == "NOT_RUN"
    assert result.rebuttals.driver.status == "NOT_RUN"
    assert result.judge.status == "NOT_RUN"
    assert provider.rebuttal_requests == []
    assert provider.judge_requests == []


def test_malformed_rebuttal_json_fails_safely() -> None:
    provider = _StubProvider(rebuttal_raw="{not json at all")
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.rider.status == "FAILED"
    assert result.rebuttals.driver.status == "FAILED"
    assert "MALFORMED_JSON" in (result.rebuttals.rider.failure_reason or "")
    # A rebuttal failure never corrupts the deterministic layer.
    assert result.deterministic_resolution.recommended_action
    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")


def test_provider_429_on_rebuttal_fails_safely() -> None:
    provider = _StubProvider(
        rebuttal_error=LlmProviderError(
            "rate limited", provider="stub", code=ProviderErrorCode.RATE_LIMITED
        )
    )
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.rider.status == "FAILED"
    assert "RATE_LIMITED" in (result.rebuttals.rider.failure_reason or "")
    assert result.deterministic_resolution.recommended_action


def test_provider_503_on_rebuttal_fails_safely() -> None:
    provider = _StubProvider(
        rebuttal_error=LlmProviderError(
            "server error", provider="stub", code=ProviderErrorCode.SERVER_ERROR
        )
    )
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.rider.status == "FAILED"
    assert "SERVER_ERROR" in (result.rebuttals.rider.failure_reason or "")
    assert result.deterministic_resolution.recommended_action


def test_rebuttal_failure_never_leaks_the_credential() -> None:
    provider = _StubProvider(
        rebuttal_error=LlmProviderError(
            "Bearer sk-supersecret-abcdef123456",
            provider="stub",
            code=ProviderErrorCode.AUTHENTICATION_FAILED,
        )
    )
    result = _run(ROUTE_CASE, provider)
    serialized = result.model_dump_json(by_alias=True)
    assert "sk-supersecret" not in serialized


# ---------------------------------------------------------------------------
# 18-20. Judge context and Judge references
# ---------------------------------------------------------------------------


def test_rejected_rebuttal_never_enters_judge_context() -> None:
    """A rebuttal that fails verification is absent from the Judge's prompt."""
    provider = _StubProvider(
        rebuttal_payload_factory=lambda context: {
            "side": context.own_side,
            "responses": [
                {
                    "targetClaimId": context.opposing_verified_claims[0].claim_id,
                    "stance": "CHALLENGE",
                    "reasoningSummary": "This cites evidence E99 which proves my point.",
                    "evidenceIds": ["E99"],
                    "policyRuleIds": [],
                    "assertedFacts": [],
                }
            ],
            "concessions": [],
            "overallSummary": "Rests on invented evidence.",
        }
    )
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.verification_summary.verified_count == 0
    assert result.rebuttals.verification_summary.rejected_count == 2
    assert result.rebuttals.rider.rejected_rebuttals

    prompt = provider.judge_requests[0].metadata["context_json"]
    assert "E99" not in prompt
    assert "proves my point" not in prompt
    assert "rejectedRebuttals" not in prompt

    judge_context = json.loads(prompt)
    assert judge_context["verifiedRiderRebuttals"] == []
    assert judge_context["verifiedDriverRebuttals"] == []


def test_judge_context_contains_verified_rebuttals() -> None:
    provider = _StubProvider()
    _run(ROUTE_CASE, provider)

    judge_context = json.loads(provider.judge_requests[0].metadata["context_json"])
    assert judge_context["verifiedRiderRebuttals"]
    assert judge_context["verifiedDriverRebuttals"]
    assert all(
        item["verificationStatus"] == "VERIFIED"
        for item in judge_context["verifiedRiderRebuttals"]
    )


def test_judge_can_reference_a_verified_rebuttal() -> None:
    provider = _StubProvider()
    result = _run(ROUTE_CASE, provider)

    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")
    assert result.judge.considered_rider_rebuttal_ids
    assert result.judge.considered_driver_rebuttal_ids
    assert result.judge.validation_issues == []


def test_judge_cannot_reference_a_rejected_rebuttal() -> None:
    """A rebuttal that failed verification is not addressable by the Judge."""
    provider = _StubProvider(
        judge_payload_factory=lambda context: {
            **_valid_judge(context),
            "consideredRiderRebuttalIds": ["RIDER-RB99"],
        }
    )
    result = _run(ROUTE_CASE, provider)

    assert result.judge.status == "FAILED"
    codes = [issue.code for issue in result.judge.validation_issues]
    assert UNKNOWN_REBUTTAL_ID in codes


def test_judge_cannot_cite_a_rebuttal_from_the_other_side() -> None:
    provider = _StubProvider(
        judge_payload_factory=lambda context: {
            **_valid_judge(context),
            "consideredRiderRebuttalIds": ["DRIVER-RB1"],
        }
    )
    result = _run(ROUTE_CASE, provider)

    assert result.judge.status == "FAILED"
    codes = [issue.code for issue in result.judge.validation_issues]
    assert REBUTTAL_ID_BELONGS_TO_OTHER_SIDE in codes


def test_judge_cannot_cite_the_same_rebuttal_twice() -> None:
    provider = _StubProvider(
        judge_payload_factory=lambda context: {
            **_valid_judge(context),
            "consideredRiderRebuttalIds": ["RIDER-RB1", "RIDER-RB1"],
        }
    )
    result = _run(ROUTE_CASE, provider)

    assert result.judge.status == "FAILED"
    codes = [issue.code for issue in result.judge.validation_issues]
    assert DUPLICATE_REBUTTAL_ID in codes


def test_judge_may_cite_no_rebuttal_at_all() -> None:
    """An empty rebuttal list is valid: a Judge need not rely on cross-examination."""
    provider = _StubProvider(
        judge_payload_factory=lambda context: {
            **_valid_judge(context),
            "consideredRiderRebuttalIds": [],
            "consideredDriverRebuttalIds": [],
        }
    )
    result = _run(ROUTE_CASE, provider)

    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")
    assert result.judge.validation_issues == []


def test_judge_validation_rejects_a_rebuttal_reference_with_no_verified_rebuttals() -> None:
    """Direct service test: with no rebuttal layer at all, any reference is unknown."""
    from app.models.judge import JudgeOutput

    case, analysis = _load(ROUTE_CASE)
    advocate_context = AdvocateContextBuilder().build(case, analysis)
    advocate_run = _advocate_run(case, analysis)

    # rebuttal_run=None models a Stage 5 replay: the Judge runs without a
    # cross-examination layer, so no rebuttal ID can be valid.
    judge_context = JudgeContextBuilder().build(
        case,
        analysis,
        advocate_context,
        advocate_run.rider,
        advocate_run.driver,
        None,
    )
    assert judge_context.verified_rider_rebuttals == []
    assert judge_context.verified_driver_rebuttals == []

    output = JudgeOutput.model_validate(
        {**_valid_judge(judge_context), "consideredRiderRebuttalIds": ["RIDER-RB1"]}
    )
    result = JudgeOutputValidationService().validate(output, judge_context)

    assert not result.valid
    assert UNKNOWN_REBUTTAL_ID in [issue.code for issue in result.issues]


# ---------------------------------------------------------------------------
# 21-24. Determinism preserved
# ---------------------------------------------------------------------------


def test_human_review_is_unchanged_by_rebuttals() -> None:
    provider = _StubProvider()
    result = _run(HUMAN_REVIEW_CASE, provider)

    assert result.deterministic_resolution.resolution_mode == "HUMAN_REVIEW"
    assert result.deterministic_resolution.recommended_action == "HUMAN_REVIEW"
    assert result.judge.status == "PENDING_HUMAN_REVIEW"
    assert result.judge.executable is False
    assert result.judge.requires_human_review is True
    # Rebuttals ran, and changed nothing.
    assert result.rebuttals.rider.status in ("COMPLETE", "FAILED")


def test_refund_confidence_and_escalation_are_unchanged() -> None:
    """Byte-identical to the deterministic analysis, with rebuttals in the mix."""
    for case_id in (ROUTE_CASE, NO_SHOW_CASE, HUMAN_REVIEW_CASE):
        case, analysis = _load(case_id)
        result = _run(case_id, _StubProvider())

        assert result.deterministic_resolution.refund_amount == (
            analysis.resolution_recommendation.refund_amount
        )
        assert result.deterministic_resolution.confidence == (
            analysis.confidence.overall_confidence
        )
        assert result.deterministic_resolution.escalation_reasons == list(
            analysis.escalation_reasons
        )
        assert result.deterministic_resolution.recommended_action == (
            analysis.resolution_recommendation.recommended_action
        )
        assert result.deterministic_resolution.resolution_mode == analysis.resolution_mode


def test_rebuttal_prose_cannot_move_the_deterministic_result() -> None:
    """A rebuttal asserting a refund figure is rejected and changes nothing."""
    baseline = _run(ROUTE_CASE, _StubProvider())

    provider = _StubProvider(
        rebuttal_payload_factory=lambda context: {
            "side": context.own_side,
            "responses": [
                {
                    "targetClaimId": context.opposing_verified_claims[0].claim_id,
                    "stance": "CHALLENGE",
                    "reasoningSummary": "The correct refund is SGD 9999.00 and must be paid.",
                    "evidenceIds": [],
                    "policyRuleIds": [],
                    "assertedFacts": [],
                }
            ],
            "concessions": [],
            "overallSummary": "Demands a figure.",
        }
    )
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.verification_summary.verified_count == 0
    assert INVENTED_MONETARY_VALUE in result.rebuttals.verification_summary.rejection_reasons
    assert result.deterministic_resolution.refund_amount == (
        baseline.deterministic_resolution.refund_amount
    )
    assert result.deterministic_resolution.recommended_action == (
        baseline.deterministic_resolution.recommended_action
    )
    assert result.deterministic_resolution.confidence == (
        baseline.deterministic_resolution.confidence
    )


# ---------------------------------------------------------------------------
# 25. Deterministic explainability and counterfactuals
# ---------------------------------------------------------------------------


def test_counterfactual_thresholds_come_from_policy_evaluation() -> None:
    case, analysis = _load(NO_SHOW_CASE)
    result = _run(NO_SHOW_CASE, _StubProvider())

    rule_thresholds = {
        rule.rule_id: rule.required_value
        for rule in analysis.policy_evaluation.evaluated_rules
    }
    assert result.counterfactual.thresholds, "expected at least one threshold"
    for item in result.counterfactual.thresholds:
        # Every threshold is the policy's own required value, not an invention.
        assert item.threshold == rule_thresholds[item.rule_id]
        assert item.rule_id in rule_thresholds
    assert result.counterfactual.generated_by == "DETERMINISTIC_ENGINE"


def test_counterfactual_directions_match_how_each_rule_is_evaluated() -> None:
    no_show = _run(NO_SHOW_CASE, _StubProvider())
    directions = {item.rule_id: item.direction for item in no_show.counterfactual.thresholds}

    # A minimum-wait rule fails when the wait falls below its threshold.
    assert directions["NO_SHOW_WAIT_TIME"] == "BELOW"
    # A pickup-radius rule fails when the distance rises above its threshold.
    assert directions["NO_SHOW_PICKUP_RADIUS"] == "ABOVE"

    route = _run(ROUTE_CASE, _StubProvider())
    route_directions = {item.rule_id: item.direction for item in route.counterfactual.thresholds}
    assert route_directions["ROUTE_UNEXPLAINED_DEVIATION"] == "BELOW"


def test_explanation_reports_the_deterministic_action_and_basis() -> None:
    case, analysis = _load(ROUTE_CASE)
    result = _run(ROUTE_CASE, _StubProvider())

    assert result.explanation.final_deterministic_action == (
        analysis.resolution_recommendation.recommended_action
    )
    assert result.explanation.ruling == analysis.resolution_recommendation.ruling
    assert result.explanation.deterministic_basis == (
        analysis.resolution_recommendation.explanation
    )


def test_explanation_lists_only_verified_rebuttals() -> None:
    provider = _StubProvider()
    result = _run(ROUTE_CASE, provider)

    explained = {item.rebuttal_id for item in result.explanation.relevant_rebuttals}
    verified = {
        item.rebuttal_id
        for item in [
            *result.rebuttals.rider.verified_rebuttals,
            *result.rebuttals.driver.verified_rebuttals,
        ]
    }
    assert explained == verified
    assert explained


def test_explanation_flags_a_judge_disagreement() -> None:
    """A Judge that disagrees with code is reported, not hidden."""
    provider = _StubProvider(
        judge_payload_factory=lambda context: {
            **_valid_judge(context),
            "recommendedOutcome": sorted(context.allowed_outcomes)[-1],
        }
    )
    result = _run(ROUTE_CASE, provider)

    if result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW"):
        if result.judge.recommended_outcome != result.deterministic_resolution.recommended_action:
            assert result.explanation.judge_advisory_differs is True
        # Whatever happens, the deterministic action is untouched.
        assert result.deterministic_resolution.recommended_action


def test_counterfactual_contains_no_hypothetical_evidence() -> None:
    """No counterfactual may propose a story the record cannot evaluate."""
    result = _run(NO_SHOW_CASE, _StubProvider())
    serialized = json.dumps(result.counterfactual.model_dump(by_alias=True)).lower()

    for phrase in ("maybe", "perhaps", "if the rider had called", "what if", "could have"):
        assert phrase not in serialized


# ---------------------------------------------------------------------------
# 26-27. No chain-of-thought, no secrets
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYS = {
    "chain_of_thought",
    "chainofthought",
    "chain_of_thoughts",
    "cot",
    "reasoning",
    "internal_reasoning",
    "thinking",
    "thoughts",
    "scratchpad",
}


def _walk_keys(node: object):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def test_response_contains_no_chain_of_thought_field() -> None:
    result = _run(ROUTE_CASE, _StubProvider())
    payload = json.loads(result.model_dump_json(by_alias=True))

    leaked = {key for key in _walk_keys(payload) if key.lower() in _FORBIDDEN_KEYS}
    assert leaked == set(), f"chain-of-thought-like keys present: {leaked}"


def test_rebuttal_output_schema_has_no_reasoning_field() -> None:
    """The contract itself offers no field for deliberation."""
    from app.models.rebuttal import RebuttalOutput, RebuttalResponse

    for model in (RebuttalOutput, RebuttalResponse):
        fields = {name.lower() for name in model.model_fields}
        assert fields & _FORBIDDEN_KEYS == set()


def test_audit_never_carries_a_prompt_a_secret_or_reasoning() -> None:
    result = _run(ROUTE_CASE, _StubProvider())

    for event in result.audit:
        for key in event.metadata:
            assert key.lower() not in _FORBIDDEN_KEYS | {
                "api_key",
                "authorization",
                "headers",
                "prompt",
                "system_prompt",
                "user_prompt",
                "raw_response",
            }

    serialized = json.dumps([e.metadata for e in result.audit])
    assert "Bearer" not in serialized
    assert "api_key" not in serialized.lower()


def test_rebuttal_audit_events_are_recorded() -> None:
    result = _run(ROUTE_CASE, _StubProvider())
    names = [event.event for event in result.audit]

    assert "REBUTTAL_CONTEXT_BUILT" in names
    assert "RIDER_REBUTTAL_STARTED" in names
    assert "RIDER_REBUTTAL_COMPLETED" in names
    assert "DRIVER_REBUTTAL_STARTED" in names
    assert "DRIVER_REBUTTAL_COMPLETED" in names
    assert "REBUTTAL_VERIFIED" in names
    # Ordering: the rebuttal layer precedes the Judge.
    assert names.index("REBUTTAL_CONTEXT_BUILT") < names.index("JUDGE_CONTEXT_BUILT")


def test_audit_records_rebuttal_failure_events() -> None:
    provider = _StubProvider(fail_rebuttal_side="RIDER")
    result = _run(ROUTE_CASE, provider)
    names = [event.event for event in result.audit]

    assert "RIDER_REBUTTAL_FAILED" in names
    assert "DRIVER_REBUTTAL_COMPLETED" in names


def test_audit_records_that_rejected_rebuttals_were_excluded() -> None:
    result = _run(ROUTE_CASE, _StubProvider())
    context_event = next(
        event for event in result.audit if event.event == "JUDGE_CONTEXT_BUILT"
    )
    assert context_event.metadata["rejected_rebuttals_included"] is False
    assert context_event.metadata["rejected_claims_included"] is False


def test_audit_records_rebuttals_not_run_when_advocates_fail() -> None:
    provider = _StubProvider(fail_advocate_side="RIDER")
    result = _run(ROUTE_CASE, provider)
    names = [event.event for event in result.audit]

    assert "REBUTTALS_NOT_RUN" in names
    assert "RIDER_REBUTTAL_STARTED" not in names


# ---------------------------------------------------------------------------
# 28-29. Mock mode, pipeline shape and API compatibility
# ---------------------------------------------------------------------------


def test_mock_mode_drives_the_full_stage_6_pipeline() -> None:
    case, analysis = _load(ROUTE_CASE)
    result = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS, provider=MockLlmProvider()
    ).run(case, analysis)

    assert result.rebuttals.rider.status == "COMPLETE"
    assert result.rebuttals.driver.status == "COMPLETE"
    assert result.rebuttals.verification_summary.verified_count > 0
    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")
    assert result.explanation.final_deterministic_action
    assert result.counterfactual.statement


def test_mock_rebuttals_are_deterministic_across_calls() -> None:
    first = _run(ROUTE_CASE, _StubProvider())
    second = _run(ROUTE_CASE, _StubProvider())

    assert [
        (r.rebuttal_id, r.stance, r.target_claim_id)
        for r in first.rebuttals.rider.verified_rebuttals
    ] == [
        (r.rebuttal_id, r.stance, r.target_claim_id)
        for r in second.rebuttals.rider.verified_rebuttals
    ]


def test_pipeline_stages_appear_in_execution_order() -> None:
    result = _run(ROUTE_CASE, _StubProvider())
    stages = [stage.stage for stage in result.pipeline]

    assert stages.index("RIDER_ADVOCATE") < stages.index("RIDER_REBUTTAL")
    assert stages.index("DRIVER_ADVOCATE") < stages.index("DRIVER_REBUTTAL")
    assert stages.index("RIDER_REBUTTAL") < stages.index("REBUTTAL_VERIFICATION")
    assert stages.index("REBUTTAL_VERIFICATION") < stages.index("JUDGE")
    assert all(stage.status == "COMPLETE" for stage in result.pipeline)


def test_stage_4_advocate_endpoint_is_unchanged() -> None:
    """The Stage 4 contract must not grow a rebuttals field."""
    with TestClient(app) as client:
        response = client.post(f"/api/cases/{ROUTE_CASE}/advocates/run")

    assert response.status_code == 200
    body = response.json()
    assert "rebuttals" not in body
    assert "judge" not in body
    assert set(body) >= {"caseId", "rider", "driver", "agentRun", "verificationSummary", "pipeline"}


def test_resolution_endpoint_returns_all_stage_6_layers() -> None:
    with TestClient(app) as client:
        response = client.post(f"/api/cases/{ROUTE_CASE}/resolution/run")

    assert response.status_code == 200
    body = response.json()
    for key in (
        "rider",
        "driver",
        "rebuttals",
        "judge",
        "deterministicResolution",
        "explanation",
        "counterfactual",
        "audit",
    ):
        assert key in body, f"missing layer: {key}"

    assert body["rebuttals"]["round"] == 1
    assert body["rebuttals"]["maxRounds"] == 1
    assert body["counterfactual"]["generatedBy"] == "DETERMINISTIC_ENGINE"


def test_resolution_endpoint_rejects_an_unknown_case() -> None:
    with TestClient(app) as client:
        response = client.post("/api/cases/NOPE-999/resolution/run")
    assert response.status_code == 404
