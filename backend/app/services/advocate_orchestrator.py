from __future__ import annotations

import time

from app.agents.base_advocate import AdvocateAgentError, AdvocateOutputErrorCode
from app.agents.claim_verification import AdvocateClaimVerificationService
from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.driver_advocate import DriverAdvocateAgent
from app.agents.provider import LlmProvider, LlmProviderError
from app.agents.providers.registry import get_provider
from app.agents.rider_advocate import RiderAdvocateAgent
from app.models.advocate import (
    AdvocateRunResponse,
    AdvocateSideResult,
    PipelineStage,
    VerificationSummary,
)
from app.models.analysis import CaseAnalysisResponse
from app.models.agent import AgentCallMetadata, AgentCaseContext, AgentRunMetadata
from app.models.case import DisputeCase

JUDGE_STAGE = "JUDGE"

# An agent answered, but its answer could not be turned into the contract.
# Distinct from a provider failure, where the model was never reached.
_UNUSABLE_OUTPUT_CODES = frozenset(
    {
        AdvocateOutputErrorCode.MALFORMED_JSON,
        AdvocateOutputErrorCode.SCHEMA_VALIDATION_FAILED,
        AdvocateOutputErrorCode.WRONG_SIDE,
        AdvocateOutputErrorCode.OUTPUT_TRUNCATED,
    }
)


class AdvocateOrchestratorService:
    """Runs both advocates and verifies their claims.

    Hard invariant: this service never mutates deterministic data. It reads the
    canonical case and the deterministic analysis, and its only output is an
    AdvocateRunResponse. There is deliberately no method that persists or merges
    advocate output back into the case.

    One advocate failing must not corrupt the other advocate or the
    deterministic analysis.

    Dependencies are supplied by the caller rather than imported, which keeps
    this module free of any import cycle with the case service.
    """

    def __init__(
        self,
        *,
        settings: AgentSettings | None = None,
        context_builder: AdvocateContextBuilder | None = None,
        verification: AdvocateClaimVerificationService | None = None,
        provider: LlmProvider | None = None,
    ) -> None:
        self._settings = settings or AgentSettings.from_environment()
        self._context_builder = context_builder or AdvocateContextBuilder()
        self._verification = verification or AdvocateClaimVerificationService()
        self._provider = provider

    def run(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext | None = None,
    ) -> AdvocateRunResponse:
        """Run both advocates.

        ``context`` may be supplied by a caller that has already built it (Stage
        5 builds one projection and shares it with the Judge). It is optional so
        Stage 4 callers are unaffected, and it is never mutated.
        """
        started = time.monotonic()
        pipeline: list[PipelineStage] = [
            PipelineStage(stage="CASE_RECEIVED", status="COMPLETE"),
            PipelineStage(stage="EVIDENCE_VALIDATED", status="COMPLETE"),
            PipelineStage(stage="DETERMINISTIC_ANALYSIS", status="COMPLETE"),
            PipelineStage(stage="RIDER_ADVOCATE", status="NOT_RUN"),
            PipelineStage(stage="DRIVER_ADVOCATE", status="NOT_RUN"),
            PipelineStage(stage="CLAIM_VERIFICATION", status="NOT_RUN"),
            PipelineStage(stage=JUDGE_STAGE, status="NOT_RUN"),
        ]

        # The case and its deterministic analysis arrive already computed. The
        # orchestrator never recalculates them and never writes to them.
        context = context or self._context_builder.build(case, analysis)

        # A provider failure here is configuration-level (unsupported provider,
        # missing credential). It is raised rather than degraded, because
        # pretending both advocates ran would misrepresent the run.
        provider = self._provider or get_provider(self._settings)
        rider_agent = RiderAdvocateAgent(provider, self._settings)
        driver_agent = DriverAdvocateAgent(provider, self._settings)

        # 5-6. Independent execution. Neither agent sees the other's output.
        rider_result = self._run_side(rider_agent, context, pipeline, "RIDER_ADVOCATE")
        driver_result = self._run_side(driver_agent, context, pipeline, "DRIVER_ADVOCATE")

        _mark(
            pipeline,
            "CLAIM_VERIFICATION",
            "FAILED"
            if rider_result.failure_reason and driver_result.failure_reason
            else "COMPLETE",
        )

        verified = len(rider_result.verified_claims) + len(driver_result.verified_claims)
        rejected = len(rider_result.rejected_claims) + len(driver_result.rejected_claims)
        reasons = sorted(
            {
                claim.reason
                for claim in [*rider_result.rejected_claims, *driver_result.rejected_claims]
            }
        )

        return AdvocateRunResponse(
            case_id=case.id,
            dispute_type=case.dispute_type,
            rider=rider_result,
            driver=driver_result,
            agent_run=AgentRunMetadata(
                mode=self._settings.mode,
                provider=provider.name,
                model=self._settings.model if self._settings.mode == "real" else None,
                prompt_version=self._settings.prompt_version,
                duration_ms=int((time.monotonic() - started) * 1000),
                input_tokens=_sum_tokens(
                    rider_result.execution.input_tokens if rider_result.execution else None,
                    driver_result.execution.input_tokens if driver_result.execution else None,
                ),
                output_tokens=_sum_tokens(
                    rider_result.execution.output_tokens if rider_result.execution else None,
                    driver_result.execution.output_tokens if driver_result.execution else None,
                ),
                total_tokens=_sum_tokens(
                    rider_result.execution.total_tokens if rider_result.execution else None,
                    driver_result.execution.total_tokens if driver_result.execution else None,
                ),
            ),
            verification_summary=VerificationSummary(
                verified_count=verified,
                rejected_count=rejected,
                rejection_reasons=reasons,
            ),
            pipeline=pipeline,
        )

    def _run_side(
        self,
        agent: RiderAdvocateAgent | DriverAdvocateAgent,
        context: AgentCaseContext,
        pipeline: list[PipelineStage],
        stage: str,
    ) -> AdvocateSideResult:
        """Run one advocate in isolation. Failure never propagates outward."""
        side = agent.side
        started = time.monotonic()
        try:
            argument = agent.argue_with_trace(context)
        except (LlmProviderError, AdvocateAgentError) as error:
            _mark(pipeline, stage, "FAILED")
            return AdvocateSideResult(
                side=side,
                status="FAILED",
                summary="",
                failure_reason=_safe_failure_reason(error),
                execution=self._failure_metadata(agent, error, started),
            )
        except Exception as error:  # noqa: BLE001 - isolation boundary
            _mark(pipeline, stage, "FAILED")
            return AdvocateSideResult(
                side=side,
                status="FAILED",
                summary="",
                failure_reason=f"Advocate run failed: {type(error).__name__}",
                execution=self._failure_metadata(agent, error, started),
            )

        outcome = self._verification.verify(argument.output, context)
        _mark(pipeline, stage, "COMPLETE")
        completion = argument.completion
        return AdvocateSideResult(
            side=side,
            status="COMPLETE",
            summary=argument.output.summary,
            requested_outcome=argument.output.requested_outcome,
            context_acknowledged=argument.output.context_acknowledged,
            verified_claims=outcome.verified_claims,
            rejected_claims=outcome.rejected_claims,
            warnings=outcome.warnings,
            execution=AgentCallMetadata(
                provider=completion.provider_name,
                model=completion.model_name,
                duration_ms=completion.duration_ms,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                total_tokens=completion.total_tokens,
                generated_claim_count=len(argument.output.claims),
                verified_claim_count=len(outcome.verified_claims),
                rejected_claim_count=len(outcome.rejected_claims),
                rejection_reasons=sorted({claim.reason for claim in outcome.rejected_claims}),
                malformed_output=False,
            ),
        )

    def _failure_metadata(
        self,
        agent: RiderAdvocateAgent | DriverAdvocateAgent,
        error: Exception,
        started: float,
    ) -> AgentCallMetadata:
        """Record the failed attempt so Stage 4C can count failures by cause.

        When the model *did* respond but its answer was unusable, the provider
        metrics are still available. Keeping them matters: a model that always
        exhausts its token budget should be visible as such, not reduced to a
        bare failure with no cost signal.
        """
        completion = getattr(error, "completion", None)
        if isinstance(error, LlmProviderError):
            code = error.code
            malformed = False
        elif isinstance(error, AdvocateAgentError):
            code = error.code
            malformed = error.code in _UNUSABLE_OUTPUT_CODES
        else:
            code = type(error).__name__
            malformed = False
        return AgentCallMetadata(
            provider=completion.provider_name if completion else agent.provider_name,
            model=(
                completion.model_name
                if completion and completion.model_name
                else (self._settings.model if self._settings.mode == "real" else None)
            ),
            duration_ms=(
                completion.duration_ms
                if completion
                else int((time.monotonic() - started) * 1000)
            ),
            input_tokens=completion.input_tokens if completion else None,
            output_tokens=completion.output_tokens if completion else None,
            total_tokens=completion.total_tokens if completion else None,
            malformed_output=malformed,
            failure_code=code,
        )


def _mark(pipeline: list[PipelineStage], stage: str, status: str) -> None:
    for item in pipeline:
        if item.stage == stage:
            item.status = status  # type: ignore[assignment]
            return


def _sum_tokens(*values: int | None) -> int | None:
    """Sum token counts, preserving "not reported" as None rather than 0."""
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _safe_failure_reason(error: Exception) -> str:
    """Produce a failure reason that never leaks keys, headers, or traces.

    The error code is included because it is machine-generated and safe, and it
    tells the operator what to fix (a bad key versus an unavailable model)
    instead of a generic outage message.
    """
    if isinstance(error, LlmProviderError):
        return (
            f"Agent provider unavailable ({error.provider}: {error.code}). "
            "The case analysis is unaffected."
        )
    if isinstance(error, AdvocateAgentError):
        return f"Advocate output could not be used ({error.code}): {error}"
    return "Advocate run failed."
