"""Stage 4C benchmark tests — entirely offline.

No live Gemini call and no live Ollama call is made anywhere in this file. The
benchmark's ``provider_factory`` seam is what makes that possible: the same code
path that talks to a real model is driven by a stub instead.

The stub is deliberately not a simplified stand-in for the pipeline. It only
replaces the *transport*; prompt assembly, response parsing, Pydantic
validation, and claim verification are all the real production classes.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProviderError,
    ProviderErrorCode,
)
from app.benchmarks.cases import (
    NO_SHOW_CRITERION,
    ROUTE_CRITERION,
    BenchmarkCaseSelectionError,
    evaluate_criterion,
    select_cases,
    select_profile_cases,
)
from app.benchmarks.metrics import (
    aggregate_calls,
    classify_failure,
    compute_unattributed_tokens,
    compute_verification_rate,
    latency_stats,
    rejection_breakdown,
    sum_tokens,
)
from app.benchmarks.models import (
    MODEL_CATALOGUE,
    BenchmarkConfigurationError,
    BenchmarkModelConfig,
    CallMetrics,
    FailureCategory,
    RunStatus,
    SkipReason,
    resolve_model_configs,
)
from app.benchmarks.reporting import (
    BenchmarkSecretLeakError,
    find_secret_leaks,
    redact,
    render_markdown,
    write_results,
)
from app.benchmarks.runner import (
    build_plan,
    check_model_availability,
    execute_plan,
    render_plan,
)

FAKE_KEY = "bench_fake_key_do_not_use_1a2b3c4d"


# ---------------------------------------------------------------------------
# Stub transport
# ---------------------------------------------------------------------------


def _grounding(context: dict, side: str) -> tuple[list[str], str, list[dict]]:
    """The real evidence ids, policy rule id, and true fact for *this* call.

    Read from the live context rather than hardcoded, so a test that injects one
    defect is measuring that defect and not an accidental second one.
    """
    facts = context["facts"]
    if facts["analysisType"] == "route_deviation":
        asserted = [{"fact": "DEVIATION_PERCENTAGE", "value": facts["deviationPercentage"]}]
    else:
        asserted = [{"fact": "WAITING_DURATION_SECONDS", "value": facts["waitingDurationSeconds"]}]

    verified_evidence = [
        item["id"] for item in context["evidence"] if item.get("status") == "Verified"
    ] or [context["evidence"][0]["id"]]
    rule_id = context["policy"]["rules"][0]["ruleId"]
    return verified_evidence, rule_id, asserted


def _payload(
    context: dict,
    side: str,
    *,
    claim_count: int = 1,
    evidence_ids: list[str] | None = None,
    policy_refs: list[str] | None = None,
    asserted_facts: list[dict] | None = None,
) -> dict:
    """A schema-valid advocate response, optionally with one defect injected.

    Everything not explicitly overridden comes from the real per-call context.
    The side is always the side actually being asked, so a payload reused across
    both advocates does not fail the *other* one with ``WRONG_SIDE``.
    """
    verified_evidence, rule_id, true_facts = _grounding(context, side)
    resolved_evidence = verified_evidence[:1] if evidence_ids is None else evidence_ids
    resolved_policy = [rule_id] if policy_refs is None else policy_refs
    resolved_facts = true_facts if asserted_facts is None else asserted_facts

    claims = [
        {
            "claimId": f"C{index + 1}",
            "claim": f"{side} argument number {index + 1}.",
            "evidenceIds": list(resolved_evidence),
            "policyRefs": list(resolved_policy),
            "reasoningSummary": "Grounded in the trusted context.",
            "importance": "HIGH",
            "assertedFacts": [dict(fact) for fact in resolved_facts],
            "disputedEvidenceIds": [],
        }
        for index in range(claim_count)
    ]

    return {
        "side": side,
        "summary": f"{side} summary.",
        "claims": claims,
        "requestedOutcome": "NO_REFUND" if side == "DRIVER" else "PARTIAL_REFUND",
        "contextAcknowledged": True,
    }


def _grounded_payload(context: dict, side: str, claim_count: int = 1) -> dict:
    """Build a schema-valid advocate response grounded in the trusted context."""
    return _payload(context, side, claim_count=claim_count)


class _StubProvider:
    """Transport stub. Replaces HTTP only, nothing else."""

    name = "stub"

    def __init__(
        self,
        *,
        claim_count: int = 1,
        fail_with: LlmProviderError | None = None,
        raw_text: str | Callable[[dict, str], str] | None = None,
        attempt_count: int = 1,
        input_tokens: int | None = 100,
        output_tokens: int | None = 20,
        total_tokens: int | None = 150,
    ) -> None:
        self._claim_count = claim_count
        self._fail_with = fail_with
        self._raw_text = raw_text
        self._attempt_count = attempt_count
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._total_tokens = total_tokens
        self.requests: list[AgentCompletionRequest] = []

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.requests.append(request)
        if self._fail_with is not None:
            raise self._fail_with
        context = json.loads(request.metadata["context_json"])
        side = request.metadata["side"]
        text = self._raw_text
        if callable(text):
            # A callable sees the real context and the real side, so a defective
            # payload can be built per call instead of being replayed verbatim
            # into a call it was never written for.
            text = text(context, side)
        if text is None:
            text = json.dumps(_grounded_payload(context, side, self._claim_count))
        return LlmCompletion(
            raw_text=text,
            provider_name=self.name,
            model_name="stub-model",
            duration_ms=42,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._total_tokens,
            finish_reason="stop",
            attempt_count=self._attempt_count,
        )


def _test_config(**overrides: object) -> BenchmarkModelConfig:
    base: dict[str, object] = {
        "name": "stub-model",
        "provider": "openai_compatible",
        "model": "stub-model",
        "base_url": "https://stub.invalid/v1",
        "api_key_env": "BENCHMARK_TEST_API_KEY",
        "placeholder_api_key": FAKE_KEY,
        "max_retries": 0,
    }
    base.update(overrides)
    return BenchmarkModelConfig(**base)  # type: ignore[arg-type]


def _run_with(
    provider: _StubProvider,
    *,
    config: BenchmarkModelConfig | None = None,
    profile: str = "smoke",
    runs: int = 1,
):
    model_config = config or _test_config()
    plan = build_plan(profile=profile, model_configs=[model_config], runs=runs)
    return execute_plan(
        plan,
        provider_factory=lambda _config: provider,
        availability_checker=lambda _config: (True, None),
    )


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------


def test_the_catalogue_contains_no_plaintext_secret() -> None:
    """A config may name an env var; it must never carry a credential."""
    for key, config in MODEL_CATALOGUE.items():
        assert config.api_key_env, key
        assert config.placeholder_api_key in (None, "ollama"), key


def test_resolving_an_unknown_model_fails_loudly() -> None:
    with pytest.raises(BenchmarkConfigurationError):
        resolve_model_configs(["not-a-real-model"])


def test_resolving_duplicate_names_yields_one_config() -> None:
    resolved = resolve_model_configs(["gemini", "gemini-3.8-flash"])
    assert len(resolved) == 1


def test_describe_never_contains_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", FAKE_KEY)
    description = json.dumps(_test_config().describe())
    assert FAKE_KEY not in description
    assert "BENCHMARK_TEST_API_KEY" in description


def test_local_model_does_not_borrow_the_cloud_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local server must not be handed the real cloud key."""
    monkeypatch.setenv("AGENT_API_KEY", FAKE_KEY)
    qwen = MODEL_CATALOGUE["qwen"]
    assert qwen.api_key_env != "AGENT_API_KEY"
    assert qwen.resolve_api_key() == "ollama"


def test_a_placeholder_key_is_not_treated_as_a_secret_to_scan_for() -> None:
    from app.benchmarks.reporting import collect_secret_values

    # "ollama" is 6 characters, below the scan floor, so it cannot match
    # everywhere and produce a meaningless redaction.
    assert collect_secret_values([MODEL_CATALOGUE["qwen"]]) == []


# ---------------------------------------------------------------------------
# Semantic case selection
# ---------------------------------------------------------------------------


def test_route_criterion_matches_only_clean_fully_explained_cases() -> None:
    matched = evaluate_criterion(ROUTE_CRITERION)
    assert matched == ["DISP-005", "DISP-008"]


def test_no_show_criterion_matches_only_clean_chargeable_cases() -> None:
    matched = evaluate_criterion(NO_SHOW_CRITERION)
    assert matched == ["DISP-002", "DISP-004"]


def test_selection_is_not_based_on_a_hardcoded_case_id() -> None:
    """The criterion must be a property, so it survives a fixture rewrite."""
    assert "DISP-005" not in ROUTE_CRITERION.description
    assert "route" in ROUTE_CRITERION.description


def test_smoke_selection_takes_one_case_per_type_and_states_the_tie_break() -> None:
    selection = select_cases(ROUTE_CRITERION, limit=1)
    assert selection.selected == ["DISP-005"]
    assert selection.candidates == ["DISP-005", "DISP-008"]
    assert selection.tie_break is not None
    assert "2 cases matched" in selection.tie_break


def test_requiring_uniqueness_surfaces_the_ambiguity() -> None:
    """Two equivalent fixtures exist; asking for one must fail, not guess."""
    with pytest.raises(BenchmarkCaseSelectionError) as caught:
        select_cases(ROUTE_CRITERION, require_unique=True)
    assert "DISP-005" in str(caught.value)
    assert "DISP-008" in str(caught.value)


def test_a_criterion_matching_nothing_fails_loudly() -> None:
    from app.benchmarks.cases import CaseCriterion

    empty = CaseCriterion(
        key="impossible",
        dispute_type="route_deviation",
        description="never true",
        predicate=lambda _case, _analysis: False,
    )
    with pytest.raises(BenchmarkCaseSelectionError):
        select_cases(empty)


def test_full_profile_selects_every_qualifying_case() -> None:
    selections = select_profile_cases("full")
    selected = [case_id for selection in selections for case_id in selection.selected]
    assert selected == ["DISP-005", "DISP-008", "DISP-002", "DISP-004"]
    assert all(selection.tie_break is None for selection in selections)


# ---------------------------------------------------------------------------
# Planning and expected call count
# ---------------------------------------------------------------------------


def test_smoke_plan_expects_four_calls_per_model() -> None:
    plan = build_plan(profile="smoke", model_configs=[_test_config()], runs=1)
    assert plan.cases_per_model == 2
    assert plan.calls_per_case == 2
    assert plan.expected_calls_per_model == 4
    assert plan.total_expected_calls == 4


def test_runs_multiply_the_expected_call_count() -> None:
    plan = build_plan(profile="smoke", model_configs=[_test_config()], runs=3)
    assert plan.expected_calls_per_model == 12


def test_multiple_models_multiply_the_total_only() -> None:
    plan = build_plan(
        profile="smoke",
        model_configs=[_test_config(name="a"), _test_config(name="b")],
        runs=1,
    )
    assert plan.expected_calls_per_model == 4
    assert plan.total_expected_calls == 8


def test_full_plan_expects_more_calls_than_smoke() -> None:
    smoke = build_plan(profile="smoke", model_configs=[_test_config()], runs=1)
    full = build_plan(profile="full", model_configs=[_test_config()], runs=1)
    assert full.expected_calls_per_model == 8
    assert full.expected_calls_per_model > smoke.expected_calls_per_model


def test_an_unknown_profile_is_rejected() -> None:
    with pytest.raises(BenchmarkConfigurationError):
        build_plan(profile="enormous", model_configs=[_test_config()])


def test_zero_runs_is_rejected() -> None:
    with pytest.raises(BenchmarkConfigurationError):
        build_plan(profile="smoke", model_configs=[_test_config()], runs=0)


def test_the_rendered_plan_states_the_expected_call_count() -> None:
    plan = build_plan(profile="smoke", model_configs=[_test_config()], runs=1)
    rendered = render_plan(plan)
    assert "EXPECTED API CALLS: 4" in rendered
    assert "NO API CALLS MADE" in rendered


def test_the_rendered_plan_never_contains_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", FAKE_KEY)
    plan = build_plan(profile="smoke", model_configs=[_test_config()], runs=1)
    assert FAKE_KEY not in render_plan(plan)


# ---------------------------------------------------------------------------
# Failure classification: provider vs model output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    ["RATE_LIMITED", "SERVER_ERROR", "TIMEOUT", "NETWORK_ERROR", "AUTHENTICATION_FAILED"],
)
def test_transport_codes_are_provider_failures(code: str) -> None:
    assert classify_failure(code) == FailureCategory.PROVIDER_FAILURE


@pytest.mark.parametrize(
    "code",
    ["MALFORMED_JSON", "SCHEMA_VALIDATION_FAILED", "OUTPUT_TRUNCATED", "WRONG_SIDE"],
)
def test_unusable_output_codes_are_model_failures(code: str) -> None:
    assert classify_failure(code) == FailureCategory.MODEL_OUTPUT_FAILURE


def test_a_missing_code_is_not_a_failure() -> None:
    assert classify_failure(None) == FailureCategory.NONE


def test_an_unrecognised_code_is_an_internal_error_not_a_model_fault() -> None:
    """Guessing here would invent a quality problem that does not exist."""
    assert classify_failure("SOME_NEW_CODE_WE_FORGOT") == FailureCategory.INTERNAL_ERROR


def test_a_503_is_never_counted_as_a_hallucination() -> None:
    provider_error = LlmProviderError(
        "down", provider="stub", code=ProviderErrorCode.SERVER_ERROR
    )
    result = _run_with(_StubProvider(fail_with=provider_error))
    aggregate = result.aggregates[0]
    assert aggregate.provider_failure_count == 4
    assert aggregate.model_output_failure_count == 0
    assert aggregate.fact_contradictions == 0
    assert aggregate.invalid_evidence_refs == 0


def test_malformed_output_is_never_counted_as_provider_downtime() -> None:
    result = _run_with(_StubProvider(raw_text="this is not json at all"))
    aggregate = result.aggregates[0]
    assert aggregate.model_output_failure_count == 4
    assert aggregate.provider_failure_count == 0
    assert aggregate.malformed_output_count == 4
    assert aggregate.completed_calls == 0


def test_schema_failure_is_counted_separately_from_malformed_json() -> None:
    result = _run_with(_StubProvider(raw_text='{"side": "RIDER"}'))
    aggregate = result.aggregates[0]
    assert aggregate.schema_failure_count == 4
    assert aggregate.malformed_output_count == 4
    assert aggregate.model_output_failure_count == 4


# ---------------------------------------------------------------------------
# Metric derivation
# ---------------------------------------------------------------------------


def test_unattributed_tokens_capture_thinking_tokens() -> None:
    """Gemini's total exceeds input+output; the remainder must be visible."""
    assert compute_unattributed_tokens(4462, 650, 6352) == 1240


def test_unattributed_tokens_never_go_negative() -> None:
    assert compute_unattributed_tokens(100, 100, 150) == 0


def test_unattributed_tokens_are_unknown_when_any_input_is_unknown() -> None:
    assert compute_unattributed_tokens(None, 10, 20) is None
    assert compute_unattributed_tokens(10, None, 20) is None
    assert compute_unattributed_tokens(10, 10, None) is None


def test_verification_rate_is_computed_from_generated_claims() -> None:
    assert compute_verification_rate(3, 4) == 0.75


def test_verification_rate_is_undefined_with_no_claims() -> None:
    """Zero claims is not the same as every claim rejected."""
    assert compute_verification_rate(0, 0) is None


def test_latency_stats_report_their_sample_size() -> None:
    stats = latency_stats([100, 200, 300])
    assert stats.sample_size == 3
    assert stats.median_ms == 200
    assert stats.min_ms == 100
    assert stats.max_ms == 300


def test_latency_stats_of_nothing_are_empty_not_zero() -> None:
    stats = latency_stats([])
    assert stats.sample_size == 0
    assert stats.mean_ms is None


def test_sum_tokens_preserves_unknown() -> None:
    assert sum_tokens([None, None]) is None
    assert sum_tokens([None, 5]) == 5


@pytest.mark.parametrize(
    ("reason", "bucket"),
    [
        ("EVIDENCE_ID_NOT_FOUND", "invalid_evidence"),
        ("POLICY_REF_NOT_FOUND", "invalid_policy"),
        ("FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS", "fact_contradiction"),
        ("UNKNOWN_ASSERTED_FACT", "unknown_fact"),
        ("DUPLICATE_CLAIM_ID", "other"),
    ],
)
def test_rejection_reasons_map_to_the_right_bucket(reason: str, bucket: str) -> None:
    assert rejection_breakdown([reason])[bucket] == 1


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _call(**overrides: object) -> CallMetrics:
    """A per-call record shaped the way the runner actually produces one.

    ``failure_category`` is derived from ``failure_code`` exactly as
    ``_run_call`` derives it, so an aggregate test cannot accidentally exercise
    a state production never emits (a failure code with no category, which
    silently drops the error out of every bucket).
    """
    base: dict[str, object] = {
        "benchmark_run_id": "bench-test",
        "timestamp": "2026-09-28T00:00:00+00:00",
        "case_id": "DISP-005",
        "dispute_type": "route_deviation",
        "side": "RIDER",
        "model_name": "stub-model",
        "provider": "stub",
        "model": "stub-model",
        "status": RunStatus.COMPLETE,
    }
    base.update(overrides)
    if "failure_category" not in overrides:
        base["failure_category"] = classify_failure(base.get("failure_code"))  # type: ignore[arg-type]
    return CallMetrics(**base)  # type: ignore[arg-type]


def test_aggregate_separates_completed_and_failed_latency() -> None:
    """A fast failure must not make a model look efficient."""
    aggregate = aggregate_calls(
        [
            _call(status=RunStatus.COMPLETE, latency_ms=10_000),
            _call(status=RunStatus.FAILED, latency_ms=500, failure_code="SERVER_ERROR"),
        ],
        model_name="stub-model",
        provider="stub",
        model="stub-model",
    )
    assert aggregate.completed_latency.mean_ms == 10_000
    assert aggregate.failed_latency.mean_ms == 500
    assert aggregate.completed_latency.sample_size == 1
    assert aggregate.failed_latency.sample_size == 1


def test_aggregate_keeps_failures_visible() -> None:
    aggregate = aggregate_calls(
        [
            _call(),
            _call(status=RunStatus.FAILED, failure_code="RATE_LIMITED"),
            _call(status=RunStatus.FAILED, failure_code="MALFORMED_JSON"),
        ],
        model_name="stub-model",
        provider="stub",
        model="stub-model",
    )
    assert aggregate.total_calls == 3
    assert aggregate.completed_calls == 1
    assert aggregate.failed_calls == 2
    assert aggregate.completion_rate == pytest.approx(1 / 3)


def test_aggregate_counts_zero_claim_calls() -> None:
    aggregate = aggregate_calls(
        [_call(generated_claim_count=0), _call(generated_claim_count=2, verified_claim_count=2)],
        model_name="stub-model",
        provider="stub",
        model="stub-model",
    )
    assert aggregate.calls_with_zero_claims == 1
    assert aggregate.verification_rate == 1.0


def test_aggregate_reports_attempts_and_retries() -> None:
    aggregate = aggregate_calls(
        [_call(attempt_count=1, retry_count=0), _call(attempt_count=3, retry_count=2)],
        model_name="stub-model",
        provider="stub",
        model="stub-model",
    )
    assert aggregate.total_attempts == 4
    assert aggregate.total_retries == 2


def test_aggregate_groups_provider_errors_by_code() -> None:
    aggregate = aggregate_calls(
        [
            _call(status=RunStatus.FAILED, failure_code="RATE_LIMITED"),
            _call(status=RunStatus.FAILED, failure_code="RATE_LIMITED"),
            _call(status=RunStatus.FAILED, failure_code="TIMEOUT"),
        ],
        model_name="stub-model",
        provider="stub",
        model="stub-model",
    )
    assert aggregate.provider_error_counts == {"RATE_LIMITED": 2, "TIMEOUT": 1}


def test_aggregate_records_recommended_outcomes_without_penalising_disagreement() -> None:
    aggregate = aggregate_calls(
        [_call(recommended_outcome="PARTIAL_REFUND"), _call(recommended_outcome="NO_REFUND")],
        model_name="stub-model",
        provider="stub",
        model="stub-model",
    )
    assert aggregate.recommended_outcomes == {"PARTIAL_REFUND": 1, "NO_REFUND": 1}


def test_an_empty_aggregate_has_no_completion_rate() -> None:
    """A skipped model has no rate; 0% would read as total failure."""
    aggregate = aggregate_calls([], model_name="x", provider="stub", model="x")
    assert aggregate.total_calls == 0
    assert aggregate.completion_rate is None
    assert aggregate.verification_rate is None


# ---------------------------------------------------------------------------
# Execution through the real pipeline
# ---------------------------------------------------------------------------


def test_execute_refuses_live_calls_without_explicit_permission() -> None:
    plan = build_plan(profile="smoke", model_configs=[_test_config()], runs=1)
    with pytest.raises(BenchmarkConfigurationError) as caught:
        execute_plan(plan)
    assert "--execute" in str(caught.value)


def test_a_smoke_run_makes_exactly_four_calls() -> None:
    provider = _StubProvider(claim_count=2)
    result = _run_with(provider)
    assert len(result.calls) == 4
    assert len(provider.requests) == 4


def test_each_call_uses_the_real_context_and_prompt() -> None:
    """The benchmark must exercise the production prompt, not a stand-in."""
    provider = _StubProvider()
    _run_with(provider)
    for request in provider.requests:
        assert "trusted case context" in request.user_prompt
        assert "Required output schema" in request.system_prompt
        assert "context_json" in request.metadata
        context = json.loads(request.metadata["context_json"])
        assert context["caseId"] in {"DISP-005", "DISP-002"}


def test_both_sides_are_exercised_for_every_case() -> None:
    provider = _StubProvider()
    result = _run_with(provider)
    pairs = {(call.case_id, call.side) for call in result.calls}
    assert pairs == {
        ("DISP-005", "RIDER"),
        ("DISP-005", "DRIVER"),
        ("DISP-002", "RIDER"),
        ("DISP-002", "DRIVER"),
    }


def test_repetitions_produce_the_expected_number_of_calls() -> None:
    provider = _StubProvider()
    result = _run_with(provider, runs=3)
    assert len(result.calls) == 12


def test_a_grounded_run_verifies_its_claims() -> None:
    result = _run_with(_StubProvider(claim_count=2))
    aggregate = result.aggregates[0]
    assert aggregate.completed_calls == 4
    assert aggregate.total_claims == 8
    assert aggregate.verified_claims == 8
    assert aggregate.verification_rate == 1.0


def test_a_hallucinated_evidence_id_is_rejected_by_the_real_verifier() -> None:
    """The benchmark must inherit the verifier's strictness, not bypass it.

    The only injected defect is the evidence id; the policy reference, the
    asserted fact, and the side all come from the live context, so the rejection
    can only be attributed to the invented citation.
    """
    result = _run_with(
        _StubProvider(
            raw_text=lambda context, side: json.dumps(
                _payload(context, side, evidence_ids=["E99"])
            )
        )
    )
    aggregate = result.aggregates[0]
    assert aggregate.completed_calls == 4
    assert aggregate.verified_claims == 0
    assert aggregate.rejected_claims == 4
    assert aggregate.invalid_evidence_refs == 4
    assert aggregate.rejection_reason_counts == {"EVIDENCE_ID_NOT_FOUND": 4}


def test_cross_domain_fact_mistakes_are_counted() -> None:
    """A no-show fact asserted in a route case is a distinct failure mode.

    Both cases are driven with the same claim. The route case has no
    ``WAITING_DURATION_SECONDS`` fact at all, so it is rejected as unknown; the
    no-show case has it with exactly the asserted value, so it stands. That
    asymmetry is the point — the same text is a hallucination in one domain and
    a correct citation in the other.
    """
    result = _run_with(
        _StubProvider(
            raw_text=lambda context, side: json.dumps(
                _payload(
                    context,
                    side,
                    asserted_facts=[{"fact": "WAITING_DURATION_SECONDS", "value": 480}],
                )
            )
        )
    )
    aggregate = result.aggregates[0]

    route_calls = [call for call in result.calls if call.case_id == "DISP-005"]
    no_show_calls = [call for call in result.calls if call.case_id == "DISP-002"]

    assert len(route_calls) == 2
    assert all(call.unknown_fact_count == 1 for call in route_calls)
    assert all(call.rejected_claim_count == 1 for call in route_calls)

    assert len(no_show_calls) == 2
    assert all(call.unknown_fact_count == 0 for call in no_show_calls)
    assert all(call.verified_claim_count == 1 for call in no_show_calls)

    assert aggregate.unknown_facts == 2
    assert aggregate.rejection_reason_counts == {"UNKNOWN_ASSERTED_FACT": 2}


def test_per_call_rejection_counters_agree_with_the_aggregate() -> None:
    """The raw per-call record must not report 0 for a rejection that happened.

    The aggregate derives its counters from ``rejection_reasons`` and can
    therefore summarise any record; the per-call counters are a deliverable of
    their own. If the two paths ever disagree, the JSON output is lying about
    one of them.
    """
    result = _run_with(
        _StubProvider(
            raw_text=lambda context, side: json.dumps(
                _payload(context, side, evidence_ids=["E99"])
            )
        )
    )
    aggregate = result.aggregates[0]
    calls = result.calls

    assert sum(call.rejected_claim_count for call in calls) == aggregate.rejected_claims
    assert (
        sum(call.invalid_evidence_reference_count for call in calls)
        == aggregate.invalid_evidence_refs
    )
    assert sum(call.invalid_policy_reference_count for call in calls) == aggregate.invalid_policy_refs
    assert sum(call.fact_contradiction_count for call in calls) == aggregate.fact_contradictions
    assert sum(call.unknown_fact_count for call in calls) == aggregate.unknown_facts
    assert sum(call.other_rejection_count for call in calls) == aggregate.other_rejections
    assert sum(call.invalid_evidence_reference_count for call in calls) == 4


def test_token_metrics_flow_through_to_the_aggregate() -> None:
    result = _run_with(
        _StubProvider(input_tokens=4462, output_tokens=650, total_tokens=6352)
    )
    aggregate = result.aggregates[0]
    assert aggregate.input_tokens == 4462 * 4
    assert aggregate.output_tokens == 650 * 4
    assert aggregate.total_tokens == 6352 * 4
    assert aggregate.unattributed_tokens == 1240 * 4


def test_unreported_tokens_stay_unknown_in_the_aggregate() -> None:
    result = _run_with(_StubProvider(input_tokens=None, output_tokens=None, total_tokens=None))
    aggregate = result.aggregates[0]
    assert aggregate.input_tokens is None
    assert aggregate.total_tokens is None
    assert aggregate.unattributed_tokens is None


def test_attempt_counts_are_recorded_from_the_completion() -> None:
    result = _run_with(_StubProvider(attempt_count=3))
    aggregate = result.aggregates[0]
    assert aggregate.total_attempts == 12
    assert aggregate.total_retries == 8


def test_a_skipped_model_produces_no_calls_and_is_not_a_failure() -> None:
    plan = build_plan(profile="smoke", model_configs=[_test_config()], runs=1)
    result = execute_plan(
        plan,
        provider_factory=lambda _config: _StubProvider(),
        availability_checker=lambda _config: (False, SkipReason.LOCAL_MODEL_UNAVAILABLE),
    )
    assert result.calls == []
    aggregate = result.aggregates[0]
    assert aggregate.skipped is True
    assert aggregate.skip_reason == SkipReason.LOCAL_MODEL_UNAVAILABLE
    assert aggregate.total_calls == 0
    assert aggregate.failed_calls == 0


def test_a_missing_credential_skips_rather_than_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHMARK_TEST_API_KEY", raising=False)
    config = _test_config(placeholder_api_key=None)
    available, reason = check_model_availability(config)
    assert available is False
    assert reason == SkipReason.CREDENTIAL_REQUIRED


def test_an_unreachable_local_model_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_settings: object) -> object:
        raise LlmProviderError(
            "unreachable", provider="openai_compatible", code=ProviderErrorCode.NETWORK_ERROR
        )

    monkeypatch.setattr("app.benchmarks.runner.get_provider", _boom)
    available, reason = check_model_availability(MODEL_CATALOGUE["qwen"])
    assert available is False
    assert reason == SkipReason.LOCAL_MODEL_UNAVAILABLE


def test_one_skipped_model_does_not_stop_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud and local results must remain independently obtainable."""
    plan = build_plan(
        profile="smoke",
        model_configs=[_test_config(name="cloud"), _test_config(name="local")],
        runs=1,
    )

    def checker(config: BenchmarkModelConfig) -> tuple[bool, str | None]:
        if config.name == "local":
            return False, SkipReason.LOCAL_MODEL_UNAVAILABLE
        return True, None

    result = execute_plan(
        plan,
        provider_factory=lambda _config: _StubProvider(),
        availability_checker=checker,
    )
    assert len(result.calls) == 4
    by_name = {aggregate.model_name: aggregate for aggregate in result.aggregates}
    assert by_name["cloud"].skipped is False
    assert by_name["cloud"].completed_calls == 4
    assert by_name["local"].skipped is True


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def test_cost_is_unknown_when_no_pricing_is_configured() -> None:
    result = _run_with(_StubProvider())
    assert result.aggregates[0].estimated_cost is None


def test_cost_is_estimated_when_pricing_is_supplied() -> None:
    config = _test_config(input_cost_per_million=1.0, output_cost_per_million=2.0)
    result = _run_with(_StubProvider(input_tokens=1_000_000, output_tokens=1_000_000), config=config)
    # 4 calls x 1M input at $1/M + 4 calls x 1M output at $2/M
    assert result.aggregates[0].estimated_cost == pytest.approx(12.0)


def test_cost_estimate_is_none_when_usage_is_unreported() -> None:
    config = _test_config(input_cost_per_million=1.0, output_cost_per_million=1.0)
    result = _run_with(
        _StubProvider(input_tokens=None, output_tokens=None, total_tokens=None), config=config
    )
    assert result.aggregates[0].estimated_cost is None


def test_model_config_cost_returns_none_without_pricing() -> None:
    assert _test_config().estimate_cost(1000, 1000) is None


# ---------------------------------------------------------------------------
# Serialisation and secret redaction
# ---------------------------------------------------------------------------


def test_results_serialize_to_json_cleanly() -> None:
    result = _run_with(_StubProvider())
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["profile"] == "smoke"
    assert len(payload["calls"]) == 4
    assert len(payload["aggregates"]) == 1
    assert payload["aggregates"][0]["modelName"] == "stub-model"


def test_result_files_are_written_and_named_by_timestamp(tmp_path) -> None:
    result = _run_with(_StubProvider())
    json_path, markdown_path = write_results(result, output_dir=tmp_path)
    assert json_path.exists()
    assert markdown_path.exists()
    assert json_path.name.startswith("benchmark_")
    assert json_path.name.endswith(".json")
    assert markdown_path.name.endswith(".md")


def test_written_json_contains_no_credential(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", FAKE_KEY)
    config = _test_config()
    plan = build_plan(profile="smoke", model_configs=[config], runs=1)
    result = execute_plan(
        plan,
        provider_factory=lambda _config: _StubProvider(),
        availability_checker=lambda _config: (True, None),
    )
    from app.benchmarks.reporting import collect_secret_values

    json_path, markdown_path = write_results(
        result, output_dir=tmp_path, secret_values=collect_secret_values([config])
    )
    assert FAKE_KEY not in json_path.read_text(encoding="utf-8")
    assert FAKE_KEY not in markdown_path.read_text(encoding="utf-8")


def test_a_leaked_credential_blocks_the_write(tmp_path) -> None:
    """The write is refused rather than sanitised silently."""
    result = _run_with(_StubProvider())
    result.calls[0].recommended_outcome = FAKE_KEY
    with pytest.raises(BenchmarkSecretLeakError):
        write_results(result, output_dir=tmp_path, secret_values=[FAKE_KEY])
    assert not list(tmp_path.glob("*.json"))


def test_leak_detection_never_echoes_the_secret() -> None:
    leaks = find_secret_leaks(f"token={FAKE_KEY}", [FAKE_KEY])
    assert leaks
    assert FAKE_KEY not in json.dumps(leaks)


def test_redaction_replaces_the_secret() -> None:
    assert FAKE_KEY not in redact(f"token={FAKE_KEY}", [FAKE_KEY])


def test_short_strings_are_not_treated_as_secrets() -> None:
    assert find_secret_leaks("ollama is running", ["ollama"]) == []


def test_markdown_summary_states_the_limitations() -> None:
    result = _run_with(_StubProvider())
    markdown = render_markdown(result)
    assert "No aggregate quality score is computed" in markdown
    assert "No model is ranked" in markdown
    assert "A skipped model is not a worse model" in markdown


def test_markdown_reports_a_skipped_model_rather_than_omitting_it() -> None:
    plan = build_plan(profile="smoke", model_configs=[_test_config()], runs=1)
    result = execute_plan(
        plan,
        provider_factory=lambda _config: _StubProvider(),
        availability_checker=lambda _config: (False, SkipReason.LOCAL_MODEL_UNAVAILABLE),
    )
    markdown = render_markdown(result)
    assert "LOCAL_MODEL_UNAVAILABLE" in markdown
    assert "No calls were made" in markdown


# ---------------------------------------------------------------------------
# CLI gating
# ---------------------------------------------------------------------------


def test_the_cli_defaults_to_plan_only(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from app.benchmarks.advocate_benchmark import main

    def _no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the dry run attempted network access")

    monkeypatch.setattr(httpx, "Client", _no_network)
    monkeypatch.setattr(httpx, "post", _no_network)
    monkeypatch.setattr(httpx, "get", _no_network)

    code = main(["--profile", "smoke", "--models", "gemini"])
    captured = capsys.readouterr()
    assert code == 0
    assert "PLAN ONLY" in captured.out
    assert "EXPECTED API CALLS: 4" in captured.out


def test_the_cli_rejects_an_unknown_model(capsys) -> None:
    from app.benchmarks.advocate_benchmark import main

    code = main(["--profile", "smoke", "--models", "gpt-9-turbo"])
    assert code == 2
    assert "Unknown benchmark model" in capsys.readouterr().err


def test_the_cli_rejects_an_unknown_profile() -> None:
    from app.benchmarks.advocate_benchmark import main

    with pytest.raises(SystemExit):
        main(["--profile", "gigantic"])
