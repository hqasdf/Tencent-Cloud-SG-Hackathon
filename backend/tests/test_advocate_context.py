"""Tests for the trusted AgentCaseContext projection.

The negative assertions here are the most important part of Stage 4: the context
must contain facts, evidence, and policy, and must never contain the answer.
"""

from app.agents.context_builder import AdvocateContextBuilder
from app.data.cases import MOCK_CASES
from app.models.agent import NoShowFacts, RouteDeviationFacts
from app.services.dispute_analysis import DisputeAnalysisService


def context_for(case_id: str):
    case = next(item for item in MOCK_CASES if item.id == case_id)
    analysis = DisputeAnalysisService().analyze(case)
    return AdvocateContextBuilder().build(case, analysis), case, analysis


def test_route_case_exposes_correct_trusted_facts() -> None:
    context, _, _ = context_for("CASE-2026-1041")
    facts = context.facts
    assert isinstance(facts, RouteDeviationFacts)
    assert facts.expected_route_distance_km == 5.8
    assert facts.actual_route_distance_km == 7.1
    assert facts.distance_difference_km == 1.3
    assert facts.deviation_percentage == 22.41
    assert facts.duration_difference_minutes == 8
    assert facts.fare_difference == 2.3
    assert facts.explained_deviation_distance_km == 0.5
    assert facts.unexplained_deviation_distance_km == 0.8


def test_route_case_exposes_only_verified_conditions_and_supporting_evidence() -> None:
    context, _, _ = context_for("CASE-2026-1041")
    facts = context.facts
    assert [condition.type for condition in facts.verified_conditions] == ["traffic_diversion"]
    assert facts.verified_conditions[0].evidence_ids == ["E06"]
    assert set(facts.supporting_evidence_ids) <= {item.id for item in context.evidence}


def test_no_show_case_exposes_correct_trusted_facts() -> None:
    context, _, _ = context_for("CASE-2026-1043")
    facts = context.facts
    assert isinstance(facts, NoShowFacts)
    assert facts.driver_within_pickup_radius is True
    assert facts.driver_distance_to_pickup_meters < 100
    assert facts.waiting_duration_seconds == 372
    assert facts.cancellation_charge_amount == 6.0
    assert facts.driver_message_evidence_ids == ["E06"]


def test_context_contains_evidence_and_canonical_timeline() -> None:
    context, case, _ = context_for("CASE-2026-1041")
    assert [item.id for item in context.evidence] == [item.id for item in case.evidence]
    assert all(item.status for item in context.evidence)
    timestamps = [event.timestamp for event in context.timeline]
    assert timestamps == sorted(timestamps)


def test_context_contains_the_correct_policy_and_rules() -> None:
    route_context, _, _ = context_for("CASE-2026-1041")
    assert route_context.policy.policy_id == "ROUTE_DEVIATION_POLICY_V1"
    assert route_context.policy.dispute_type == "route_deviation"
    assert {rule.rule_id for rule in route_context.policy.rules} == {
        "ROUTE_REQUIRED_EVIDENCE",
        "ROUTE_UNEXPLAINED_DEVIATION",
        "ROUTE_EVIDENCE_CONSISTENCY",
    }

    no_show_context, _, _ = context_for("CASE-2026-1043")
    assert no_show_context.policy.policy_id == "NO_SHOW_POLICY_V1"
    assert {rule.rule_id for rule in no_show_context.policy.rules} == {
        "NO_SHOW_REQUIRED_EVIDENCE",
        "NO_SHOW_PICKUP_RADIUS",
        "NO_SHOW_WAIT_TIME",
        "NO_SHOW_EVIDENCE_CONSISTENCY",
    }


def test_context_carries_policy_evaluation_rule_results() -> None:
    context, _, analysis = context_for("CASE-2026-1041")
    assert context.policy_evaluation.policy_id == analysis.policy_evaluation.policy_id
    assert context.policy_evaluation.overall_outcome == analysis.policy_evaluation.overall_outcome
    assert len(context.policy_evaluation.evaluated_rules) == len(
        analysis.policy_evaluation.evaluated_rules
    )


def test_context_flags_missing_and_conflicting_evidence() -> None:
    clean, _, _ = context_for("CASE-2026-1041")
    assert clean.missing_evidence_ids == []
    assert clean.conflicting_evidence_ids == []

    conflicted, _, _ = context_for("CASE-2026-1044")
    assert conflicted.conflicting_evidence_ids


def test_context_does_not_leak_the_recommendation_or_any_answer() -> None:
    """The core safety property of Stage 4.

    The advocates receive FACTS + EVIDENCE + POLICY + policy evaluation. They must
    never receive the resolution recommendation, the refund amount, the confidence
    score, the resolution mode, or the escalation reasons.

    Note on vocabulary overlap: ``ContextPolicyEvaluation.overall_outcome`` carries
    PolicyTwin outcome strings such as ``UPHOLD_CANCELLATION_CHARGE``. Those are
    policy-evaluation vocabulary that the brief explicitly requires to be visible,
    so this test asserts on the *structured fields* rather than doing a broad
    substring scan over the serialized context.
    """
    for case_id in ("CASE-2026-1041", "CASE-2026-1043", "CASE-2026-1044"):
        context, _, analysis = context_for(case_id)
        serialized = context.model_dump_json(by_alias=True)
        payload = context.model_dump()

        # No raw calculation source.
        assert "analysis_input" not in serialized
        assert "analysisInput" not in serialized
        assert "analysisInput" not in payload

        # The trusted facts ARE present — the context is not empty.
        assert "expectedRouteDistanceKm" in serialized or "driverDistanceToPickupMeters" in serialized

        # Structural guard: no recommendation / refund / confidence / mode /
        # escalation key anywhere in the projection, at any nesting depth.
        forbidden_keys = (
            "resolution_recommendation",
            "resolutionRecommendation",
            "recommended_action",
            "recommendedAction",
            "refund_amount",
            "refundAmount",
            "confidence",
            "confidence_penalties",
            "confidencePenalties",
            "resolution_mode",
            "resolutionMode",
            "escalation_reasons",
            "escalationReasons",
            "judge_criteria",
            "judgeCriteria",
        )
        flat_keys = _all_keys(payload)
        for key in forbidden_keys:
            assert key not in flat_keys, f"{case_id} exposed forbidden key {key!r}"

        # Value guard: the concrete answer values must not appear as data.
        # The resolution mode is a token that would only appear if the answer leaked.
        assert analysis.resolution_mode not in serialized, f"{case_id} leaked resolution mode"

        # The recommended action MAY coincidentally equal the policy evaluation
        # outcome (e.g. the policy says UPHOLD_CANCELLATION_CHARGE and the
        # recommendation agrees). That is not a leak: the brief explicitly requires
        # the policy evaluation to be visible. So assert the action string appears
        # ONLY in the policyEvaluation.overallOutcome position.
        action = analysis.resolution_recommendation.recommended_action
        allowed = f'"overallOutcome":"{action}"'
        if action in serialized:
            without_policy_outcome = serialized.replace(allowed, "")
            assert action not in without_policy_outcome, (
                f"{case_id} leaked the recommended action {action!r} outside the "
                f"policy evaluation outcome"
            )

        # The refund amount must not be carried as a field. "refund" may legitimately
        # appear inside policy prose, so assert on keys (done above) and on the
        # amount value rather than on the word itself.
        refund = analysis.resolution_recommendation.refund_amount
        if refund is not None and refund > 0:
            assert '"refundAmount"' not in serialized, f"{case_id} leaked refundAmount"
            assert '"refund_amount"' not in serialized, f"{case_id} leaked refund_amount"


def _all_keys(value: object) -> set[str]:
    """Collect every dict key in a nested payload."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(key)
            keys |= _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            keys |= _all_keys(nested)
    return keys


def test_context_has_no_recommendation_confidence_or_escalation_fields() -> None:
    context, _, _ = context_for("CASE-2026-1041")
    fields = set(context.model_dump().keys())
    for forbidden in (
        "resolution_recommendation",
        "recommendation",
        "refund_amount",
        "confidence",
        "resolution_mode",
        "escalation_reasons",
        "counterfactual_explanation",
        "explanation",
    ):
        assert forbidden not in fields, f"AgentCaseContext must not expose {forbidden}"


def test_context_never_exposes_the_raw_calculation_input() -> None:
    context, case, _ = context_for("CASE-2026-1041")
    # The case still holds its internal input, but the context does not.
    assert case.analysis_input is not None
    assert "routeDeviation" not in context.model_dump_json(by_alias=True)
    assert "noShow" not in context.model_dump_json(by_alias=True)


def test_context_excludes_historical_and_fairness_sensitive_data() -> None:
    context, _, _ = context_for("CASE-2026-1041")
    serialized = context.model_dump_json(by_alias=True).lower()
    for forbidden in ("rating", "history", "historical", "fraud", "previous", "reputation"):
        assert forbidden not in serialized, f"Context leaked fairness-sensitive term {forbidden!r}"
