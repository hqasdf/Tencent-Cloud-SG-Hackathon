"""Benchmark model configuration and result contracts.

A benchmark model config is *not* the application's ``AgentSettings``. The app
runs one configured provider; the benchmark needs to point at several models in
one session without rewriting ``backend/.env`` between them. Keeping the two
separate is what stops benchmarking from mutating production configuration.

Security rule: a config may name the environment variable holding a credential
(``api_key_env``) but must never contain the credential itself. The only
literal key it may carry is ``placeholder_api_key``, which exists solely for
local servers such as Ollama that do not authenticate at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.agents.config import AgentSettings

# The environment variable a local model reads its throwaway key from.
#
# Deliberately NOT AGENT_API_KEY: that variable holds the real cloud credential,
# and reading it here would send the Gemini key to a localhost server.
LOCAL_PLACEHOLDER_KEY_ENV = "BENCHMARK_OLLAMA_API_KEY"


class BenchmarkConfigurationError(RuntimeError):
    """Raised when a benchmark cannot be configured as requested."""


@dataclass(frozen=True)
class BenchmarkModelConfig:
    """One model under test.

    ``input_cost_per_million`` / ``output_cost_per_million`` are optional and
    unset by default. Pricing changes over time and is not something to bake
    into evaluation logic, so a missing price yields ``estimatedCost = null``
    rather than a guessed number.
    """

    name: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    timeout_seconds: float = 60.0
    max_tokens: int = 8192
    max_retries: int = 0
    retry_backoff_seconds: float = 1.0
    retry_max_backoff_seconds: float = 20.0
    temperature: float = 0.0
    reasoning_effort: str | None = None
    placeholder_api_key: str | None = None
    """Non-secret literal for local servers that do not authenticate."""

    availability_url: str | None = None
    """When set, the model must be listed here before the run proceeds."""

    local: bool = False
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    def resolve_api_key(self) -> str | None:
        """Read the credential from the environment, or fall back to a placeholder."""
        from_env = (os.getenv(self.api_key_env) or "").strip()
        if from_env:
            return from_env
        return self.placeholder_api_key

    def credential_available(self) -> bool:
        return bool(self.resolve_api_key())

    def to_settings(self) -> AgentSettings:
        """Build the agent settings this model runs under.

        The returned object carries the key in memory, exactly as production
        does. It is never serialized: ``describe`` is what reaches a report.
        """
        return AgentSettings(
            mode="real",
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            api_key=self.resolve_api_key(),
            timeout_seconds=self.timeout_seconds,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            retry_backoff_seconds=self.retry_backoff_seconds,
            retry_max_backoff_seconds=self.retry_max_backoff_seconds,
            reasoning_effort=self.reasoning_effort,
        )

    def describe(self) -> dict[str, object]:
        """Secret-free description, safe to write to a report."""
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "baseUrl": self.base_url,
            "apiKeyEnv": self.api_key_env,
            "credentialAvailable": self.credential_available(),
            "local": self.local,
            "timeoutSeconds": self.timeout_seconds,
            "maxTokens": self.max_tokens,
            "maxRetries": self.max_retries,
            "temperature": self.temperature,
            "reasoningEffort": self.reasoning_effort,
            "pricing": self.pricing(),
        }

    def pricing(self) -> dict[str, float] | None:
        """Pricing in the report, or ``None`` when no pricing was configured."""
        if self.input_cost_per_million is None and self.output_cost_per_million is None:
            return None
        return {
            "inputCostPerMillion": self.input_cost_per_million,
            "outputCostPerMillion": self.output_cost_per_million,
        }

    def estimate_cost(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        """Estimate cost, or ``None`` when pricing or usage is unavailable.

        Returns ``None`` rather than ``0.0`` when pricing is absent, because
        "unknown cost" and "free" are different claims and a comparison table
        must not blur them.
        """
        if self.input_cost_per_million is None and self.output_cost_per_million is None:
            return None
        if input_tokens is None and output_tokens is None:
            return None
        return (
            (input_tokens or 0) / 1_000_000 * (self.input_cost_per_million or 0.0)
            + (output_tokens or 0) / 1_000_000 * (self.output_cost_per_million or 0.0)
        )


# ---------------------------------------------------------------------------
# Catalogue
#
# ``gemini-3.8-flash`` is the project-selected advocate model for RydeResolve.
# Stage 4C is a *characterization* benchmark: it measures how that one model
# behaves, and it does not rank models or declare a winner.
#
# ``QWEN_LOCAL`` is retained as an unused fallback. It is not run, started,
# configured, or benchmarked, and no Stage 4C result depends on it. It stays in
# the catalogue so the provider-compatibility path remains exercisable, because
# "the transport is vendor-agnostic" is only true while something can prove it.
#
# No pricing is baked in. Fill ``input_cost_per_million`` /
# ``output_cost_per_million`` locally when a cost figure is wanted.
# ---------------------------------------------------------------------------

GEMINI_FLASH = BenchmarkModelConfig(
    name="gemini-3.8-flash",
    provider="openai_compatible",
    model="gemini-3.8-flash",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key_env="AGENT_API_KEY",
    timeout_seconds=60.0,
    max_tokens=8192,
    # Bounded and backoff'd. A 429 without Retry-After is not retried at all,
    # so this cannot silently burn an exhausted quota.
    max_retries=2,
    retry_backoff_seconds=2.0,
    retry_max_backoff_seconds=20.0,
    temperature=0.0,
)

# Unused fallback — see the catalogue note above. Do not remove: deleting it
# would delete the only evidence that the transport is not Gemini-specific.
QWEN_LOCAL = BenchmarkModelConfig(
    name="qwen3-ctx16k",
    provider="openai_compatible",
    model="qwen3-ctx16k",
    base_url="http://127.0.0.1:11434/v1",
    api_key_env=LOCAL_PLACEHOLDER_KEY_ENV,
    placeholder_api_key="ollama",
    timeout_seconds=900.0,
    max_tokens=8192,
    # A local server has no quota to protect and no rate limiter, so retries
    # would only mask a real failure.
    max_retries=0,
    temperature=0.0,
    availability_url="http://127.0.0.1:11434/v1/models",
    local=True,
)

MODEL_CATALOGUE: dict[str, BenchmarkModelConfig] = {
    "gemini": GEMINI_FLASH,
    "gemini-3.8-flash": GEMINI_FLASH,
    "qwen": QWEN_LOCAL,
    "qwen3-ctx16k": QWEN_LOCAL,
    "qwen3-8b-local": QWEN_LOCAL,
}


def resolve_model_configs(names: list[str]) -> list[BenchmarkModelConfig]:
    """Map CLI names onto model configs, rejecting anything unknown.

    Unknown names raise rather than being skipped: silently benchmarking fewer
    models than asked for is how a comparison ends up missing a column with no
    explanation.
    """
    resolved: list[BenchmarkModelConfig] = []
    seen: set[str] = set()
    for name in names:
        key = name.strip().lower()
        if not key:
            continue
        config = MODEL_CATALOGUE.get(key)
        if config is None:
            known = ", ".join(sorted(MODEL_CATALOGUE))
            raise BenchmarkConfigurationError(
                f"Unknown benchmark model {name!r}. Known names: {known}"
            )
        if config.name in seen:
            continue
        seen.add(config.name)
        resolved.append(config)
    if not resolved:
        raise BenchmarkConfigurationError("No benchmark models selected.")
    return resolved


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------


class FailureCategory:
    """Which side of the boundary a failure happened on.

    The distinction is the point of Stage 4C's error handling: a provider that
    is down says nothing about a model's reasoning, and a model that invents
    evidence says nothing about provider health. Aggregates keep them apart.
    """

    NONE = "NONE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    MODEL_OUTPUT_FAILURE = "MODEL_OUTPUT_FAILURE"
    # A failure this taxonomy does not recognise. Kept separate rather than
    # defaulted into one of the other two: an unrecognised code is far more
    # likely to be our bug than the model's, and quietly filing it under "model
    # output" would manufacture a quality problem that does not exist.
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RunStatus:
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SkipReason:
    """Why a model never produced a call at all.

    A skipped model is not a failed model. It is reported separately so an
    unavailable local server is never silently scored as a worse model.
    """

    LOCAL_MODEL_UNAVAILABLE = "LOCAL_MODEL_UNAVAILABLE"
    CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"


# Provider-boundary codes. These describe the transport, the gateway, or the
# account, never the model's reasoning.
PROVIDER_FAILURE_CODES = frozenset(
    {
        "NOT_CONFIGURED",
        "AUTHENTICATION_FAILED",
        "ACCESS_DENIED",
        "MODEL_NOT_FOUND",
        "RATE_LIMITED",
        "TIMEOUT",
        "NETWORK_ERROR",
        "SERVER_ERROR",
        "REQUEST_REJECTED",
        "MALFORMED_RESPONSE",
        "PROVIDER_ERROR",
    }
)

# The model was reached and answered, but the answer could not be used, or it
# was usable but made claims the verifier rejected.
MODEL_OUTPUT_FAILURE_CODES = frozenset(
    {
        "MALFORMED_JSON",
        "SCHEMA_VALIDATION_FAILED",
        "WRONG_SIDE",
        "OUTPUT_TRUNCATED",
        "ADVOCATE_ERROR",
        # An empty completion is the model answering with nothing. The transport
        # worked; the model did not produce usable content.
        "EMPTY_CONTENT",
    }
)


@dataclass
class CallMetrics:
    """Raw metrics for one advocate call. Never contains a secret."""

    benchmark_run_id: str
    timestamp: str
    case_id: str
    dispute_type: str
    side: str
    model_name: str
    provider: str
    model: str
    status: str
    failure_code: str | None = None
    failure_category: str = FailureCategory.NONE
    skip_reason: str | None = None

    latency_ms: int | None = None
    attempt_count: int | None = None
    retry_count: int | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    unattributed_tokens: int | None = None

    generated_claim_count: int = 0
    verified_claim_count: int = 0
    rejected_claim_count: int = 0
    verification_rate: float | None = None

    rejection_reasons: list[str] = field(default_factory=list)
    invalid_evidence_reference_count: int = 0
    invalid_policy_reference_count: int = 0
    fact_contradiction_count: int = 0
    unknown_fact_count: int = 0
    other_rejection_count: int = 0

    recommended_outcome: str | None = None
    structured_output_valid: bool = False
    malformed_output: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "benchmarkRunId": self.benchmark_run_id,
            "timestamp": self.timestamp,
            "caseId": self.case_id,
            "disputeType": self.dispute_type,
            "side": self.side,
            "modelName": self.model_name,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "failureCode": self.failure_code,
            "failureCategory": self.failure_category,
            "skipReason": self.skip_reason,
            "latencyMs": self.latency_ms,
            "attemptCount": self.attempt_count,
            "retryCount": self.retry_count,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "unattributedTokens": self.unattributed_tokens,
            "generatedClaimCount": self.generated_claim_count,
            "verifiedClaimCount": self.verified_claim_count,
            "rejectedClaimCount": self.rejected_claim_count,
            "verificationRate": self.verification_rate,
            "rejectionReasons": list(self.rejection_reasons),
            "invalidEvidenceReferenceCount": self.invalid_evidence_reference_count,
            "invalidPolicyReferenceCount": self.invalid_policy_reference_count,
            "factContradictionCount": self.fact_contradiction_count,
            "unknownFactCount": self.unknown_fact_count,
            "otherRejectionCount": self.other_rejection_count,
            "recommendedOutcome": self.recommended_outcome,
            "structuredOutputValid": self.structured_output_valid,
            "malformedOutput": self.malformed_output,
        }


@dataclass
class LatencyStats:
    """Latency summary over a set of calls. ``sampleSize`` is always reported.

    Sample size is explicit because a mean over one call and a mean over twenty
    look identical otherwise, and a comparison table that hides that is
    misleading.
    """

    sample_size: int = 0
    mean_ms: float | None = None
    median_ms: float | None = None
    min_ms: int | None = None
    max_ms: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "sampleSize": self.sample_size,
            "meanMs": self.mean_ms,
            "medianMs": self.median_ms,
            "minMs": self.min_ms,
            "maxMs": self.max_ms,
        }


@dataclass
class ModelAggregate:
    """Model-level roll-up. Failures are always included, never hidden."""

    model_name: str
    provider: str
    model: str
    total_calls: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    skipped: bool = False
    skip_reason: str | None = None
    completion_rate: float | None = None

    provider_failure_count: int = 0
    model_output_failure_count: int = 0
    internal_error_count: int = 0

    total_claims: int = 0
    verified_claims: int = 0
    rejected_claims: int = 0
    verification_rate: float | None = None
    calls_with_zero_claims: int = 0

    schema_failure_count: int = 0
    malformed_output_count: int = 0

    invalid_evidence_refs: int = 0
    invalid_policy_refs: int = 0
    fact_contradictions: int = 0
    unknown_facts: int = 0
    other_rejections: int = 0

    completed_latency: LatencyStats = field(default_factory=LatencyStats)
    failed_latency: LatencyStats = field(default_factory=LatencyStats)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    unattributed_tokens: int | None = None

    total_attempts: int = 0
    total_retries: int = 0

    provider_error_counts: dict[str, int] = field(default_factory=dict)
    model_output_error_counts: dict[str, int] = field(default_factory=dict)
    internal_error_counts: dict[str, int] = field(default_factory=dict)
    rejection_reason_counts: dict[str, int] = field(default_factory=dict)
    recommended_outcomes: dict[str, int] = field(default_factory=dict)

    estimated_cost: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "modelName": self.model_name,
            "provider": self.provider,
            "model": self.model,
            "totalCalls": self.total_calls,
            "completedCalls": self.completed_calls,
            "failedCalls": self.failed_calls,
            "skipped": self.skipped,
            "skipReason": self.skip_reason,
            "completionRate": self.completion_rate,
            "providerFailureCount": self.provider_failure_count,
            "modelOutputFailureCount": self.model_output_failure_count,
            "internalErrorCount": self.internal_error_count,
            "totalClaims": self.total_claims,
            "verifiedClaims": self.verified_claims,
            "rejectedClaims": self.rejected_claims,
            "verificationRate": self.verification_rate,
            "callsWithZeroClaims": self.calls_with_zero_claims,
            "schemaFailureCount": self.schema_failure_count,
            "malformedOutputCount": self.malformed_output_count,
            "invalidEvidenceRefs": self.invalid_evidence_refs,
            "invalidPolicyRefs": self.invalid_policy_refs,
            "factContradictions": self.fact_contradictions,
            "unknownFacts": self.unknown_facts,
            "otherRejections": self.other_rejections,
            "completedLatency": self.completed_latency.to_dict(),
            "failedLatency": self.failed_latency.to_dict(),
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "unattributedTokens": self.unattributed_tokens,
            "totalAttempts": self.total_attempts,
            "totalRetries": self.total_retries,
            "providerErrorCounts": dict(self.provider_error_counts),
            "modelOutputErrorCounts": dict(self.model_output_error_counts),
            "internalErrorCounts": dict(self.internal_error_counts),
            "rejectionReasonCounts": dict(self.rejection_reason_counts),
            "recommendedOutcomes": dict(self.recommended_outcomes),
            "estimatedCost": self.estimated_cost,
        }
