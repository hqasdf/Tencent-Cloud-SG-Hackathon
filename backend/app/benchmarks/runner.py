"""Benchmark planning and execution.

The runner drives the **real** production components:

  AdvocateContextBuilder -> RiderAdvocateAgent / DriverAdvocateAgent
                         -> AdvocateClaimVerificationService

It does not re-implement prompt assembly, response parsing, or verification.
A benchmark that measured a simplified stand-in prompt would be measuring the
stand-in, not the system.

The orchestrator is deliberately not used here. ``AdvocateOrchestratorService``
is a presentation-shaped wrapper that collapses errors into a human-readable
``AdvocateSideResult``; benchmarking needs the raw failure code, the attempt
count, and the distinction between a provider fault and a model fault, all of
which the wrapper discards. The call sequence below mirrors the orchestrator's
per-side sequence exactly.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from app.agents.base_advocate import (
    AdvocateAgentError,
    AdvocateOutputErrorCode,
    BaseAdvocateAgent,
)
from app.agents.claim_verification import AdvocateClaimVerificationService
from app.agents.config import AgentConfigurationError
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.driver_advocate import DriverAdvocateAgent
from app.agents.provider import LlmProvider, LlmProviderError
from app.agents.providers.openai_compatible import normalise_model_id
from app.agents.providers.registry import get_provider
from app.agents.rider_advocate import RiderAdvocateAgent
from app.benchmarks.cases import (
    CaseSelection,
    case_by_id,
    select_profile_cases,
)
from app.benchmarks.metrics import (
    aggregate_calls,
    classify_failure,
    compute_unattributed_tokens,
    compute_verification_rate,
    rejection_breakdown,
)
from app.benchmarks.models import (
    BenchmarkConfigurationError,
    BenchmarkModelConfig,
    CallMetrics,
    FailureCategory,
    ModelAggregate,
    RunStatus,
    SkipReason,
)
from app.services.dispute_analysis import DisputeAnalysisService

SIDES: tuple[str, ...] = ("RIDER", "DRIVER")

# Codes that mean "the model answered, but we could not use the answer".
_UNUSABLE_OUTPUT_CODES = frozenset(
    {
        AdvocateOutputErrorCode.MALFORMED_JSON,
        AdvocateOutputErrorCode.SCHEMA_VALIDATION_FAILED,
        AdvocateOutputErrorCode.WRONG_SIDE,
        AdvocateOutputErrorCode.OUTPUT_TRUNCATED,
    }
)


@dataclass(frozen=True)
class BenchmarkPlan:
    """What a run would do, before it does anything.

    Exists so the cost of a benchmark can be inspected and refused. With a
    constrained free tier, "how many API calls is this?" must be answerable
    without making any.
    """

    profile: str
    runs: int
    model_configs: list[BenchmarkModelConfig]
    case_selections: list[CaseSelection]
    sides: tuple[str, ...] = SIDES

    @property
    def case_ids(self) -> list[str]:
        return [case_id for selection in self.case_selections for case_id in selection.selected]

    @property
    def cases_per_model(self) -> int:
        return len(self.case_ids)

    @property
    def calls_per_case(self) -> int:
        return len(self.sides) * self.runs

    @property
    def expected_calls_per_model(self) -> int:
        return self.cases_per_model * self.calls_per_case

    @property
    def total_expected_calls(self) -> int:
        return self.expected_calls_per_model * len(self.model_configs)

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "runs": self.runs,
            "casesPerModel": self.cases_per_model,
            "advocatesPerCase": len(self.sides),
            "expectedCallsPerModel": self.expected_calls_per_model,
            "totalExpectedCalls": self.total_expected_calls,
            "models": [config.describe() for config in self.model_configs],
            "caseSelections": [selection.to_dict() for selection in self.case_selections],
        }


@dataclass
class BenchmarkResult:
    benchmark_run_id: str
    started_at: str
    finished_at: str
    profile: str
    runs: int
    plan: dict[str, object]
    calls: list[CallMetrics] = field(default_factory=list)
    aggregates: list[ModelAggregate] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "benchmarkRunId": self.benchmark_run_id,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "profile": self.profile,
            "runs": self.runs,
            "plan": self.plan,
            "aggregates": [aggregate.to_dict() for aggregate in self.aggregates],
            "calls": [call.to_dict() for call in self.calls],
        }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def build_plan(
    *,
    profile: str,
    model_configs: list[BenchmarkModelConfig],
    runs: int = 1,
) -> BenchmarkPlan:
    """Build a plan. Makes no network calls and reads no credentials."""
    if profile not in ("smoke", "full"):
        raise BenchmarkConfigurationError(
            f"Unknown profile {profile!r}. Expected 'smoke' or 'full'."
        )
    if runs < 1:
        raise BenchmarkConfigurationError(f"--runs must be at least 1, received {runs}.")
    if not model_configs:
        raise BenchmarkConfigurationError("No models selected.")

    return BenchmarkPlan(
        profile=profile,
        runs=runs,
        model_configs=list(model_configs),
        case_selections=select_profile_cases(profile),
    )


def render_plan(plan: BenchmarkPlan) -> str:
    """Human-readable plan, printed before any call is made."""
    lines = [
        "Benchmark plan (NO API CALLS MADE)",
        f"  profile   : {plan.profile}",
        f"  runs      : {plan.runs}",
        "",
    ]
    for selection in plan.case_selections:
        lines.append(f"  {selection.dispute_type}: {', '.join(selection.selected)}")
        lines.append(f"    criterion : {selection.description}")
        lines.append(f"    candidates: {', '.join(selection.candidates)}")
        if selection.tie_break:
            lines.append(f"    tie break : {selection.tie_break}")
    lines.append("")
    for config in plan.model_configs:
        credential = "present" if config.credential_available() else "MISSING"
        lines.extend(
            [
                f"  model     : {config.name}",
                f"    provider: {config.provider}",
                f"    model id: {config.model}",
                f"    base url: {config.base_url}",
                f"    key env : {config.api_key_env} ({credential})",
                f"    cases   : {plan.cases_per_model}",
                f"    advocates per case: {len(plan.sides)}",
                f"    runs    : {plan.runs}",
                f"    EXPECTED API CALLS: {plan.expected_calls_per_model}",
                "",
            ]
        )
    lines.append(f"  TOTAL EXPECTED API CALLS: {plan.total_expected_calls}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def check_model_availability(
    config: BenchmarkModelConfig,
) -> tuple[bool, str | None]:
    """Decide whether a model can be benchmarked, without spending a completion.

    A local model that is not running is reported as ``LOCAL_MODEL_UNAVAILABLE``
    rather than failing the benchmark, so a cloud result and a local result stay
    independently obtainable.
    """
    if not config.credential_available():
        return False, SkipReason.CREDENTIAL_REQUIRED
    if not config.availability_url:
        return True, None

    try:
        provider = get_provider(config.to_settings())
        models = provider.list_models()  # type: ignore[attr-defined]
    except (LlmProviderError, AgentConfigurationError, AttributeError):
        return False, SkipReason.LOCAL_MODEL_UNAVAILABLE

    wanted = normalise_model_id(config.model)
    if not any(normalise_model_id(candidate) == wanted for candidate in models):
        return False, SkipReason.LOCAL_MODEL_UNAVAILABLE
    return True, None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_plan(
    plan: BenchmarkPlan,
    *,
    allow_live: bool = False,
    provider_factory: Callable[[BenchmarkModelConfig], LlmProvider] | None = None,
    availability_checker: Callable[[BenchmarkModelConfig], tuple[bool, str | None]] | None = None,
    run_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> BenchmarkResult:
    """Execute a plan through the real advocate pipeline.

    Live execution requires ``allow_live=True``. The default refusal exists
    because the cheapest mistake in a benchmark is accidentally spending a
    constrained quota.
    """
    if provider_factory is None and not allow_live:
        raise BenchmarkConfigurationError(
            "Refusing to execute: live model calls require allow_live=True "
            "(CLI: pass --execute)."
        )

    now = clock or (lambda: datetime.now(timezone.utc))
    checker = availability_checker or check_model_availability
    resolved_run_id = run_id or _new_run_id(now())

    builder = AdvocateContextBuilder()
    verifier = AdvocateClaimVerificationService()
    analysis_service = DisputeAnalysisService()

    started_at = now().isoformat()
    calls: list[CallMetrics] = []
    aggregates: list[ModelAggregate] = []

    for config in plan.model_configs:
        available, skip_reason = checker(config)
        if not available:
            aggregates.append(
                aggregate_calls(
                    [],
                    model_name=config.name,
                    provider=config.provider,
                    model=config.model,
                    skipped=True,
                    skip_reason=skip_reason,
                )
            )
            continue

        settings = config.to_settings()
        provider = (
            provider_factory(config) if provider_factory is not None else get_provider(settings)
        )
        agents: dict[str, BaseAdvocateAgent] = {
            "RIDER": RiderAdvocateAgent(provider, settings),
            "DRIVER": DriverAdvocateAgent(provider, settings),
        }

        model_calls: list[CallMetrics] = []
        for case_id in plan.case_ids:
            case = case_by_id(case_id)
            analysis = analysis_service.analyze(case)
            context = builder.build(case, analysis)
            for _ in range(plan.runs):
                for side in plan.sides:
                    call = _run_call(
                        agent=agents[side],
                        context=context,
                        verifier=verifier,
                        config=config,
                        case=case,
                        side=side,
                        run_id=resolved_run_id,
                        timestamp=now().isoformat(),
                    )
                    model_calls.append(call)
                    calls.append(call)

        aggregates.append(
            aggregate_calls(
                model_calls,
                model_name=config.name,
                provider=config.provider,
                model=config.model,
                input_cost_per_million=config.input_cost_per_million,
                output_cost_per_million=config.output_cost_per_million,
            )
        )

    return BenchmarkResult(
        benchmark_run_id=resolved_run_id,
        started_at=started_at,
        finished_at=now().isoformat(),
        profile=plan.profile,
        runs=plan.runs,
        plan=plan.to_dict(),
        calls=calls,
        aggregates=aggregates,
    )


def _run_call(
    *,
    agent: BaseAdvocateAgent,
    context: object,
    verifier: AdvocateClaimVerificationService,
    config: BenchmarkModelConfig,
    case: object,
    side: str,
    run_id: str,
    timestamp: str,
) -> CallMetrics:
    """One advocate call, with every failure mode turned into a metric."""
    base: dict[str, object] = {
        "benchmark_run_id": run_id,
        "timestamp": timestamp,
        "case_id": case.id,  # type: ignore[attr-defined]
        "dispute_type": case.dispute_type,  # type: ignore[attr-defined]
        "side": side,
        "model_name": config.name,
        "provider": config.provider,
        "model": config.model,
    }
    started = time.monotonic()

    try:
        argument = agent.argue_with_trace(context)  # type: ignore[arg-type]
    except LlmProviderError as error:
        return CallMetrics(
            **base,  # type: ignore[arg-type]
            status=RunStatus.FAILED,
            failure_code=error.code,
            failure_category=classify_failure(error.code),
            latency_ms=_elapsed_ms(started),
            attempt_count=error.attempts,
            retry_count=max(error.attempts - 1, 0),
        )
    except AdvocateAgentError as error:
        completion = error.completion
        malformed = error.code in _UNUSABLE_OUTPUT_CODES
        return CallMetrics(
            **base,  # type: ignore[arg-type]
            status=RunStatus.FAILED,
            failure_code=error.code,
            failure_category=classify_failure(error.code),
            # A failed call still reports its cost when the model did respond,
            # so a model that always burns its whole budget stays visible.
            latency_ms=(
                completion.duration_ms if completion else _elapsed_ms(started)
            ),
            attempt_count=completion.attempt_count if completion else None,
            retry_count=completion.retry_count if completion else None,
            input_tokens=completion.input_tokens if completion else None,
            output_tokens=completion.output_tokens if completion else None,
            total_tokens=completion.total_tokens if completion else None,
            unattributed_tokens=(
                compute_unattributed_tokens(
                    completion.input_tokens, completion.output_tokens, completion.total_tokens
                )
                if completion
                else None
            ),
            malformed_output=malformed,
            structured_output_valid=False,
        )
    except Exception as error:  # noqa: BLE001 - benchmark boundary
        return CallMetrics(
            **base,  # type: ignore[arg-type]
            status=RunStatus.FAILED,
            failure_code=type(error).__name__,
            failure_category=FailureCategory.INTERNAL_ERROR,
            latency_ms=_elapsed_ms(started),
        )

    outcome = verifier.verify(argument.output, context)  # type: ignore[arg-type]
    completion = argument.completion
    generated = len(argument.output.claims)
    verified = len(outcome.verified_claims)
    rejected = len(outcome.rejected_claims)
    reasons = sorted({claim.reason for claim in outcome.rejected_claims})
    # The per-call counters are derived here as well as in the aggregate. The
    # aggregate must stay able to summarise a hand-built record, so it cannot
    # rely on these fields; but the raw per-call JSON is a deliverable in its own
    # right, and emitting 0 for a rejection that happened would be a false
    # statement in that file. Both paths call the same breakdown function.
    breakdown = rejection_breakdown(reasons)

    return CallMetrics(
        **base,  # type: ignore[arg-type]
        status=RunStatus.COMPLETE,
        latency_ms=completion.duration_ms,
        attempt_count=completion.attempt_count,
        retry_count=completion.retry_count,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        total_tokens=completion.total_tokens,
        unattributed_tokens=compute_unattributed_tokens(
            completion.input_tokens, completion.output_tokens, completion.total_tokens
        ),
        generated_claim_count=generated,
        verified_claim_count=verified,
        rejected_claim_count=rejected,
        verification_rate=compute_verification_rate(verified, generated),
        rejection_reasons=reasons,
        invalid_evidence_reference_count=breakdown["invalid_evidence"],
        invalid_policy_reference_count=breakdown["invalid_policy"],
        fact_contradiction_count=breakdown["fact_contradiction"],
        unknown_fact_count=breakdown["unknown_fact"],
        other_rejection_count=breakdown["other"],
        recommended_outcome=argument.output.requested_outcome,
        structured_output_valid=True,
        malformed_output=False,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _new_run_id(moment: datetime) -> str:
    return f"bench-{moment.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
