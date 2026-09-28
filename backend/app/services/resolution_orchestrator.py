"""Stage 5 orchestration: advocates, verification, Judge, deterministic remedy.

Responsibility is split across small collaborators rather than merged into one
method, so each can be tested and reasoned about alone:

    AdvocateOrchestratorService   advocates + claim verification   (Stage 4, reused)
    JudgeContextBuilder           allow-list projection for the Judge
    JudgeAgent                    one model call, parsed
    JudgeOutputValidationService  deterministic trust check
    ResolutionEngine et al.       the authoritative numbers           (untouched)

This service only sequences them. It performs no calculation of its own, and it
cannot alter a deterministic value: the ``DeterministicResolution`` it returns is
a direct projection of the ``CaseAnalysisResponse`` it was handed.

Two failure behaviours are deliberate and load-bearing.

*Missing advocate input* — if either side failed, the Judge does not run. Running
it anyway would ask the Judge to weigh a dispute where only one party argued,
while presenting the result as though both had. The Judge reports ``NOT_RUN``
with ``ADVOCATE_INPUT_INCOMPLETE``, which is distinct from ``FAILED``: nothing
was attempted, so there is nothing to retry.

*Judge failure* — if the Judge fails or its output is rejected, the advocates,
their verified claims, and the whole deterministic analysis remain intact and
visible. The Judge block reports ``FAILED``. No result is fabricated, and the
deterministic layer's own human-review decision survives untouched.
"""

from __future__ import annotations

import time

from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.judge_agent import JudgeAgent, JudgeAgentError
from app.agents.judge_context_builder import JudgeContextBuilder
from app.agents.judge_validation import JudgeOutputValidationService
from app.agents.provider import LlmProvider, LlmProviderError
from app.agents.providers.registry import get_provider
from app.models.advocate import AdvocateRunResponse, AdvocateSideResult, PipelineStage
from app.models.analysis import CaseAnalysisResponse
from app.models.agent import AgentCallMetadata, AgentCaseContext
from app.models.case import DisputeCase
from app.models.judge import (
    CaseResolutionResponse,
    DeterministicResolution,
    JudgeResult,
    JudgeValidationIssue,
)
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
    AuditTrail,
)

JUDGE_STAGE = "JUDGE"

_ADVOCATE_INPUT_INCOMPLETE = "ADVOCATE_INPUT_INCOMPLETE"

# Output error codes that mean the model answered but the answer was unusable.
_UNUSABLE_OUTPUT_CODES = frozenset(
    {"MALFORMED_JSON", "SCHEMA_VALIDATION_FAILED", "OUTPUT_TRUNCATED"}
)


class ResolutionOrchestratorService:
    """Runs the full Stage 5 pipeline for one case."""

    def __init__(
        self,
        *,
        settings: AgentSettings | None = None,
        advocate_orchestrator: AdvocateOrchestratorService | None = None,
        advocate_context_builder: AdvocateContextBuilder | None = None,
        judge_context_builder: JudgeContextBuilder | None = None,
        judge_validation: JudgeOutputValidationService | None = None,
        provider: LlmProvider | None = None,
    ) -> None:
        self._settings = settings or AgentSettings.from_environment()
        self._advocate_context_builder = advocate_context_builder or AdvocateContextBuilder()
        # The provider is threaded through to the advocates as well as the Judge.
        # Without this the two halves of the pipeline would resolve their own
        # providers, so a caller injecting one would silently only affect the
        # Judge — and an offline test could reach a live model through the
        # advocate path.
        self._advocates = advocate_orchestrator or AdvocateOrchestratorService(
            settings=self._settings,
            context_builder=self._advocate_context_builder,
            provider=provider,
        )
        self._judge_context_builder = judge_context_builder or JudgeContextBuilder()
        self._judge_validation = judge_validation or JudgeOutputValidationService()
        self._provider = provider

    def run(self, case: DisputeCase, analysis: CaseAnalysisResponse) -> CaseResolutionResponse:
        audit = AuditTrail()

        # One projection, shared by the advocates and the Judge, so the two
        # cannot be reasoning over different facts. The deterministic analysis
        # arrives already computed and is never recalculated or mutated.
        context = self._advocate_context_builder.build(case, analysis)

        # Stage 4, reused unchanged. The advocate run owns its own failure
        # isolation and its own deterministic-analysis guarantee.
        advocate_run = self._advocates.run(case, analysis, context)

        pipeline = list(advocate_run.pipeline)
        judge_result = self._run_judge(case, analysis, advocate_run, context, audit)
        _mark(pipeline, JUDGE_STAGE, _stage_status(judge_result.status))

        return CaseResolutionResponse(
            case_id=case.id,
            dispute_type=case.dispute_type,
            rider=advocate_run.rider,
            driver=advocate_run.driver,
            agent_run=advocate_run.agent_run,
            verification_summary=advocate_run.verification_summary,
            judge=judge_result,
            deterministic_resolution=self._deterministic(analysis),
            pipeline=pipeline,
            audit=audit.events,
        )

    # -- Judge ------------------------------------------------------------

    def _run_judge(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        advocate_run: AdvocateRunResponse,
        advocate_context: AgentCaseContext,
        audit: AuditTrail,
    ) -> JudgeResult:
        blocked = self._blocked_reason(advocate_run)
        if blocked is not None:
            audit.record(
                JUDGE_NOT_RUN,
                reason=_ADVOCATE_INPUT_INCOMPLETE,
                failed_sides=blocked,
            )
            return JudgeResult(
                status="NOT_RUN",
                skip_reason=_ADVOCATE_INPUT_INCOMPLETE,
                failure_reason=(
                    "Judge did not run: "
                    + " and ".join(blocked)
                    + " did not produce an argument. Deciding a dispute with one side "
                    "absent would misrepresent the record."
                ),
            )

        context = self._judge_context_builder.build(
            case,
            analysis,
            advocate_context,
            advocate_run.rider,
            advocate_run.driver,
        )
        audit.record(
            JUDGE_CONTEXT_BUILT,
            case_id=context.case_id,
            dispute_type=context.dispute_type,
            evidence_count=len(context.evidence),
            policy_rule_count=len(context.applicable_policy.rules),
            rider_verified_claims=context.rider.verified_claim_count,
            driver_verified_claims=context.driver.verified_claim_count,
            resolution_mode=context.resolution_mode,
            rejected_claims_included=False,
        )

        started = time.monotonic()
        audit.record(JUDGE_STARTED, provider=self._provider_name(), mode=self._settings.mode)

        try:
            decision = self._judge_agent().judge_with_trace(context)
        except (LlmProviderError, JudgeAgentError) as error:
            return self._failed(error, started, audit, context)
        except Exception as error:  # noqa: BLE001 - isolation boundary
            return self._failed(error, started, audit, context)

        audit.record(
            JUDGE_COMPLETED,
            duration_ms=decision.completion.duration_ms,
            total_tokens=decision.completion.total_tokens,
            attempt_count=decision.completion.attempt_count,
        )

        validation = self._judge_validation.validate(decision.output, context)
        if not validation.valid:
            audit.record(
                JUDGE_OUTPUT_REJECTED,
                issue_codes=sorted({issue.code for issue in validation.issues}),
                issue_count=len(validation.issues),
            )
            return JudgeResult(
                status="FAILED",
                recommended_outcome=None,
                failure_reason=(
                    "Judge output was rejected by deterministic validation "
                    f"({len(validation.issues)} issue(s)). It was not repaired or "
                    "partially accepted."
                ),
                validation_issues=validation.issues,
                execution=_completion_metadata(decision.completion, self._settings),
            )

        audit.record(JUDGE_OUTPUT_VALIDATED, issue_count=0)

        pending = context.resolution_mode == "HUMAN_REVIEW"
        if pending:
            audit.record(PENDING_HUMAN_REVIEW, reason="DETERMINISTIC_RESOLUTION_MODE")

        return JudgeResult(
            status="PENDING_HUMAN_REVIEW" if pending else "COMPLETE",
            # A recommendation is only marked executable when code has already
            # decided this case may be automated. The Judge never promotes its
            # own advice.
            executable=not pending,
            recommended_outcome=decision.output.recommended_outcome,
            accepted_rider_claim_ids=decision.output.accepted_rider_claim_ids,
            accepted_driver_claim_ids=decision.output.accepted_driver_claim_ids,
            rejected_rider_claim_ids=decision.output.rejected_rider_claim_ids,
            rejected_driver_claim_ids=decision.output.rejected_driver_claim_ids,
            reasoning_summary=decision.output.reasoning_summary,
            evidence_ids=decision.output.evidence_ids,
            policy_rule_ids=decision.output.policy_rule_ids,
            uncertainties=decision.output.uncertainties,
            requires_human_review=decision.output.requires_human_review,
            execution=_completion_metadata(decision.completion, self._settings),
        )

    @staticmethod
    def _blocked_reason(advocate_run: AdvocateRunResponse) -> list[str] | None:
        failed = [
            side.side
            for side in (advocate_run.rider, advocate_run.driver)
            if side.status != "COMPLETE"
        ]
        return failed or None

    def _judge_agent(self) -> JudgeAgent:
        provider = self._provider or get_provider(self._settings)
        return JudgeAgent(provider, self._settings)

    def _provider_name(self) -> str:
        if self._provider is not None:
            return self._provider.name
        return self._settings.provider

    def _failed(
        self,
        error: Exception,
        started: float,
        audit: AuditTrail,
        context: object,
    ) -> JudgeResult:
        """Record a Judge failure without disturbing anything deterministic."""
        completion = getattr(error, "completion", None)
        code = getattr(error, "code", type(error).__name__)
        audit.record(
            JUDGE_FAILED,
            failure_code=code,
            failure_category=_category(error),
        )
        return JudgeResult(
            status="FAILED",
            recommended_outcome=None,
            failure_reason=_safe_failure_reason(error),
            execution=_completion_metadata(completion, self._settings, started),
        )

    # -- deterministic layer ---------------------------------------------

    @staticmethod
    def _deterministic(analysis: CaseAnalysisResponse) -> DeterministicResolution:
        """Project the authoritative numbers. Nothing here is influenced by AI."""
        recommendation = analysis.resolution_recommendation
        return DeterministicResolution(
            ruling=recommendation.ruling,
            recommended_action=recommendation.recommended_action,
            refund_amount=recommendation.refund_amount,
            currency=recommendation.currency,
            resolution_mode=analysis.resolution_mode,
            confidence=analysis.confidence.overall_confidence,
            escalation_reasons=list(analysis.escalation_reasons),
            explanation=recommendation.explanation,
            counterfactual_explanation=recommendation.counterfactual_explanation,
        )


def _completion_metadata(
    completion: object,
    settings: AgentSettings,
    started: float | None = None,
) -> AgentCallMetadata:
    if completion is None:
        return AgentCallMetadata(
            provider=settings.provider,
            model=settings.model if settings.mode == "real" else None,
            duration_ms=int(((time.monotonic() - started) * 1000)) if started else 0,
        )
    return AgentCallMetadata(
        provider=getattr(completion, "provider_name", settings.provider),
        model=getattr(completion, "model_name", None),
        duration_ms=getattr(completion, "duration_ms", 0),
        input_tokens=getattr(completion, "input_tokens", None),
        output_tokens=getattr(completion, "output_tokens", None),
        total_tokens=getattr(completion, "total_tokens", None),
    )


def _category(error: Exception) -> str:
    if isinstance(error, LlmProviderError):
        return "PROVIDER_FAILURE"
    if isinstance(error, JudgeAgentError):
        return (
            "MODEL_OUTPUT_FAILURE"
            if error.code in _UNUSABLE_OUTPUT_CODES
            else "INTERNAL_ERROR"
        )
    return "INTERNAL_ERROR"


def _safe_failure_reason(error: Exception) -> str:
    """A failure reason that never leaks keys, headers, or traces."""
    if isinstance(error, LlmProviderError):
        return (
            f"Judge provider unavailable ({error.provider}: {error.code}). "
            "The advocates, their verified claims and the deterministic analysis "
            "are unaffected."
        )
    if isinstance(error, JudgeAgentError):
        return f"Judge output could not be used ({error.code})."
    return "Judge run failed."


def _stage_status(judge_status: str) -> str:
    """Map the Judge's own status onto the pipeline stage's coarser vocabulary.

    ``PENDING_HUMAN_REVIEW`` means the stage *ran successfully* and reached a
    non-executable conclusion, so the stage is COMPLETE. Reporting it as anything
    else would conflate "the Judge could not run" with "the Judge ran and code
    decided a human must sign off". The distinction is preserved in
    ``judge.status``, which is what the UI and the audit trail read.
    """
    if judge_status == "PENDING_HUMAN_REVIEW":
        return "COMPLETE"
    return judge_status


def _mark(pipeline: list[PipelineStage], stage: str, status: str) -> None:
    for item in pipeline:
        if item.stage == stage:
            item.status = status  # type: ignore[assignment]
            return


__all__ = ["JUDGE_STAGE", "ResolutionOrchestratorService"]
