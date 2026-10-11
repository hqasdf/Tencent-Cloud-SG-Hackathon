"""The pipeline's dependency semantics, asserted scenario by scenario.

The orchestrator already had the right *behaviour* before this module existed —
advocate failure blocks the Judge, rebuttal failure does not, the deterministic
resolution survives everything. What it did not have was an explicit, testable
statement of that behaviour, and a consumer could only recover it by reading a
flat stage list and inferring dependencies.

These tests pin both halves:

* the ``workflow`` state machine, which is what the UI renders, and
* the payload that crosses each transition, so a status test cannot pass while
  the data flowing underneath it is wrong.

The stub transport is scripted per (role, side) so a single test can make the
Rider advocate succeed and the Driver rebuttal fail, which is the only way to
exercise the asymmetries the product actually depends on.
"""

from __future__ import annotations

import json

import pytest

from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.judge_context_builder import JudgeContextBuilder
from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProviderError,
    ProviderErrorCode,
)
from app.agents.providers.mock import MockLlmProvider
from app.agents.rebuttal_context_builder import RebuttalContextBuilder
from app.benchmarks.cases import case_by_id
from app.models.judge import JudgeCaseContext
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

ROUTE_CASE = "DISP-005"       # routes to AUTO_RESOLVE
NO_SHOW_CASE = "DISP-002"     # routes to AUTO_RESOLVE
HUMAN_REVIEW_CASE = "DISP-003"  # routes to HUMAN_REVIEW

MOCK_SETTINGS = AgentSettings(mode="mock", provider="mock")


# ---------------------------------------------------------------------------
# Scripted transport
# ---------------------------------------------------------------------------


class ScriptedProvider:
    """A transport whose behaviour is scripted per (role, side).

    Anything not scripted is delegated to the real ``MockLlmProvider``, so the
    half of the pipeline a test is not about still runs through production code
    and a test cannot pass because a stage silently did nothing.
    """

    name = "stub"

    def __init__(
        self,
        *,
        fail: set[tuple[str, str | None]] | None = None,
        raw: dict[tuple[str, str | None], list[str]] | None = None,
    ) -> None:
        self._fail = fail or set()
        # Each key holds a queue of raw responses, consumed in order. A queue of
        # length 2 is how a repair is scripted: the first answer is unusable and
        # the second is good.
        self._raw = {key: list(values) for key, values in (raw or {}).items()}
        self._mock = MockLlmProvider()
        self.requests: list[AgentCompletionRequest] = []

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.requests.append(request)
        key = _key(request)
        if key in self._fail:
            raise LlmProviderError(
                "scripted outage",
                provider=self.name,
                code=ProviderErrorCode.SERVER_ERROR,
            )
        queue = self._raw.get(key)
        if queue:
            return LlmCompletion(
                raw_text=queue.pop(0) if len(queue) > 1 else queue[0],
                provider_name=self.name,
                model_name="stub",
                duration_ms=5,
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            )
        return self._mock.complete(request)

    def calls(self, role: str, side: str | None = None) -> int:
        """Count requests for one role. Uses the same key normalisation as the
        script, so a test can never disagree with the transport about which
        stage a request belonged to.

        Advocate requests carry no ``role`` key at all — the side is what
        identifies them — so an absent role means "advocate" rather than
        "unknown".
        """
        return sum(1 for request in self.requests if _key(request) == (role, side))


def _key(request: AgentCompletionRequest) -> tuple[str, str | None]:
    return (request.metadata.get("role") or "advocate", request.metadata.get("side"))


def _load(case_id: str):
    case = case_by_id(case_id)
    analysis = DisputeAnalysisService().analyze(case)
    return case, analysis


def _run(case_id: str, provider: ScriptedProvider):
    case, analysis = _load(case_id)
    return ResolutionOrchestratorService(
        settings=MOCK_SETTINGS, provider=provider
    ).run(case, analysis)


def _advocate_raw(side: str, *, claim_id: str = "C1") -> str:
    """A minimal, schema-valid advocate response that passes verification.

    It must cite both real evidence *and* a real policy rule: a claim citing no
    policy rule is rejected with ``POLICY_REF_NOT_FOUND`` before its facts are
    even considered. Building the references from the live context is what makes
    this helper a genuine "grounded" response rather than a shape that merely
    parses.
    """
    case, analysis = _load(ROUTE_CASE)
    context = AdvocateContextBuilder().build(case, analysis)
    from app.agents.semantic_registry import supporting_evidence_ids
    evidence = supporting_evidence_ids(context, "UNEXPLAINED_DEVIATION_KM")
    policy_refs = [context.policy.rules[0].rule_id]
    return json.dumps(
        {
            "contractVersion": "atomic-propositions-v2",
            "side": side,
            "summary": f"{side} summary",
            "claims": [
                {
                    "claimId": claim_id,
                    "claim": "A grounded statement.",
                    "evidenceIds": evidence,
                    "policyRefs": policy_refs,
                    "reasoningSummary": "Reasoned from the trusted record.",
                    "importance": "HIGH",
                    "assertedFacts": [],
                    "propositions": [{"propositionId": "P1", "kind": "FACT", "operator": "EQUALS",
                        "fact": "UNEXPLAINED_DEVIATION_KM", "value": context.facts.unexplained_deviation_distance_km,
                        "evidenceIds": evidence}],
                    "disputedEvidenceIds": [],
                }
            ],
            "requestedOutcome": "NO_REFUND",
            "contextAcknowledged": True,
        }
    )


def _wrong_side_raw() -> str:
    """Schema-valid, but names the other side. Never repaired."""
    payload = json.loads(_advocate_raw("RIDER"))
    payload["side"] = "DRIVER"
    return json.dumps(payload)


def _malformed_raw() -> str:
    return "I think the rider should win, but here is no JSON at all."


def _judge_raw(context_json: str, *, outcome: str | None = None) -> str:
    context = JudgeCaseContext.model_validate_json(context_json)
    pending = context.resolution_mode == "HUMAN_REVIEW"
    return json.dumps(
        {
            "status": "PENDING_HUMAN_REVIEW" if pending else "COMPLETE",
            "recommendedOutcome": outcome or sorted(context.allowed_outcomes)[0],
            "acceptedRiderClaimIds": [c.claim_id for c in context.rider.verified_claims],
            "acceptedDriverClaimIds": [
                c.claim_id for c in context.driver.verified_claims
            ],
            "rejectedRiderClaimIds": [],
            "rejectedDriverClaimIds": [],
            "reasoningSummary": "Grounded in the supplied facts and policy.",
            "evidenceIds": [item.id for item in context.evidence],
            "policyRuleIds": [rule.rule_id for rule in context.applicable_policy.rules],
            "uncertainties": [],
            "requiresHumanReview": pending,
        }
    )


# ---------------------------------------------------------------------------
# Scenario A — everything succeeds
# ---------------------------------------------------------------------------


def test_scenario_a_all_success():
    result = _run(ROUTE_CASE, ScriptedProvider())
    workflow = result.workflow

    assert workflow.deterministic.status == "COMPLETE"
    assert workflow.advocates.rider.status == "COMPLETE"
    assert workflow.advocates.driver.status == "COMPLETE"
    assert workflow.rebuttals.rider.status == "COMPLETE"
    assert workflow.rebuttals.driver.status == "COMPLETE"
    assert workflow.judge.status == "COMPLETE"
    assert workflow.final_resolution.status == "COMPLETE"
    assert workflow.explanation.status == "COMPLETE"


def test_scenario_a_every_ai_role_used_the_same_provider():
    provider = ScriptedProvider()
    _run(ROUTE_CASE, provider)

    # Five roles, one transport: two advocates, two rebuttals, one Judge.
    assert provider.calls("advocate", "RIDER") == 1
    assert provider.calls("advocate", "DRIVER") == 1
    assert provider.calls("rebuttal", "RIDER") == 1
    assert provider.calls("rebuttal", "DRIVER") == 1
    assert provider.calls("judge") == 1
    assert len(provider.requests) == 5


# ---------------------------------------------------------------------------
# Scenarios B and C — an initial advocate fails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failing_side", "other_side"),
    [("RIDER", "DRIVER"), ("DRIVER", "RIDER")],
)
def test_scenario_b_c_advocate_failure_blocks_downstream(failing_side, other_side):
    provider = ScriptedProvider(fail={("advocate", failing_side)})
    result = _run(ROUTE_CASE, provider)
    workflow = result.workflow

    assert getattr(workflow.advocates, failing_side.lower()).status == "FAILED"
    assert getattr(workflow.advocates, other_side.lower()).status == "COMPLETE"

    # BLOCKED, not NOT_RUN: nothing was attempted, and a dependency is at fault.
    assert workflow.rebuttals.rider.status == "BLOCKED"
    assert workflow.rebuttals.driver.status == "BLOCKED"
    assert workflow.rebuttals.rider.reason == "ADVOCATE_INPUT_INCOMPLETE"
    assert workflow.judge.status == "BLOCKED"
    assert workflow.judge.reason == "ADVOCATE_INPUT_INCOMPLETE"

    # The deterministic half is untouched.
    assert workflow.final_resolution.status == "COMPLETE"
    assert workflow.explanation.status == "COMPLETE"
    assert result.deterministic_resolution.recommended_action == "NO_REFUND"
    assert result.judge.executable is False


def test_scenario_b_no_rebuttal_or_judge_call_is_spent():
    provider = ScriptedProvider(fail={("advocate", "RIDER")})
    _run(ROUTE_CASE, provider)

    assert provider.calls("rebuttal", "RIDER") == 0
    assert provider.calls("rebuttal", "DRIVER") == 0
    assert provider.calls("judge") == 0
    assert len(provider.requests) == 2


# ---------------------------------------------------------------------------
# Scenarios D and E — rebuttals are supplementary
# ---------------------------------------------------------------------------


def test_scenario_d_one_rebuttal_fails_and_the_judge_still_runs():
    provider = ScriptedProvider(fail={("rebuttal", "RIDER")})
    result = _run(ROUTE_CASE, provider)
    workflow = result.workflow

    assert workflow.advocates.rider.status == "COMPLETE"
    assert workflow.advocates.driver.status == "COMPLETE"
    assert workflow.rebuttals.rider.status == "FAILED"
    assert workflow.rebuttals.driver.status == "COMPLETE"
    assert workflow.judge.status == "COMPLETE"
    assert provider.calls("judge") == 1


def test_scenario_e_both_rebuttals_fail_and_the_judge_still_runs():
    provider = ScriptedProvider(
        fail={("rebuttal", "RIDER"), ("rebuttal", "DRIVER")}
    )
    result = _run(ROUTE_CASE, provider)
    workflow = result.workflow

    assert workflow.rebuttals.rider.status == "FAILED"
    assert workflow.rebuttals.driver.status == "FAILED"
    assert workflow.judge.status == "COMPLETE"
    # The Judge is given the verified initial arguments; it is not starved.
    assert result.judge.accepted_rider_claim_ids
    assert result.judge.accepted_driver_claim_ids


def test_scenario_d_missing_rebuttal_is_never_fabricated():
    provider = ScriptedProvider(fail={("rebuttal", "RIDER")})
    result = _run(ROUTE_CASE, provider)

    assert result.rebuttals.rider.verified_rebuttals == []
    assert result.rebuttals.rider.status == "FAILED"
    # And the successful side was not re-run to fill the gap.
    assert provider.calls("rebuttal", "DRIVER") == 1


# ---------------------------------------------------------------------------
# Scenario F — the Judge fails
# ---------------------------------------------------------------------------


def test_scenario_f_judge_failure_preserves_everything_before_it():
    provider = ScriptedProvider(
        fail={("judge", None)},
    )
    result = _run(ROUTE_CASE, provider)
    workflow = result.workflow

    assert workflow.advocates.rider.status == "COMPLETE"
    assert workflow.advocates.driver.status == "COMPLETE"
    assert workflow.rebuttals.rider.status == "COMPLETE"
    assert workflow.rebuttals.driver.status == "COMPLETE"
    assert workflow.judge.status == "FAILED"

    # The authoritative numbers are still there, unchanged.
    assert workflow.final_resolution.status == "COMPLETE"
    assert result.deterministic_resolution.recommended_action == "NO_REFUND"
    assert result.deterministic_resolution.refund_amount == 0.0
    assert result.deterministic_resolution.confidence is not None
    # And the verified material the advocates produced is still returned.
    assert result.rider.verified_claims
    assert result.driver.verified_claims


# ---------------------------------------------------------------------------
# Scenario G — human review
# ---------------------------------------------------------------------------


def test_scenario_g_human_review_cannot_be_overridden_by_ai():
    result = _run(HUMAN_REVIEW_CASE, ScriptedProvider())
    workflow = result.workflow

    assert workflow.judge.status == "PENDING_HUMAN_REVIEW"
    assert result.judge.status == "PENDING_HUMAN_REVIEW"
    assert result.judge.executable is False
    # The deterministic layer still produced its authoritative statement.
    assert workflow.final_resolution.status == "COMPLETE"
    assert result.deterministic_resolution.resolution_mode == "HUMAN_REVIEW"


def test_scenario_g_the_judge_still_analyses_when_inputs_are_complete():
    provider = ScriptedProvider()
    result = _run(HUMAN_REVIEW_CASE, provider)

    assert provider.calls("judge") == 1
    assert result.judge.reasoning_summary


# ---------------------------------------------------------------------------
# Scenario H — malformed output
# ---------------------------------------------------------------------------


def test_scenario_h_malformed_output_is_repaired_exactly_once():
    provider = ScriptedProvider(
        raw={("advocate", "RIDER"): [_malformed_raw(), _advocate_raw("RIDER")]}
    )
    result = _run(ROUTE_CASE, provider)

    assert provider.calls("advocate", "RIDER") == 2
    assert result.workflow.advocates.rider.status == "COMPLETE"


def test_scenario_h_wrong_side_is_never_repaired():
    provider = ScriptedProvider(raw={("advocate", "RIDER"): [_wrong_side_raw()]})
    result = _run(ROUTE_CASE, provider)

    # A schema-valid answer naming the other side is not a shape problem, so the
    # repair budget is not spent on it.
    assert provider.calls("advocate", "RIDER") == 1
    assert result.workflow.advocates.rider.status == "FAILED"
    assert result.workflow.advocates.rider.failure_code == "WRONG_SIDE"
    # And the failure blocks the dependent stages rather than being papered over.
    assert result.workflow.judge.status == "BLOCKED"


def test_scenario_h_a_trust_boundary_rejection_is_not_a_parse_failure():
    """A hallucinated evidence reference is rejected by verification, not repaired.

    The side still COMPLETES — it produced a usable response — while the claim
    that failed the check is recorded as rejected. Conflating the two would let a
    hallucination look like a transport problem.
    """
    payload = json.loads(_advocate_raw("RIDER"))
    payload["claims"][0]["evidenceIds"] = ["E99"]
    provider = ScriptedProvider(
        raw={("advocate", "RIDER"): [json.dumps(payload)]}
    )
    result = _run(ROUTE_CASE, provider)

    assert provider.calls("advocate", "RIDER") == 1
    assert result.workflow.advocates.rider.status == "COMPLETE"
    assert result.rider.verified_claims == []
    assert [claim.claim_id for claim in result.rider.rejected_claims] == ["C1"]


# ---------------------------------------------------------------------------
# Handoffs — statuses are not enough
# ---------------------------------------------------------------------------


def test_handoff_verification_splits_generated_claims_honestly():
    payload = json.loads(_advocate_raw("RIDER"))
    payload["claims"].append(
        {
            "claimId": "C2",
            "claim": "Invented.",
            # A real policy rule, so the only thing wrong with C2 is its
            # evidence reference — the rejection must be about that, not about
            # a missing policy citation.
            "policyRefs": list(payload["claims"][0]["policyRefs"]),
            "evidenceIds": ["E99"],
            "reasoningSummary": "No support.",
            "importance": "LOW",
            "assertedFacts": [],
            "disputedEvidenceIds": [],
        }
    )
    provider = ScriptedProvider(raw={("advocate", "RIDER"): [json.dumps(payload)]})
    result = _run(ROUTE_CASE, provider)

    assert [claim.claim_id for claim in result.rider.verified_claims] == ["C1"]
    assert [claim.claim_id for claim in result.rider.rejected_claims] == ["C2"]
    assert result.rider.execution.generated_claim_count == 2


def test_handoff_rebuttal_context_carries_only_verified_opposing_claims():
    case, analysis = _load(ROUTE_CASE)
    provider = ScriptedProvider()
    result = _run(ROUTE_CASE, provider)

    advocate_context = AdvocateContextBuilder().build(case, analysis)
    context = RebuttalContextBuilder().build(
        case,
        analysis,
        advocate_context,
        result.rider,
        result.driver,
        "RIDER",
    )
    # The Rider answers the Driver, and only the Driver's *verified* claims.
    assert context.own_side == "RIDER"
    # Targets are namespaced by side, so "D1" from the Driver can never be
    # confused with a "D1" the Rider might have produced.
    assert [c.claim_id for c in context.opposing_verified_claims] == [
        f"DRIVER-{claim.claim_id}" for claim in result.driver.verified_claims
    ]
    # Its own verified claims are carried too, under its own namespace.
    assert [c.claim_id for c in context.own_verified_claims] == [
        f"RIDER-{claim.claim_id}" for claim in result.rider.verified_claims
    ]
    assert context.rebuttal_round == 1
    assert context.max_rebuttal_rounds == 1


def test_handoff_judge_context_excludes_everything_untrusted():
    case, analysis = _load(ROUTE_CASE)
    provider = ScriptedProvider()
    result = _run(ROUTE_CASE, provider)

    advocate_context = AdvocateContextBuilder().build(case, analysis)
    context = JudgeContextBuilder().build(
        case,
        analysis,
        advocate_context,
        result.rider,
        result.driver,
        result.rebuttals,
    )
    serialised = context.model_dump_json()

    # Nothing rejected reaches the Judge, on either side.
    for claim in [*result.rider.rejected_claims, *result.driver.rejected_claims]:
        assert claim.claim_id not in serialised
    for rebuttal in [
        *result.rebuttals.rider.rejected_rebuttals,
        *result.rebuttals.driver.rejected_rebuttals,
    ]:
        assert rebuttal.rebuttal_id not in serialised

    # Nor does the deterministic answer, or anything derived from it. The
    # allowed-outcome vocabulary IS supplied on purpose — the Judge must know
    # which outcomes exist — but nothing may reveal which one code chose.
    assert result.deterministic_resolution.ruling not in serialised
    assert "refundAmount" not in serialised
    assert "confidence" not in serialised.lower()
    assert "escalation" not in serialised.lower()
    assert "recommendedAction" not in serialised
    assert result.deterministic_resolution.explanation not in serialised

    # What it does carry: verified claims from both sides and the trusted base.
    assert context.rider.verified_claim_count == len(result.rider.verified_claims)
    assert context.driver.verified_claim_count == len(result.driver.verified_claims)
    assert context.evidence
    assert context.applicable_policy.rules


def test_handoff_judge_output_is_validated_before_it_is_trusted():
    """An out-of-contract Judge answer becomes FAILED, never a partial accept."""
    provider = ScriptedProvider(raw={("judge", None): ["not json"]})
    result = _run(ROUTE_CASE, provider)

    assert result.workflow.judge.status == "FAILED"
    assert result.judge.recommended_outcome is None
    # The explanation reports no advisory outcome rather than inventing one.
    assert result.explanation.judge_advisory_outcome is None
    assert result.explanation.judge_advisory_differs is False


def test_handoff_explanation_reports_the_advisory_only_when_there_is_one():
    with_judge = _run(ROUTE_CASE, ScriptedProvider())
    without_judge = _run(ROUTE_CASE, ScriptedProvider(fail={("judge", None)}))

    assert with_judge.explanation.judge_advisory_outcome is not None
    assert without_judge.explanation.judge_advisory_outcome is None


# ---------------------------------------------------------------------------
# The workflow object and the flat pipeline must not disagree
# ---------------------------------------------------------------------------


_EXPECTED_PIPELINE = {
    "DETERMINISTIC_ANALYSIS": lambda w: w.deterministic.status,
    "RIDER_ADVOCATE": lambda w: w.advocates.rider.status,
    "DRIVER_ADVOCATE": lambda w: w.advocates.driver.status,
    "RIDER_REBUTTAL": lambda w: w.rebuttals.rider.status,
    "DRIVER_REBUTTAL": lambda w: w.rebuttals.driver.status,
    "JUDGE": lambda w: w.judge.status,
}


@pytest.mark.parametrize(
    "provider",
    [
        ScriptedProvider(),
        ScriptedProvider(fail={("advocate", "RIDER")}),
        ScriptedProvider(fail={("rebuttal", "RIDER")}),
        ScriptedProvider(fail={("judge", None)}),
    ],
    ids=["all-success", "advocate-failed", "rebuttal-failed", "judge-failed"],
)
def test_workflow_state_agrees_with_the_flat_pipeline(provider):
    """The richer state must not contradict the legacy stage list.

    ``pipeline`` is kept for backward compatibility, so the two are projections
    of the same run. If they ever disagree, one of them is lying to a consumer.
    """
    result = _run(ROUTE_CASE, provider)
    workflow = result.workflow
    stages = {item.stage: item.status for item in result.pipeline}

    for stage, read in _EXPECTED_PIPELINE.items():
        if stage not in stages:
            continue
        state = read(workflow)
        legacy = stages[stage]
        if state in {"COMPLETE", "PENDING_HUMAN_REVIEW"}:
            assert legacy == "COMPLETE", f"{stage}: {state} vs {legacy}"
        elif state in {"FAILED", "BLOCKED", "SKIPPED"}:
            # NOT_RUN is the legacy vocabulary's only way to say "did not
            # produce a result", so it must not be reported as COMPLETE.
            assert legacy != "COMPLETE", f"{stage}: {state} vs {legacy}"


def test_a_blocked_stage_is_never_reported_as_complete_anywhere():
    """The specific regression that made the UI claim 8 of 8 steps ready."""
    result = _run(ROUTE_CASE, ScriptedProvider(fail={("advocate", "DRIVER")}))
    workflow = result.workflow

    assert workflow.judge.status == "BLOCKED"
    assert workflow.rebuttals.rider.status == "BLOCKED"
    assert workflow.rebuttals.driver.status == "BLOCKED"

    stages = {item.stage: item.status for item in result.pipeline}
    assert stages["JUDGE"] != "COMPLETE"
    assert stages["RIDER_REBUTTAL"] != "COMPLETE"
    assert stages["DRIVER_REBUTTAL"] != "COMPLETE"
    assert stages["RIDER_ADVOCATE"] == "COMPLETE"
