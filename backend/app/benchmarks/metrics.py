"""Metric derivation and aggregation for Stage 4C.

Two rules govern everything here:

1. **Provider failure and model-output failure never merge.** A 503 says the
   service was down; a rejected claim says the model reasoned badly. Summing
   them into one "error count" would make an outage look like incompetence and
   a hallucination look like downtime.

2. **Unknown is not zero.** A missing token count, an undefined verification
   rate (no claims were generated), and an absent price all stay ``None``.
   Reporting ``0`` for any of them would be a factual claim the data does not
   support.
"""

from __future__ import annotations

import statistics

from app.benchmarks.models import (
    MODEL_OUTPUT_FAILURE_CODES,
    PROVIDER_FAILURE_CODES,
    CallMetrics,
    FailureCategory,
    LatencyStats,
    ModelAggregate,
    RunStatus,
)

# Verifier rejection codes, grouped into the metric buckets Stage 4C reports.
_INVALID_EVIDENCE_REASONS = frozenset(
    {
        "EVIDENCE_ID_NOT_FOUND",
        "EVIDENCE_NOT_IN_CASE",
        "NO_EVIDENCE_REFERENCE",
        "EVIDENCE_NOT_VERIFIED",
    }
)
_INVALID_POLICY_REASONS = frozenset(
    {
        "POLICY_REF_NOT_FOUND",
        "POLICY_NOT_APPLICABLE",
    }
)
_FACT_CONTRADICTION_REASONS = frozenset({"FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS"})
_UNKNOWN_FACT_REASONS = frozenset({"UNKNOWN_ASSERTED_FACT"})


def classify_failure(code: str | None) -> str:
    """Map a failure code onto the side of the boundary it belongs to."""
    if not code:
        return FailureCategory.NONE
    if code in PROVIDER_FAILURE_CODES:
        return FailureCategory.PROVIDER_FAILURE
    if code in MODEL_OUTPUT_FAILURE_CODES:
        return FailureCategory.MODEL_OUTPUT_FAILURE
    # An unrecognised code is a programming error, not something to guess at.
    # Treating it as a model fault would blame the model for our own bug, and
    # treating it as a provider fault would hide it behind an outage story.
    return FailureCategory.INTERNAL_ERROR


def compute_unattributed_tokens(
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
) -> int | None:
    """Tokens the provider counted but did not attribute to input or output.

    Gemini reports a ``total_tokens`` larger than input + output because hidden
    reasoning contributes to the total without appearing in
    ``completion_tokens``. The remainder is recorded under a deliberately
    generic name: the provider does not tell us what those tokens were, so
    calling them "chain-of-thought" would be an assumption, not a measurement.

    Returns ``None`` when any of the three inputs is unknown, because a
    remainder computed from a missing value is meaningless.
    """
    if total_tokens is None or input_tokens is None or output_tokens is None:
        return None
    return max(total_tokens - input_tokens - output_tokens, 0)


def compute_verification_rate(verified: int, generated: int) -> float | None:
    """Verified claims as a share of generated claims.

    ``None`` when nothing was generated: a model that produced no claims has no
    verification rate, and calling that 0.0 would score it the same as a model
    whose every claim was rejected. Those are very different failures.
    """
    if generated <= 0:
        return None
    return verified / generated


def rejection_breakdown(reasons: list[str]) -> dict[str, int]:
    """Bucket verifier rejection reasons into the Stage 4C counters."""
    invalid_evidence = invalid_policy = contradictions = unknown = other = 0
    for reason in reasons:
        if reason in _INVALID_EVIDENCE_REASONS:
            invalid_evidence += 1
        elif reason in _INVALID_POLICY_REASONS:
            invalid_policy += 1
        elif reason in _FACT_CONTRADICTION_REASONS:
            contradictions += 1
        elif reason in _UNKNOWN_FACT_REASONS:
            unknown += 1
        else:
            other += 1
    return {
        "invalid_evidence": invalid_evidence,
        "invalid_policy": invalid_policy,
        "fact_contradiction": contradictions,
        "unknown_fact": unknown,
        "other": other,
    }


def latency_stats(values: list[int]) -> LatencyStats:
    """Summarise latencies, reporting the sample size alongside the mean."""
    present = [value for value in values if value is not None]
    if not present:
        return LatencyStats()
    return LatencyStats(
        sample_size=len(present),
        mean_ms=round(statistics.fmean(present), 2),
        median_ms=round(statistics.median(present), 2),
        min_ms=min(present),
        max_ms=max(present),
    )


def sum_tokens(values: list[int | None]) -> int | None:
    """Sum token counts, preserving "not reported" as ``None`` rather than 0."""
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def aggregate_calls(
    calls: list[CallMetrics],
    *,
    model_name: str,
    provider: str,
    model: str,
    skipped: bool = False,
    skip_reason: str | None = None,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
) -> ModelAggregate:
    """Roll per-call records up into a model-level summary."""
    aggregate = ModelAggregate(
        model_name=model_name,
        provider=provider,
        model=model,
        skipped=skipped,
        skip_reason=skip_reason,
    )

    for call in calls:
        aggregate.total_calls += 1
        if call.status == RunStatus.COMPLETE:
            aggregate.completed_calls += 1
        elif call.status == RunStatus.FAILED:
            aggregate.failed_calls += 1

        if call.failure_category == FailureCategory.PROVIDER_FAILURE:
            aggregate.provider_failure_count += 1
        elif call.failure_category == FailureCategory.MODEL_OUTPUT_FAILURE:
            aggregate.model_output_failure_count += 1
        elif call.failure_category == FailureCategory.INTERNAL_ERROR:
            aggregate.internal_error_count += 1

        if call.failure_code:
            if call.failure_category == FailureCategory.PROVIDER_FAILURE:
                bucket = aggregate.provider_error_counts
            elif call.failure_category == FailureCategory.MODEL_OUTPUT_FAILURE:
                bucket = aggregate.model_output_error_counts
            else:
                bucket = aggregate.internal_error_counts
            bucket[call.failure_code] = bucket.get(call.failure_code, 0) + 1

        if call.failure_code == "SCHEMA_VALIDATION_FAILED":
            aggregate.schema_failure_count += 1
        if call.malformed_output:
            aggregate.malformed_output_count += 1

        aggregate.total_claims += call.generated_claim_count
        aggregate.verified_claims += call.verified_claim_count
        aggregate.rejected_claims += call.rejected_claim_count
        if call.status == RunStatus.COMPLETE and call.generated_claim_count == 0:
            aggregate.calls_with_zero_claims += 1

        breakdown = rejection_breakdown(call.rejection_reasons)
        aggregate.invalid_evidence_refs += breakdown["invalid_evidence"]
        aggregate.invalid_policy_refs += breakdown["invalid_policy"]
        aggregate.fact_contradictions += breakdown["fact_contradiction"]
        aggregate.unknown_facts += breakdown["unknown_fact"]
        aggregate.other_rejections += breakdown["other"]

        for reason in call.rejection_reasons:
            aggregate.rejection_reason_counts[reason] = (
                aggregate.rejection_reason_counts.get(reason, 0) + 1
            )

        if call.recommended_outcome:
            aggregate.recommended_outcomes[call.recommended_outcome] = (
                aggregate.recommended_outcomes.get(call.recommended_outcome, 0) + 1
            )

        if call.attempt_count is not None:
            aggregate.total_attempts += call.attempt_count
        if call.retry_count is not None:
            aggregate.total_retries += call.retry_count

    completed_latencies = [
        call.latency_ms
        for call in calls
        if call.status == RunStatus.COMPLETE and call.latency_ms is not None
    ]
    failed_latencies = [
        call.latency_ms
        for call in calls
        if call.status == RunStatus.FAILED and call.latency_ms is not None
    ]
    # Kept apart on purpose: a request that failed in 900 ms would drag a mean
    # down and make a fast-but-broken model look efficient.
    aggregate.completed_latency = latency_stats(completed_latencies)
    aggregate.failed_latency = latency_stats(failed_latencies)

    aggregate.input_tokens = sum_tokens([call.input_tokens for call in calls])
    aggregate.output_tokens = sum_tokens([call.output_tokens for call in calls])
    aggregate.total_tokens = sum_tokens([call.total_tokens for call in calls])
    aggregate.unattributed_tokens = sum_tokens([call.unattributed_tokens for call in calls])

    aggregate.completion_rate = _rate(aggregate.completed_calls, aggregate.total_calls)
    aggregate.verification_rate = compute_verification_rate(
        aggregate.verified_claims, aggregate.total_claims
    )

    if input_cost_per_million is not None or output_cost_per_million is not None:
        if aggregate.input_tokens is not None or aggregate.output_tokens is not None:
            aggregate.estimated_cost = (
                (aggregate.input_tokens or 0) / 1_000_000 * (input_cost_per_million or 0.0)
                + (aggregate.output_tokens or 0) / 1_000_000 * (output_cost_per_million or 0.0)
            )

    return aggregate
