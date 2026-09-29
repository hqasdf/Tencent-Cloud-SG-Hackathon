"""Stage 6 orchestration: advocates, verification, one rebuttal round, Judge,
deterministic remedy.

Responsibility is split across small collaborators rather than merged into one
method, so each can be tested and reasoned about alone:

    AdvocateOrchestratorService   advocates + claim verification   (Stage 4, reused)
    RebuttalOrchestratorService   one bounded rebuttal round        (Stage 6)
    JudgeContextBuilder           allow-list projection for the Judge
    JudgeAgent                    one model call, parsed
    JudgeOutputValidationService  deterministic trust check
    DecisionExplanationService    deterministic "why this decision?"  (Stage 6)
    CounterfactualService         deterministic "what would change it?" (Stage 6)
    ResolutionEngine et al.       the authoritative numbers           (untouched)

This service only sequences them. It performs no calculation of its own, and it
cannot alter a deterministic value: the ``DeterministicResolution`` it returns is
a direct projection of the ``CaseAnalysisResponse`` it was handed.

Three failure behaviours are deliberate and load-bearing.

*Missing advocate input* — if either initial advocate failed, neither rebuttal
runs and the Judge does not run. The Judge reports ``NOT_RUN`` with
``ADVOCATE_INPUT_INCOMPLETE``, which is distinct from ``FAILED``: nothing was
attempted, so there is nothing to retry.

*Missing rebuttal* — if a rebuttal fails or is entirely rejected, the Judge still
runs with whatever verified material exists. The asymmetry with the rule above is
the point: the initial arguments are the case, while rebuttals are responses to
it. A missing rebuttal is a side that declined to respond, not a case that cannot
be decided.

*Judge failure* — if the Judge fails or its output is rejected, the advocates,
their verified claims, the verified rebuttals and the whole deterministic
analysis remain intact and visible. The Judge block reports ``FAILED``. No result
is fabricated, and the deterministic layer's own human-review decision survives
untouched.
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
from app.models.rebuttal import RebuttalRunResponse
from app.models.replay import NO_REPLAY, ReplayMetadata, ReplayMode
from app.replay.hashing import deterministic_analysis_hash
from app.replay.plan import ReplayPlan, build_plan
from app.replay.service import ReplayService
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
from app.services.counterfactual import CounterfactualService
from app.services.explanation import DecisionExplanationService
from app.services.rebuttal_orchestrator import RebuttalOrchestratorService

JUDGE_STAGE = "JUDGE"

_ADVOCATE_INPUT_INCOMPLETE = "ADVOCATE_INPUT_INCOMPLETE"

# Output error codes that mean the model answered but the answer was unusable.
_UNUSABLE_OUTPUT_CODES = frozenset(
    {"MALFORMED_JSON", "SCHEMA_VALIDATION_FAILED", "OUTPUT_TRUNCATED"}
)


class ResolutionOrchestratorService:
    """Runs the full Stage 6 pipeline for one case."""

    def __init__(
        self,
        *,
        settings: AgentSettings | None = None,
        advocate_orchestrator: AdvocateOrchestratorService | None = None,
        advocate_context_builder: AdvocateContextBuilder | None = None,
        rebuttal_orchestrator: RebuttalOrchestratorService | None = None,
        judge_context_builder: JudgeContextBuilder | None = None,
        judge_validation: JudgeOutputValidationService | None = None,
        explanation_service: DecisionExplanationService | None = None,
        counterfactual_service: CounterfactualService | None = None,
        replay_service: ReplayService | None = None,
        provider: LlmProvider | None = None,
    ) -> None:
        self._settings = settings or AgentSettings.from_environment()
        self._advocate_context_builder = advocate_context_builder or AdvocateContextBuilder()
        # The provider is threaded through to the advocates and the rebuttals as
        # well as the Judge. Without this the three stages would resolve their own
        # providers, so a caller injecting one would silently only affect the
        # Judge — and an offline test could reach a live model through the other
        # two paths.
        self._advocates = advocate_orchestrator or AdvocateOrchestratorService(
            settings=self._settings,
            context_builder=self._advocate_context_builder,
            provider=provider,
        )
        self._rebuttals = rebuttal_orchestrator or RebuttalOrchestratorService(
            settings=self._settings, provider=provider
        )
        self._judge_context_builder = judge_context_builder or JudgeContextBuilder()
        self._judge_validation = judge_validation or JudgeOutputValidationService()
        self._explanations = explanation_service or DecisionExplanationService()
        self._counterfactuals = counterfactual_service or CounterfactualService()
        self._replays = replay_service or ReplayService(settings=self._settings)
        self._provider = provider

    def run(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        *,
        replay_mode: ReplayMode = NO_REPLAY,
        capture_stages: frozenset[str] = frozenset(),
    ) -> CaseResolutionResponse:
        """Run the pipeline, optionally serving some AI stages from storage.

        The defaults reproduce Stage 6 exactly: no replay, no capture. Both are
        opt-in, and neither is reachable from the production endpoint — see
        ``app.api.cases``. A replay that cannot be trusted raises
        ``ReplayRefused`` rather than falling back to a live call.
        """
        audit = AuditTrail()
        plan = build_plan(replay_mode)
        current_hash = deterministic_analysis_hash(case, analysis)

        # One projection, shared by the advocates, the rebuttals and the Judge, so
        # the three cannot be reasoning over different facts. The deterministic
        # analysis arrives already computed and is never recalculated or mutated.
        context = self._advocate_context_builder.build(case, analysis)

        # Stage 4. Either replayed from storage or run live; either way the
        # response object is the one Stage 4 always produced, and the claim
        # verification inside it is run by the current code.
        provenance: dict[str, object] = {}
        if plan.advocates_replayed:
            advocate_run, advocate_artifact = self._replays.replay_advocates(
                case, analysis, context, audit
            )
            provenance["ADVOCATES"] = advocate_artifact
        else:
            outcome = self._advocates.run_with_raw(case, analysis, context)
            advocate_run = outcome.response
            if "ADVOCATES" in capture_stages:
                # Captured only after verification has run, so an artefact never
                # records a stage that had not been checked.
                self._replays.capture_advocates(
                    case, analysis, context, advocate_run, outcome.raw_outputs, audit
                )

        # Stage 6. Exactly one round: there is no loop here, and nothing in the
        # rebuttal context could name a rebuttal as a target, so a second round
        # is unrepresentable rather than merely disabled.
        if plan.rebuttals_replayed:
            rebuttal_run, rebuttal_artifact = self._replays.replay_rebuttals(
                case, analysis, context, advocate_run, audit
            )
            provenance["REBUTTALS"] = rebuttal_artifact
        else:
            rebuttal_outcome = self._rebuttals.run_with_raw(
                case, analysis, context, advocate_run, audit
            )
            rebuttal_run = rebuttal_outcome.response
            if "REBUTTALS" in capture_stages:
                self._replays.capture_rebuttals(
                    case,
                    analysis,
                    context,
                    rebuttal_run,
                    rebuttal_outcome.raw_outputs,
                    audit,
                )

        pipeline = _with_rebuttal_stages(list(advocate_run.pipeline), rebuttal_run)

        judge_result = self._run_judge(
            case,
            analysis,
            advocate_run,
            rebuttal_run,
            context,
            audit,
            replay=plan.judge_replayed,
            capture="JUDGE" in capture_stages,
            provenance=provenance,
        )
        _mark(pipeline, JUDGE_STAGE, _stage_status(judge_result.status))

        deterministic = self._deterministic(analysis)

        return CaseResolutionResponse(
            case_id=case.id,
            dispute_type=case.dispute_type,
            rider=advocate_run.rider,
            driver=advocate_run.driver,
            rebuttals=rebuttal_run,
            agent_run=advocate_run.agent_run,
            verification_summary=advocate_run.verification_summary,
            judge=judge_result,
            deterministic_resolution=deterministic,
            # Both of these are pure projections of deterministic data. They are
            # built after the Judge only so the explanation can report whether the
            # AI's advice agreed with the code's decision.
            explanation=self._explanations.build(
                analysis, advocate_run, rebuttal_run, judge_result
            ),
            counterfactual=self._counterfactuals.build(analysis),
            replay_metadata=_replay_metadata(plan, current_hash, provenance),
            pipeline=pipeline,
            audit=audit.events,
        )

    # -- Judge ------------------------------------------------------------

    def _run_judge(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        advocate_run: AdvocateRunResponse,
        rebuttal_run: RebuttalRunResponse,
        advocate_context: AgentCaseContext,
        audit: AuditTrail,
        *,
        replay: bool = False,
        capture: bool = False,
        provenance: dict[str, object] | None = None,
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
            rebuttal_run,
        )
        audit.record(
            JUDGE_CONTEXT_BUILT,
            case_id=context.case_id,
            dispute_type=context.dispute_type,
            evidence_count=len(context.evidence),
            policy_rule_count=len(context.applicable_policy.rules),
            rider_verified_claims=context.rider.verified_claim_count,
            driver_verified_claims=context.driver.verified_claim_count,
            rider_verified_rebuttals=len(context.verified_rider_rebuttals),
            driver_verified_rebuttals=len(context.verified_driver_rebuttals),
            resolution_mode=context.resolution_mode,
            rejected_claims_included=False,
            rejected_rebuttals_included=False,
        )

        if replay:
            # No provider call. The stored output is still put through the current
            # validator below, against the context just built for this request —
            # a stored "was valid" is never taken as read.
            stored_output, stored_execution, judge_artifact = self._replays.replay_judge(
                context, audit
            )
            output = stored_output
            execution = stored_execution
            if provenance is not None:
                provenance["JUDGE"] = judge_artifact
        else:
            started = time.monotonic()
            audit.record(
                JUDGE_STARTED, provider=self._provider_name(), mode=self._settings.mode
            )
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
            output = decision.output
            execution = _completion_metadata(decision.completion, self._settings)

        validation = self._judge_validation.validate(output, context)
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
                execution=execution,
            )

        audit.record(JUDGE_OUTPUT_VALIDATED, issue_count=0)

        if capture and not replay:
            # Captured only on the validated path, so a stored Judge artefact
            # always implies "this passed when it was captured".
            self._replays.capture_judge(
                case, analysis, advocate_context, output, execution, audit
            )

        pending = context.resolution_mode == "HUMAN_REVIEW"
        if pending:
            audit.record(PENDING_HUMAN_REVIEW, reason="DETERMINISTIC_RESOLUTION_MODE")

        return JudgeResult(
            status="PENDING_HUMAN_REVIEW" if pending else "COMPLETE",
            # A recommendation is only marked executable when code has already
            # decided this case may be automated. The Judge never promotes its
            # own advice.
            executable=not pending,
            recommended_outcome=output.recommended_outcome,
            accepted_rider_claim_ids=output.accepted_rider_claim_ids,
            accepted_driver_claim_ids=output.accepted_driver_claim_ids,
            rejected_rider_claim_ids=output.rejected_rider_claim_ids,
            rejected_driver_claim_ids=output.rejected_driver_claim_ids,
            reasoning_summary=output.reasoning_summary,
            considered_rider_rebuttal_ids=output.considered_rider_rebuttal_ids,
            considered_driver_rebuttal_ids=output.considered_driver_rebuttal_ids,
            evidence_ids=output.evidence_ids,
            policy_rule_ids=output.policy_rule_ids,
            uncertainties=output.uncertainties,
            requires_human_review=output.requires_human_review,
            execution=execution,
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


def _with_rebuttal_stages(
    pipeline: list[PipelineStage], rebuttal_run: RebuttalRunResponse
) -> list[PipelineStage]:
    """Splice the Stage 6 stages in immediately before the Judge stage.

    The advocate orchestrator returns a pipeline that already ends in JUDGE.
    Rather than rebuilding that list here — which would duplicate knowledge of
    the earlier stages — the rebuttal stages are inserted ahead of the Judge, so
    the order always reflects execution order regardless of what the advocate
    orchestrator produced.
    """
    insert_at = next(
        (index for index, item in enumerate(pipeline) if item.stage == JUDGE_STAGE),
        len(pipeline),
    )
    return [*pipeline[:insert_at], *rebuttal_run.pipeline, *pipeline[insert_at:]]


def _mark(pipeline: list[PipelineStage], stage: str, status: str) -> None:
    for item in pipeline:
        if item.stage == stage:
            item.status = status  # type: ignore[assignment]
            return


_REPLAYED_STAGE_LABELS: dict[str, str] = {
    "ADVOCATES": "advocates",
    "REBUTTALS": "rebuttals",
    "JUDGE": "judge",
}


def _replay_metadata(
    plan: ReplayPlan, current_hash: str, provenance: dict[str, object]
) -> ReplayMetadata:
    """Describe where this run's AI material came from.

    A live run reports ``NONE`` explicitly rather than omitting the field, so a
    consumer never has to treat "absent" as "live" — the two are different
    statements and only one of them is safe to assume.
    """
    if plan.mode == NO_REPLAY:
        return ReplayMetadata.live(current_analysis_hash=current_hash)

    artifacts = [artifact for artifact in provenance.values() if artifact is not None]
    first = artifacts[0] if artifacts else None
    replayed = ", ".join(
        _REPLAYED_STAGE_LABELS[stage]
        for stage in ("ADVOCATES", "REBUTTALS", "JUDGE")
        if stage in provenance
    )
    return ReplayMetadata(
        mode=plan.mode,
        replayed=True,
        advocates_replayed=plan.advocates_replayed,
        rebuttals_replayed=plan.rebuttals_replayed,
        judge_replayed=plan.judge_replayed,
        valid=True,
        current_analysis_hash=current_hash,
        artifact_analysis_hash=getattr(first, "deterministic_analysis_hash", None),
        artifact_created_at=getattr(first, "created_at", None),
        artifact_version=getattr(first, "replay_version", None),
        captured_provider=getattr(first, "provider", None),
        captured_model=getattr(first, "model", None),
        note=(
            f"Replayed: {replayed}. AI output captured previously and revalidated "
            "against the current deterministic case state. No model was called for "
            "these stages; refund, confidence, escalation and the resolution mode "
            "were recomputed from the current analysis."
        ),
    )


__all__ = ["JUDGE_STAGE", "ResolutionOrchestratorService"]
