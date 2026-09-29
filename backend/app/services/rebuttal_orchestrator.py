"""Stage 6 orchestration: one bounded rebuttal round, then verification.

This service runs both rebuttals exactly once and verifies them. It performs no
calculation of its own and never touches the deterministic analysis.

Three behaviours are deliberate and load-bearing.

*One round, structurally.* There is no loop here and no parameter that could
introduce one. ``MAX_REBUTTAL_ROUNDS`` is 1, the context advertises
``maxRebuttalRounds: 1``, and — most importantly — a ``RebuttalCaseContext`` can
only carry *initial* verified claims. A second round is not merely disallowed;
it is unrepresentable, because nothing in the pipeline could name a rebuttal as
a rebuttal target.

*Initial advocate failure blocks rebuttals.* If either side's initial argument
failed, no rebuttal runs at all. The Judge will not run either, so spending two
model calls to produce input that is guaranteed to be discarded would be waste —
and worse, it would create a record implying the case reached cross-examination
when it never did. Both sides report ``NOT_RUN``.

*Rebuttal failure is supplementary.* If one side's rebuttal fails or is entirely
rejected, the other side's rebuttal still stands and the Judge still runs. This
is the opposite of the initial-advocate rule, and the asymmetry is the point: the
initial arguments are the case, while rebuttals are responses to it. A missing
rebuttal is a side that declined to respond, not a case that cannot be decided.
Nothing is fabricated to fill the gap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.agents.config import AgentSettings
from app.agents.provider import LlmProvider, LlmProviderError
from app.agents.providers.registry import get_provider
from app.agents.rebuttal_agent import RebuttalAgent, RebuttalAgentError
from app.agents.rebuttal_context_builder import RebuttalContextBuilder
from app.agents.rebuttal_validation import RebuttalValidationService
from app.models.advocate import AdvocateRunResponse, AdvocateSideResult, PipelineStage
from app.models.analysis import CaseAnalysisResponse
from app.models.agent import AgentCallMetadata, AdvocateSide, AgentCaseContext
from app.models.case import DisputeCase
from app.models.rebuttal import (
    MAX_REBUTTAL_ROUNDS,
    RebuttalOutput,
    RebuttalOutputErrorCode,
    RebuttalRunResponse,
    RebuttalSideResult,
    RebuttalVerificationSummary,
)
from app.services.audit import (
    REBUTTALS_NOT_RUN,
    REBUTTAL_CONTEXT_BUILT,
    REBUTTAL_REJECTED,
    REBUTTAL_VERIFIED,
    SIDE_EVENTS,
    AuditTrail,
)

RIDER_REBUTTAL_STAGE = "RIDER_REBUTTAL"
DRIVER_REBUTTAL_STAGE = "DRIVER_REBUTTAL"
REBUTTAL_VERIFICATION_STAGE = "REBUTTAL_VERIFICATION"

# An agent answered, but its answer could not be turned into the contract.
_UNUSABLE_OUTPUT_CODES = frozenset(
    {
        RebuttalOutputErrorCode.MALFORMED_JSON,
        RebuttalOutputErrorCode.SCHEMA_VALIDATION_FAILED,
        RebuttalOutputErrorCode.WRONG_SIDE,
        RebuttalOutputErrorCode.OUTPUT_TRUNCATED,
    }
)


@dataclass
class RebuttalRunOutcome:
    """A rebuttal run's response plus the raw output behind each side.

    ``run`` returns only ``response``, so Stage 6's contract is untouched. A
    side that failed contributes no entry to ``raw_outputs``; a side that
    completed contributes its raw ``RebuttalOutput``, which is what a replay
    artefact stores.
    """

    response: RebuttalRunResponse
    raw_outputs: dict[str, RebuttalOutput] = field(default_factory=dict)


class RebuttalOrchestratorService:
    """Runs one rebuttal round for both sides and verifies the responses."""

    def __init__(
        self,
        *,
        settings: AgentSettings | None = None,
        context_builder: RebuttalContextBuilder | None = None,
        validation: RebuttalValidationService | None = None,
        provider: LlmProvider | None = None,
    ) -> None:
        self._settings = settings or AgentSettings.from_environment()
        self._context_builder = context_builder or RebuttalContextBuilder()
        self._validation = validation or RebuttalValidationService()
        self._provider = provider

    def run(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        advocate_context: AgentCaseContext,
        advocate_run: AdvocateRunResponse,
        audit: AuditTrail | None = None,
    ) -> RebuttalRunResponse:
        """Run one rebuttal round.

        ``advocate_context`` is passed in rather than rebuilt so the rebuttals,
        the advocates and the Judge all reason over the same facts, evidence and
        policy. Rebuilding would create a second projection that could drift.

        ``audit`` is optional and recorded *as each step happens*, not assembled
        afterwards. A trail whose timestamps all reflect the moment of assembly
        would not tell a reviewer how long a rebuttal took or whether it ran at
        all.
        """
        return self.run_with_raw(
            case, analysis, advocate_context, advocate_run, audit
        ).response

    def run_with_raw(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        advocate_context: AgentCaseContext,
        advocate_run: AdvocateRunResponse,
        audit: AuditTrail | None = None,
    ) -> RebuttalRunOutcome:
        """``run``, plus each side's raw rebuttal output for replay capture.

        Identical in every observable respect to ``run``.
        """
        pipeline: list[PipelineStage] = [
            PipelineStage(stage=RIDER_REBUTTAL_STAGE, status="NOT_RUN"),
            PipelineStage(stage=DRIVER_REBUTTAL_STAGE, status="NOT_RUN"),
            PipelineStage(stage=REBUTTAL_VERIFICATION_STAGE, status="NOT_RUN"),
        ]

        blocked = self._blocked_reason(advocate_run)
        if blocked is not None:
            # The whole layer is skipped, not partially attempted. See the module
            # docstring for why this differs from rebuttal failure below.
            reason = (
                "Rebuttal did not run: "
                + " and ".join(blocked)
                + " did not produce an initial argument, so there is no verified case "
                "to cross-examine."
            )
            if audit is not None:
                audit.record(
                    REBUTTALS_NOT_RUN,
                    reason="ADVOCATE_INPUT_INCOMPLETE",
                    failed_sides=blocked,
                )
            return RebuttalRunOutcome(
                response=RebuttalRunResponse(
                    case_id=case.id,
                    dispute_type=case.dispute_type,
                    rider=RebuttalSideResult(
                        side="RIDER", status="NOT_RUN", failure_reason=reason
                    ),
                    driver=RebuttalSideResult(
                        side="DRIVER", status="NOT_RUN", failure_reason=reason
                    ),
                    verification_summary=RebuttalVerificationSummary(
                        verified_count=0, rejected_count=0, rejection_reasons=[]
                    ),
                    pipeline=pipeline,
                )
            )

        # A provider failure here is configuration-level and is raised rather
        # than degraded: pretending both rebuttals ran would misrepresent the run.
        provider = self._provider or get_provider(self._settings)

        rider, rider_raw = self._run_side(
            case,
            analysis,
            advocate_context,
            advocate_run,
            "RIDER",
            provider,
            pipeline,
            audit,
        )
        driver, driver_raw = self._run_side(
            case,
            analysis,
            advocate_context,
            advocate_run,
            "DRIVER",
            provider,
            pipeline,
            audit,
        )

        # Verification ran for whichever sides produced output. It is reported as
        # COMPLETE even when every response was rejected: "code checked the
        # output" and "the output was good" are different statements.
        _mark(pipeline, REBUTTAL_VERIFICATION_STAGE, "COMPLETE")

        verified = len(rider.verified_rebuttals) + len(driver.verified_rebuttals)
        rejected = len(rider.rejected_rebuttals) + len(driver.rejected_rebuttals)
        reasons = sorted(
            {
                item.reason
                for item in [*rider.rejected_rebuttals, *driver.rejected_rebuttals]
            }
        )
        generated = (rider.execution.generated_claim_count if rider.execution else 0) + (
            driver.execution.generated_claim_count if driver.execution else 0
        )

        response = RebuttalRunResponse(
            case_id=case.id,
            dispute_type=case.dispute_type,
            round=MAX_REBUTTAL_ROUNDS,
            max_rounds=MAX_REBUTTAL_ROUNDS,
            rider=rider,
            driver=driver,
            verification_summary=RebuttalVerificationSummary(
                verified_count=verified,
                rejected_count=rejected,
                rejection_reasons=reasons,
                generated_count=generated,
            ),
            pipeline=pipeline,
        )
        raw_outputs: dict[str, RebuttalOutput] = {}
        if rider_raw is not None:
            raw_outputs["RIDER"] = rider_raw
        if driver_raw is not None:
            raw_outputs["DRIVER"] = driver_raw
        return RebuttalRunOutcome(response=response, raw_outputs=raw_outputs)

    # -- one side ---------------------------------------------------------

    def _run_side(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        advocate_context: AgentCaseContext,
        advocate_run: AdvocateRunResponse,
        side: AdvocateSide,
        provider: LlmProvider,
        pipeline: list[PipelineStage],
        audit: AuditTrail | None,
    ) -> tuple[RebuttalSideResult, RebuttalOutput | None]:
        """Run one rebuttal in isolation. Failure never propagates outward."""
        stage = RIDER_REBUTTAL_STAGE if side == "RIDER" else DRIVER_REBUTTAL_STAGE
        started_event, completed_event, failed_event = SIDE_EVENTS[side]
        agent = RebuttalAgent(provider, self._settings)
        started = time.monotonic()

        context = self._context_builder.build(
            case, analysis, advocate_context, advocate_run.rider, advocate_run.driver, side
        )
        if audit is not None:
            audit.record(
                REBUTTAL_CONTEXT_BUILT,
                side=side,
                case_id=context.case_id,
                dispute_type=context.dispute_type,
                own_verified_claims=len(context.own_verified_claims),
                opposing_verified_claims=len(context.opposing_verified_claims),
                evidence_count=len(context.evidence),
                rebuttal_round=context.rebuttal_round,
                max_rebuttal_rounds=context.max_rebuttal_rounds,
                rejected_claims_included=False,
            )
            audit.record(
                started_event, side=side, provider=provider.name, mode=self._settings.mode
            )

        try:
            argument = agent.rebut_with_trace(context)
        except (LlmProviderError, RebuttalAgentError) as error:
            _mark(pipeline, stage, "FAILED")
            if audit is not None:
                audit.record(
                    failed_event,
                    side=side,
                    failure_code=getattr(error, "code", type(error).__name__),
                    failure_category=_category(error),
                )
            return (
                RebuttalSideResult(
                    side=side,
                    status="FAILED",
                    failure_reason=_safe_failure_reason(error),
                    execution=self._failure_metadata(agent, error, started),
                ),
                None,
            )
        except Exception as error:  # noqa: BLE001 - isolation boundary
            _mark(pipeline, stage, "FAILED")
            if audit is not None:
                audit.record(
                    failed_event,
                    side=side,
                    failure_code=type(error).__name__,
                    failure_category="INTERNAL_ERROR",
                )
            return (
                RebuttalSideResult(
                    side=side,
                    status="FAILED",
                    failure_reason=f"Rebuttal run failed: {type(error).__name__}",
                    execution=self._failure_metadata(agent, error, started),
                ),
                None,
            )

        outcome = self._validation.validate(argument.output, context)
        _mark(pipeline, stage, "COMPLETE")
        completion = argument.completion
        generated = len(argument.output.responses)

        if audit is not None:
            audit.record(
                completed_event,
                side=side,
                duration_ms=completion.duration_ms,
                generated=generated,
                verified=len(outcome.verified_rebuttals),
                rejected=len(outcome.rejected_rebuttals),
            )
            if outcome.verified_rebuttals:
                audit.record(
                    REBUTTAL_VERIFIED,
                    side=side,
                    rebuttal_ids=[item.rebuttal_id for item in outcome.verified_rebuttals],
                    stances=sorted({item.stance for item in outcome.verified_rebuttals}),
                )
            if outcome.rejected_rebuttals:
                audit.record(
                    REBUTTAL_REJECTED,
                    side=side,
                    reasons=sorted({item.reason for item in outcome.rejected_rebuttals}),
                    rejected_count=len(outcome.rejected_rebuttals),
                )

        return (
            RebuttalSideResult(
                side=side,
                status="COMPLETE",
                overall_summary=argument.output.overall_summary,
                verified_rebuttals=outcome.verified_rebuttals,
                rejected_rebuttals=outcome.rejected_rebuttals,
                conceded_target_ids=outcome.conceded_target_ids(),
                execution=AgentCallMetadata(
                    provider=completion.provider_name,
                    model=completion.model_name,
                    duration_ms=completion.duration_ms,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    total_tokens=completion.total_tokens,
                    generated_claim_count=generated,
                    verified_claim_count=len(outcome.verified_rebuttals),
                    rejected_claim_count=len(outcome.rejected_rebuttals),
                    rejection_reasons=sorted(
                        {item.reason for item in outcome.rejected_rebuttals}
                    ),
                    malformed_output=False,
                ),
            ),
            argument.output,
        )

    @staticmethod
    def _blocked_reason(advocate_run: AdvocateRunResponse) -> list[str] | None:
        failed = [
            side.side
            for side in (advocate_run.rider, advocate_run.driver)
            if side.status != "COMPLETE"
        ]
        return failed or None

    def _failure_metadata(
        self, agent: RebuttalAgent, error: Exception, started: float
    ) -> AgentCallMetadata:
        """Record the failed attempt so the failure is visible with its cost."""
        completion = getattr(error, "completion", None)
        if isinstance(error, LlmProviderError):
            code = error.code
            malformed = False
        elif isinstance(error, RebuttalAgentError):
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


def _category(error: Exception) -> str:
    """Classify a rebuttal failure the same way the rest of the pipeline does."""
    if isinstance(error, LlmProviderError):
        return "PROVIDER_FAILURE"
    if isinstance(error, RebuttalAgentError):
        return (
            "MODEL_OUTPUT_FAILURE"
            if error.code in _UNUSABLE_OUTPUT_CODES
            else "INTERNAL_ERROR"
        )
    return "INTERNAL_ERROR"


def _safe_failure_reason(error: Exception) -> str:
    """Produce a failure reason that never leaks keys, headers, or traces."""
    if isinstance(error, LlmProviderError):
        return (
            f"Rebuttal provider unavailable ({error.provider}: {error.code}). "
            "The initial arguments and the deterministic analysis are unaffected."
        )
    if isinstance(error, RebuttalAgentError):
        return f"Rebuttal output could not be used ({error.code})."
    return "Rebuttal run failed."


__all__ = [
    "DRIVER_REBUTTAL_STAGE",
    "REBUTTAL_VERIFICATION_STAGE",
    "RIDER_REBUTTAL_STAGE",
    "RebuttalOrchestratorService",
    "RebuttalRunOutcome",
]
