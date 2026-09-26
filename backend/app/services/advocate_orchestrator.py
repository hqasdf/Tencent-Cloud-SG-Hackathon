from __future__ import annotations

import time

from app.agents.base_advocate import AdvocateAgentError
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
from app.models.agent import AgentCaseContext, AgentRunMetadata
from app.models.case import DisputeCase

JUDGE_STAGE = "JUDGE"


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

    def run(self, case: DisputeCase, analysis: CaseAnalysisResponse) -> AdvocateRunResponse:
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
        context = self._context_builder.build(case, analysis)

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
        try:
            output = agent.argue(context)
        except (LlmProviderError, AdvocateAgentError) as error:
            _mark(pipeline, stage, "FAILED")
            return AdvocateSideResult(
                side=side,
                status="FAILED",
                summary="",
                failure_reason=_safe_failure_reason(error),
            )
        except Exception as error:  # noqa: BLE001 - isolation boundary
            _mark(pipeline, stage, "FAILED")
            return AdvocateSideResult(
                side=side,
                status="FAILED",
                summary="",
                failure_reason=f"Advocate run failed: {type(error).__name__}",
            )

        outcome = self._verification.verify(output, context)
        _mark(pipeline, stage, "COMPLETE")
        return AdvocateSideResult(
            side=side,
            status="COMPLETE",
            summary=output.summary,
            requested_outcome=output.requested_outcome,
            context_acknowledged=output.context_acknowledged,
            verified_claims=outcome.verified_claims,
            rejected_claims=outcome.rejected_claims,
            warnings=outcome.warnings,
        )


def _mark(pipeline: list[PipelineStage], stage: str, status: str) -> None:
    for item in pipeline:
        if item.stage == stage:
            item.status = status  # type: ignore[assignment]
            return


def _safe_failure_reason(error: Exception) -> str:
    """Produce a failure reason that never leaks keys, headers, or traces."""
    if isinstance(error, LlmProviderError):
        return f"Agent provider unavailable ({error.provider}). The case analysis is unaffected."
    if isinstance(error, AdvocateAgentError):
        return f"Advocate output could not be used: {error}"
    return "Advocate run failed."
