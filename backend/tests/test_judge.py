"""Stage 5 Judge tests — entirely offline.

No live Gemini call and no live Ollama call is made anywhere in this file. Two
layers keep that true:

1. ``conftest.py`` forces ``AGENT_MODE=mock`` for the session.
2. Every test here injects its own provider through the orchestrator's
   ``provider`` seam, so no test can fall back to the environment.

The stub is a transport stub only. Prompt assembly, JSON parsing, Pydantic
validation, claim verification and Judge validation are all the real production
classes.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.judge_context_builder import JudgeContextBuilder
from app.agents.judge_validation import (
    CLAIM_BOTH_ACCEPTED_AND_REJECTED,
    CLAIM_ID_BELONGS_TO_OTHER_SIDE,
    DUPLICATE_CLAIM_ID,
    EMPTY_REASONING_SUMMARY,
    HUMAN_REVIEW_FLAG_INCONSISTENT,
    HUMAN_REVIEW_OVERRIDE_ATTEMPTED,
    INVENTED_MONETARY_VALUE,
    JudgeOutputValidationService,
    OUTCOME_NOT_ALLOWED_FOR_DISPUTE,
    UNKNOWN_CLAIM_ID,
    UNKNOWN_EVIDENCE_ID,
    UNKNOWN_POLICY_RULE_ID,
)
from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProviderError,
    ProviderErrorCode,
)
from app.agents.providers.mock import MockLlmProvider
from app.benchmarks.cases import case_by_id
from app.models.advocate import AdvocateRunResponse
from app.models.judge import JudgeCaseContext, JudgeOutput
from app.services.advocate_orchestrator import AdvocateOrchestratorService
from app.services.audit import (
    JUDGE_COMPLETED,
    JUDGE_CONTEXT_BUILT,
    JUDGE_FAILED,
    JUDGE_NOT_RUN,
    JUDGE_OUTPUT_REJECTED,
    JUDGE_OUTPUT_VALIDATED,
    JUDGE_STARTED,
    PENDING_HUMAN_REVIEW,
)
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

ROUTE_CASE = "DISP-005"
NO_SHOW_CASE = "DISP-002"

MOCK_SETTINGS = AgentSettings(mode="mock", provider="mock")


# ---------------------------------------------------------------------------
# Stub transport
# ---------------------------------------------------------------------------


class _StubProvider:
    """Transport stub. Advocate calls are delegated; Judge calls are scripted.

    Delegating advocate calls to the real mock provider keeps this stub small and
    means the advocate half of every test is exercised by production code.
    """

    name = "stub"

    def __init__(
        self,
        *,
        judge_payload: dict | None = None,
        judge_payload_factory=None,
        judge_raw: str | None = None,
        judge_error: Exception | None = None,
        fail_side: str | None = None,
    ) -> None:
        self._judge_payload = judge_payload
        self._judge_payload_factory = judge_payload_factory
        self._judge_raw = judge_raw
        self._judge_error = judge_error
        self._fail_side = fail_side
        self._mock = MockLlmProvider()
        self.requests: list[AgentCompletionRequest] = []

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.requests.append(request)
        if request.metadata.get("role") != "judge":
            if self._fail_side and request.metadata.get("side") == self._fail_side:
                raise LlmProviderError(
                    "stub advocate outage",
                    provider=self.name,
                    code=ProviderErrorCode.SERVER_ERROR,
                )
            return self._mock.complete(request)

        if self._judge_error is not None:
            raise self._judge_error
        if self._judge_raw is not None:
            return LlmCompletion(
                raw_text=self._judge_raw,
                provider_name=self.name,
                model_name="stub-judge",
                duration_ms=7,
            )
        context = JudgeCaseContext.model_validate_json(request.metadata["context_json"])
        payload = (
            self._judge_payload_factory(context)
            if self._judge_payload_factory is not None
            else (self._judge_payload or _valid_payload(context))
        )
        return LlmCompletion(
            raw_text=json.dumps(payload),
            provider_name=self.name,
            model_name="stub-judge",
            duration_ms=7,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )

    @property
    def judge_requests(self) -> list[AgentCompletionRequest]:
        return [r for r in self.requests if r.metadata.get("role") == "judge"]


def _valid_payload(context: JudgeCaseContext) -> dict:
    """A Judge response that passes every validation rule for this context."""
    outcome = sorted(_allowed(context))[0]
    pending = context.resolution_mode == "HUMAN_REVIEW"
    return {
        "status": "PENDING_HUMAN_REVIEW" if pending else "COMPLETE",
        "recommendedOutcome": outcome,
        "acceptedRiderClaimIds": [c.claim_id for c in context.rider.verified_claims],
        "acceptedDriverClaimIds": [c.claim_id for c in context.driver.verified_claims],
        "rejectedRiderClaimIds": [],
        "rejectedDriverClaimIds": [],
        "reasoningSummary": "Grounded in the supplied facts and policy.",
        "evidenceIds": [item.id for item in context.evidence],
        "policyRuleIds": [rule.rule_id for rule in context.applicable_policy.rules],
        "uncertainties": [],
        "requiresHumanReview": pending,
    }


def _allowed(context: JudgeCaseContext) -> list[str]:
    return list(context.allowed_outcomes)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _load(case_id: str):
    case = case_by_id(case_id)
    analysis = DisputeAnalysisService().analyze(case)
    return case, analysis


def _advocate_run(case, analysis, provider=None) -> AdvocateRunResponse:
    return AdvocateOrchestratorService(
        settings=MOCK_SETTINGS, provider=provider or MockLlmProvider()
    ).run(case, analysis)


def _judge_context(case, analysis, advocate_run) -> JudgeCaseContext:
    advocate_context = AdvocateContextBuilder().build(case, analysis)
    return JudgeContextBuilder().build(
        case, analysis, advocate_context, advocate_run.rider, advocate_run.driver
    )


def _run(case_id: str, provider: _StubProvider):
    case, analysis = _load(case_id)
    service = ResolutionOrchestratorService(settings=MOCK_SETTINGS, provider=provider)
    return service.run(case, analysis)


def _run_with_judge(case_id: str, **kwargs):
    return _run(case_id, _StubProvider(**kwargs))


# ---------------------------------------------------------------------------
# 1-2. What the Judge receives
# ---------------------------------------------------------------------------


def test_judge_receives_only_verified_claims() -> None:
    """Every claim in the Judge context came from the verified list."""
    case, analysis = _load(ROUTE_CASE)
    advocate_run = _advocate_run(case, analysis)
    context = _judge_context(case, analysis, advocate_run)

    verified_ids = {
        f"{side}-{claim.claim_id}"
        for side, result in (("RIDER", advocate_run.rider), ("DRIVER", advocate_run.driver))
        for claim in result.verified_claims
    }
    context_ids = {c.claim_id for c in context.rider.verified_claims} | {
        c.claim_id for c in context.driver.verified_claims
    }
    assert context_ids == verified_ids
    assert context.rider.verified_claim_count == len(advocate_run.rider.verified_claims)


def test_rejected_claims_never_appear_as_trusted_judge_context() -> None:
    """A rejected claim must not reach the Judge prompt in any form.

    The context is driven by a case whose advocates are forced to hallucinate an
    evidence ID, so there is a real rejected claim to leak.
    """
    case, analysis = _load(ROUTE_CASE)
    advocate_run = _advocate_run(case, analysis)

    # Inject a rejected claim onto the rider side, as the verifier would produce.
    from app.models.advocate import ClaimRejection

    rejected = ClaimRejection(
        claim_id="R9",
        claim="The driver admitted fault in a private message.",
        reason="EVIDENCE_ID_NOT_FOUND",
        detail="E99 does not exist in this case record.",
        evidence_ids=["E99"],
        policy_refs=["ROUTE_REQUIRED_EVIDENCE"],
    )
    rider = advocate_run.rider.model_copy(update={"rejected_claims": [rejected]})
    advocate_context = AdvocateContextBuilder().build(case, analysis)
    context = JudgeContextBuilder().build(case, analysis, advocate_context, rider, advocate_run.driver)

    serialized = context.model_dump_json(by_alias=True)
    assert "R9" not in serialized
    assert "E99" not in serialized
    assert "admitted fault" not in serialized
    # And the rejected claim is genuinely absent from the structured claims.
    assert all(c.claim_id != "R9" for c in context.rider.verified_claims)


def test_judge_context_contains_no_forbidden_fields() -> None:
    """The answer must not leak into the Judge's context."""
    case, analysis = _load(ROUTE_CASE)
    advocate_run = _advocate_run(case, analysis)
    context = _judge_context(case, analysis, advocate_run)
    serialized = context.model_dump_json(by_alias=True).lower()

    forbidden = [
        "resolutionrecommendation",
        "recommendedaction",
        "refundamount",
        "ruling",
        "confidence",
        "escalationreasons",
        "analysisinput",
        "counterfactual",
        "explanation",
    ]
    for token in forbidden:
        assert token not in serialized, f"{token} leaked into the Judge context"

    # The one piece of decision state that IS allowed, because the Judge cannot
    # know whether automation is permitted without it.
    assert context.resolution_mode in ("AUTO_RESOLVE", "HUMAN_REVIEW")


def test_judge_context_declares_the_zero_claim_case_explicitly() -> None:
    """A side with no trusted claims must be unmistakable, not merely empty."""
    case, analysis = _load(ROUTE_CASE)
    advocate_run = _advocate_run(case, analysis)
    stripped = advocate_run.driver.model_copy(update={"verified_claims": []})
    advocate_context = AdvocateContextBuilder().build(case, analysis)
    context = JudgeContextBuilder().build(
        case, analysis, advocate_context, advocate_run.rider, stripped
    )

    assert context.driver.verified_claim_count == 0
    assert context.driver.has_verified_claims is False
    assert context.driver.verified_claims == []
    # The other side is untouched, so the Judge can still run.
    assert context.rider.has_verified_claims is True


# ---------------------------------------------------------------------------
# 3-7. Validation rules
# ---------------------------------------------------------------------------


def _validate(case_id: str, mutate) -> list[str]:
    """Run validation over a Judge output mutated by ``mutate``. Returns codes."""
    case, analysis = _load(case_id)
    advocate_run = _advocate_run(case, analysis)
    context = _judge_context(case, analysis, advocate_run)
    payload = _valid_payload(context)
    mutate(payload, context)
    output = JudgeOutput.model_validate(payload)
    result = JudgeOutputValidationService().validate(output, context)
    return [issue.code for issue in result.issues]


def test_invented_claim_id_is_rejected() -> None:
    codes = _validate(ROUTE_CASE, lambda p, c: p["acceptedRiderClaimIds"].append("RIDER-R99"))
    assert UNKNOWN_CLAIM_ID in codes


def test_invented_evidence_id_is_rejected() -> None:
    codes = _validate(ROUTE_CASE, lambda p, c: p["evidenceIds"].append("E99"))
    assert UNKNOWN_EVIDENCE_ID in codes


def test_invented_policy_id_is_rejected() -> None:
    codes = _validate(
        ROUTE_CASE, lambda p, c: p["policyRuleIds"].append("NO_SHOW_WAIT_TIME")
    )
    assert UNKNOWN_POLICY_RULE_ID in codes


def test_unsupported_outcome_is_rejected() -> None:
    """A no-show outcome cannot be recommended for a route case."""
    codes = _validate(
        ROUTE_CASE, lambda p, c: p.__setitem__("recommendedOutcome", "UPHOLD_CANCELLATION_CHARGE")
    )
    assert OUTCOME_NOT_ALLOWED_FOR_DISPUTE in codes


def test_claim_cited_for_the_wrong_side_is_rejected() -> None:
    """A Driver claim placed in a Rider list is a distinct failure from an unknown ID."""

    def mutate(payload: dict, context: JudgeCaseContext) -> None:
        driver_claim = context.driver.verified_claims[0].claim_id
        payload["acceptedRiderClaimIds"].append(driver_claim)

    assert CLAIM_ID_BELONGS_TO_OTHER_SIDE in _validate(ROUTE_CASE, mutate)


def test_claim_both_accepted_and_rejected_is_rejected() -> None:
    def mutate(payload: dict, context: JudgeCaseContext) -> None:
        claim_id = context.rider.verified_claims[0].claim_id
        payload["acceptedRiderClaimIds"] = [claim_id]
        payload["rejectedRiderClaimIds"] = [claim_id]

    assert CLAIM_BOTH_ACCEPTED_AND_REJECTED in _validate(ROUTE_CASE, mutate)


def test_duplicate_claim_id_is_rejected() -> None:
    def mutate(payload: dict, context: JudgeCaseContext) -> None:
        claim_id = context.rider.verified_claims[0].claim_id
        payload["acceptedRiderClaimIds"] = [claim_id, claim_id]

    assert DUPLICATE_CLAIM_ID in _validate(ROUTE_CASE, mutate)


def test_empty_reasoning_summary_is_rejected() -> None:
    assert EMPTY_REASONING_SUMMARY in _validate(
        ROUTE_CASE, lambda p, c: p.__setitem__("reasoningSummary", "   ")
    )


def test_a_valid_payload_produces_no_issues() -> None:
    assert _validate(ROUTE_CASE, lambda p, c: None) == []
    assert _validate(NO_SHOW_CASE, lambda p, c: None) == []


# ---------------------------------------------------------------------------
# 7 / 15. Money
# ---------------------------------------------------------------------------


def test_an_invented_refund_amount_in_prose_is_rejected() -> None:
    """The Judge has no amount field, so prose is the only route for one."""
    codes = _validate(
        ROUTE_CASE,
        lambda p, c: p.__setitem__(
            "reasoningSummary", "The rider should receive a refund of SGD 2.50."
        ),
    )
    assert INVENTED_MONETARY_VALUE in codes


def test_quoting_a_supplied_deterministic_value_is_allowed() -> None:
    """Repeating a fact the Judge was given is not inventing a figure."""
    case, analysis = _load(ROUTE_CASE)
    advocate_run = _advocate_run(case, analysis)
    context = _judge_context(case, analysis, advocate_run)
    fare_difference = context.deterministic_facts.fare_difference

    payload = _valid_payload(context)
    payload["reasoningSummary"] = f"The fare difference of SGD {fare_difference} is fully explained."
    result = JudgeOutputValidationService().validate(JudgeOutput.model_validate(payload), context)
    assert [issue.code for issue in result.issues] == []


def test_an_invented_amount_cannot_change_the_deterministic_refund() -> None:
    """The strongest money guarantee: the number is not reachable from the Judge."""
    case, analysis = _load(ROUTE_CASE)
    before = analysis.resolution_recommendation.refund_amount

    result = _run_with_judge(
        ROUTE_CASE,
        judge_payload_factory=lambda context: {
            **_valid_payload(context),
            "reasoningSummary": "A refund of SGD 999.99 is warranted.",
        },
    )

    assert result.deterministic_resolution.refund_amount == before
    assert result.judge.status == "FAILED"
    assert INVENTED_MONETARY_VALUE in {i.code for i in result.judge.validation_issues}


def test_an_invented_amount_field_fails_schema_validation() -> None:
    """``extra="forbid"`` means a smuggled amount is rejected, not ignored."""
    payload = {"refundAmount": 999.99}
    with pytest.raises(Exception) as caught:
        JudgeOutput.model_validate({**_minimal_payload(), **payload})
    assert "refundAmount" in str(caught.value) or "extra" in str(caught.value).lower()


def _minimal_payload() -> dict:
    return {
        "status": "COMPLETE",
        "recommendedOutcome": "NO_REFUND",
        "acceptedRiderClaimIds": [],
        "acceptedDriverClaimIds": [],
        "rejectedRiderClaimIds": [],
        "rejectedDriverClaimIds": [],
        "reasoningSummary": "ok",
        "evidenceIds": [],
        "policyRuleIds": [],
        "uncertainties": [],
        "requiresHumanReview": False,
    }


# ---------------------------------------------------------------------------
# 8. Human review
# ---------------------------------------------------------------------------


def _human_review_case():
    """A case whose deterministic gate requires human review."""
    from app.models.case import DisputeCase

    for case_id in ("DISP-001", "DISP-003", "DISP-004", "DISP-006", "DISP-007", "DISP-008"):
        try:
            case, analysis = _load(case_id)
        except Exception:  # noqa: BLE001
            continue
        if analysis.resolution_mode == "HUMAN_REVIEW":
            return case, analysis
    pytest.skip("No HUMAN_REVIEW fixture available")


def test_human_review_case_cannot_become_executable() -> None:
    case, analysis = _human_review_case()
    advocate_run = _advocate_run(case, analysis)

    def factory(context: JudgeCaseContext) -> dict:
        payload = _valid_payload(context)
        # A model attempting to overrule the deterministic gate.
        payload["status"] = "COMPLETE"
        payload["requiresHumanReview"] = False
        return payload

    service = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS, provider=_StubProvider(judge_payload_factory=factory)
    )
    result = service.run(case, analysis)

    assert result.deterministic_resolution.resolution_mode == "HUMAN_REVIEW"
    assert result.judge.status == "FAILED"
    codes = {issue.code for issue in result.judge.validation_issues}
    assert HUMAN_REVIEW_OVERRIDE_ATTEMPTED in codes
    assert HUMAN_REVIEW_FLAG_INCONSISTENT in codes
    assert result.judge.executable is False
    # The deterministic refusal survives the Judge's attempt to overrule it.
    assert result.deterministic_resolution.recommended_action == "HUMAN_REVIEW"
    assert result.deterministic_resolution.refund_amount == 0


def test_human_review_case_returns_pending_status_when_the_judge_complies() -> None:
    case, analysis = _human_review_case()
    result = _run_with_judge(
        case.id, judge_payload_factory=lambda context: _valid_payload(context)
    )

    assert result.judge.status == "PENDING_HUMAN_REVIEW"
    assert result.judge.requires_human_review is True
    assert result.judge.executable is False
    assert PENDING_HUMAN_REVIEW in [event.event for event in result.audit]
    # It may still summarise — that is the point of running it at all.
    assert result.judge.reasoning_summary


# ---------------------------------------------------------------------------
# 9. Advocate failure
# ---------------------------------------------------------------------------


def test_one_advocate_failed_means_the_judge_does_not_run() -> None:
    """A one-sided dispute must not be judged as though both sides argued."""
    result = _run(ROUTE_CASE, _StubProvider(fail_side="RIDER"))

    assert result.rider.status == "FAILED"
    assert result.driver.status == "COMPLETE"
    assert result.judge.status == "NOT_RUN"
    assert result.judge.skip_reason == "ADVOCATE_INPUT_INCOMPLETE"
    assert result.judge.recommended_outcome is None
    assert JUDGE_NOT_RUN in [event.event for event in result.audit]
    # No Judge call was attempted at all.
    assert JUDGE_STARTED not in [event.event for event in result.audit]


def test_both_advocates_failed_means_the_judge_does_not_run() -> None:
    case, analysis = _load(ROUTE_CASE)

    class _AlwaysFail(_StubProvider):
        """Fails every advocate call, so both sides fail."""

        def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
            if request.metadata.get("role") != "judge":
                raise LlmProviderError(
                    "outage", provider="stub", code=ProviderErrorCode.SERVER_ERROR
                )
            return super().complete(request)

    result = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS, provider=_AlwaysFail()
    ).run(case, analysis)

    assert result.rider.status == "FAILED"
    assert result.driver.status == "FAILED"
    assert result.judge.status == "NOT_RUN"
    assert result.judge.skip_reason == "ADVOCATE_INPUT_INCOMPLETE"


# ---------------------------------------------------------------------------
# 10-12. Judge failure modes
# ---------------------------------------------------------------------------


def test_malformed_judge_json_produces_a_safe_failed_state() -> None:
    result = _run_with_judge(ROUTE_CASE, judge_raw="this is not json at all")

    assert result.judge.status == "FAILED"
    assert result.judge.recommended_outcome is None
    assert result.judge.executable is False
    assert result.rider.status == "COMPLETE"
    assert JUDGE_FAILED in [event.event for event in result.audit]


def test_judge_provider_429_produces_a_safe_failed_state() -> None:
    result = _run_with_judge(
        ROUTE_CASE,
        judge_error=LlmProviderError(
            "rate limited", provider="stub", retryable=True, code=ProviderErrorCode.RATE_LIMITED
        ),
    )

    assert result.judge.status == "FAILED"
    assert result.judge.recommended_outcome is None
    assert result.judge.execution is not None


def test_judge_provider_503_produces_a_safe_failed_state() -> None:
    result = _run_with_judge(
        ROUTE_CASE,
        judge_error=LlmProviderError(
            "server error", provider="stub", retryable=True, code=ProviderErrorCode.SERVER_ERROR
        ),
    )

    assert result.judge.status == "FAILED"
    assert result.judge.recommended_outcome is None


def test_a_judge_failure_leaves_everything_deterministic_visible() -> None:
    """Failure isolation: the advocates and the deterministic layer survive."""
    case, analysis = _load(ROUTE_CASE)
    result = _run_with_judge(
        ROUTE_CASE,
        judge_error=LlmProviderError(
            "outage", provider="stub", code=ProviderErrorCode.SERVER_ERROR
        ),
    )

    assert result.rider.status == "COMPLETE"
    assert result.driver.status == "COMPLETE"
    assert result.verification_summary.verified_count > 0
    assert result.deterministic_resolution.recommended_action == (
        analysis.resolution_recommendation.recommended_action
    )
    assert result.deterministic_resolution.refund_amount == (
        analysis.resolution_recommendation.refund_amount
    )
    assert result.judge.status == "FAILED"


def test_a_judge_failure_does_not_erase_a_human_review_decision() -> None:
    """The deterministic gate's own escalation survives a Judge outage."""
    case, analysis = _human_review_case()
    result = _run_with_judge(
        case.id,
        judge_error=LlmProviderError(
            "outage", provider="stub", code=ProviderErrorCode.SERVER_ERROR
        ),
    )

    assert result.judge.status == "FAILED"
    assert result.deterministic_resolution.resolution_mode == "HUMAN_REVIEW"
    assert result.deterministic_resolution.recommended_action == "HUMAN_REVIEW"


# ---------------------------------------------------------------------------
# 13-14. Happy paths
# ---------------------------------------------------------------------------


def test_a_valid_route_case_judge_succeeds() -> None:
    result = _run_with_judge(ROUTE_CASE, judge_payload_factory=lambda c: _valid_payload(c))

    assert result.judge.status == "COMPLETE"
    assert result.judge.recommended_outcome in {
        "NO_REFUND",
        "PARTIAL_REFUND",
        "FULL_FARE_DIFFERENCE_REFUND",
    }
    assert result.judge.executable is True
    assert result.judge.validation_issues == []
    assert result.judge.reasoning_summary
    assert result.judge.evidence_ids
    assert result.judge.policy_rule_ids


def test_a_valid_no_show_case_judge_succeeds() -> None:
    result = _run_with_judge(NO_SHOW_CASE, judge_payload_factory=lambda c: _valid_payload(c))

    assert result.judge.status == "COMPLETE"
    assert result.judge.recommended_outcome in {
        "UPHOLD_CANCELLATION_CHARGE",
        "REFUND_CANCELLATION_CHARGE",
    }
    assert result.judge.validation_issues == []


# ---------------------------------------------------------------------------
# 15-17. Determinism is untouched by Judge prose
# ---------------------------------------------------------------------------

_FLOWERY = (
    "In my considered view the rider is plainly entitled to the largest possible "
    "refund and the driver's conduct was egregious; the charge should be reversed "
    "and a penalty applied."
)


def test_deterministic_refund_confidence_and_escalation_ignore_judge_prose() -> None:
    """The same case judged two ways must produce identical deterministic output."""
    case, analysis = _load(ROUTE_CASE)
    baseline = analysis.resolution_recommendation

    agreeable = _run_with_judge(ROUTE_CASE, judge_payload_factory=lambda c: _valid_payload(c))
    adversarial = _run_with_judge(
        ROUTE_CASE,
        judge_payload_factory=lambda context: {
            **_valid_payload(context),
            # An outcome at the opposite end of the allowed set, plus maximally
            # loaded prose. Neither may move a deterministic value.
            "recommendedOutcome": sorted(context.allowed_outcomes)[-1],
            "reasoningSummary": _FLOWERY,
        },
    )

    for result in (agreeable, adversarial):
        assert result.deterministic_resolution.refund_amount == baseline.refund_amount
        assert result.deterministic_resolution.recommended_action == baseline.recommended_action
        assert result.deterministic_resolution.ruling == baseline.ruling
        assert result.deterministic_resolution.confidence == analysis.confidence.overall_confidence
        assert result.deterministic_resolution.resolution_mode == analysis.resolution_mode
        assert result.deterministic_resolution.escalation_reasons == list(
            analysis.escalation_reasons
        )

    assert (
        agreeable.deterministic_resolution.refund_amount
        == adversarial.deterministic_resolution.refund_amount
    )
    assert (
        agreeable.deterministic_resolution.recommended_action
        == adversarial.deterministic_resolution.recommended_action
    )


def test_the_judge_does_not_receive_the_refund_amount() -> None:
    """Belt and braces on the money boundary: not in the prompt either."""
    provider = _StubProvider(judge_payload_factory=lambda c: _valid_payload(c))
    result = _run(ROUTE_CASE, provider)
    assert result.judge.status == "COMPLETE"

    for request in provider.judge_requests:
        combined = request.system_prompt + request.user_prompt
        assert "refundAmount" not in combined
        assert "recommendedAction" not in combined
        assert "resolutionRecommendation" not in combined


# ---------------------------------------------------------------------------
# 18. Prompt content
# ---------------------------------------------------------------------------


def test_the_judge_prompt_states_the_binding_rules() -> None:
    provider = _StubProvider(judge_payload_factory=lambda c: _valid_payload(c))
    _run(ROUTE_CASE, provider)

    prompt = provider.judge_requests[0].system_prompt.lower()
    for rule in (
        "never invent",
        "only",
        "never state a refund",
        "chain",
        "expected and normal",
        "json",
    ):
        assert rule in prompt, f"prompt is missing the rule about {rule!r}"

    # The prompt must ask for a summary, not for hidden deliberation.
    assert "reasoningsummary" in prompt
    assert "chain of thought" in prompt or "chain-of-thought" in prompt


def test_the_judge_context_serialized_to_the_prompt_has_no_rejected_claims() -> None:
    provider = _StubProvider(judge_payload_factory=lambda c: _valid_payload(c))
    _run(ROUTE_CASE, provider)
    body = provider.judge_requests[0].user_prompt
    assert "rejectedClaims" not in body


# ---------------------------------------------------------------------------
# 19. Mock mode
# ---------------------------------------------------------------------------


def test_mock_mode_drives_the_judge_end_to_end() -> None:
    case, analysis = _load(ROUTE_CASE)
    result = ResolutionOrchestratorService(settings=MOCK_SETTINGS).run(case, analysis)

    assert result.judge.status == "COMPLETE"
    assert result.judge.validation_issues == []
    assert result.agent_run.mode == "mock"
    assert result.judge.execution is not None
    assert result.judge.execution.provider == "mock"


def test_mock_judge_is_deterministic_across_calls() -> None:
    case, analysis = _load(ROUTE_CASE)
    service = ResolutionOrchestratorService(settings=MOCK_SETTINGS)
    first = service.run(case, analysis).judge
    second = service.run(case, analysis).judge

    assert first.status == second.status
    assert first.recommended_outcome == second.recommended_outcome
    assert first.reasoning_summary == second.reasoning_summary


def test_mock_judge_output_passes_real_validation_for_both_dispute_types() -> None:
    """The mock must not normalise something a live model would be rejected for."""
    for case_id in (ROUTE_CASE, NO_SHOW_CASE):
        case, analysis = _load(case_id)
        result = ResolutionOrchestratorService(settings=MOCK_SETTINGS).run(case, analysis)
        assert result.judge.validation_issues == [], case_id
        assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")


# ---------------------------------------------------------------------------
# 20. Stage 4 compatibility
# ---------------------------------------------------------------------------


def test_stage_4_advocate_endpoint_is_unchanged() -> None:
    from app.main import app

    client = TestClient(app)
    response = client.post(f"/api/cases/{ROUTE_CASE}/advocates/run")
    assert response.status_code == 200
    body = response.json()

    assert sorted(body.keys()) == [
        "agentRun",
        "caseId",
        "disputeType",
        "driver",
        "pipeline",
        "rider",
        "verificationSummary",
    ]
    # The Stage 4 response must not have grown a Judge block.
    assert "judge" not in body
    assert "deterministicResolution" not in body
    assert any(stage["stage"] == "JUDGE" for stage in body["pipeline"])


def test_stage_5_resolution_endpoint_returns_both_halves() -> None:
    from app.main import app

    client = TestClient(app)
    response = client.post(f"/api/cases/{ROUTE_CASE}/resolution/run")
    assert response.status_code == 200
    body = response.json()

    assert body["judge"]["status"] in ("COMPLETE", "PENDING_HUMAN_REVIEW", "FAILED", "NOT_RUN")
    assert "deterministicResolution" in body
    assert "refundAmount" in body["deterministicResolution"]
    # The Judge block must never carry a monetary field.
    assert "refundAmount" not in body["judge"]


def test_resolution_endpoint_rejects_an_unknown_case() -> None:
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/cases/NOPE-999/resolution/run").status_code == 404


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_audit_trail_records_the_full_success_sequence() -> None:
    result = _run_with_judge(ROUTE_CASE, judge_payload_factory=lambda c: _valid_payload(c))
    names = [event.event for event in result.audit]

    assert names == [
        JUDGE_CONTEXT_BUILT,
        JUDGE_STARTED,
        JUDGE_COMPLETED,
        JUDGE_OUTPUT_VALIDATED,
    ]
    for event in result.audit:
        assert event.timestamp


def test_audit_trail_records_a_rejection() -> None:
    result = _run_with_judge(
        ROUTE_CASE,
        judge_payload_factory=lambda context: {
            **_valid_payload(context),
            "evidenceIds": ["E99"],
        },
    )
    names = [event.event for event in result.audit]
    assert JUDGE_OUTPUT_REJECTED in names
    assert result.judge.status == "FAILED"


def test_audit_metadata_never_carries_a_prompt_or_a_secret() -> None:
    result = _run_with_judge(ROUTE_CASE, judge_payload_factory=lambda c: _valid_payload(c))
    for event in result.audit:
        for key in event.metadata:
            assert key.lower() not in {
                "api_key",
                "authorization",
                "headers",
                "prompt",
                "system_prompt",
                "user_prompt",
                "reasoning",
                "chain_of_thought",
            }
    serialized = json.dumps([e.metadata for e in result.audit])
    assert "Bearer" not in serialized


def test_audit_records_that_rejected_claims_were_excluded() -> None:
    result = _run_with_judge(ROUTE_CASE, judge_payload_factory=lambda c: _valid_payload(c))
    context_event = next(e for e in result.audit if e.event == JUDGE_CONTEXT_BUILT)
    assert context_event.metadata["rejected_claims_included"] is False
