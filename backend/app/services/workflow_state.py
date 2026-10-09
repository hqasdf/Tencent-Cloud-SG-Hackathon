"""Projects the results a run actually produced onto the workflow state machine.

This module is the only place that decides what a stage's state *is*. It reads
the objects the stages returned — never the presence of a response, never an
HTTP status — and it applies the pipeline's dependency rules explicitly:

* an incomplete initial advocate blocks the rebuttals and the Judge;
* an incomplete rebuttal blocks nothing;
* the deterministic resolution and its explanation are complete regardless of
  what any AI stage did, because they are computed from the case record.

Keeping those rules here, in one pure function, is what allows the response to
carry a single authoritative workflow object instead of leaving every consumer
to re-derive the rules from a flat stage list.
"""

from __future__ import annotations

from app.models.advocate import AdvocateRunResponse, AdvocateSideResult
from app.models.judge import JudgeResult
from app.models.rebuttal import RebuttalRunResponse, RebuttalSideResult
from app.models.workflow import (
    REASON_ADVOCATE_INPUT_INCOMPLETE,
    SideStages,
    StageState,
    WorkflowState,
)

_DETERMINISTIC_DETAIL = (
    "Calculated by code from the case record. No AI stage can change it."
)
_FINAL_DETAIL = (
    "The authoritative outcome. Preserved in full even when an AI stage fails, "
    "because it is computed without reference to them."
)
_EXPLANATION_DETAIL = (
    "Assembled by code from the decisive facts and the policy thresholds."
)


def build_workflow_state(
    *,
    advocate_run: AdvocateRunResponse,
    rebuttal_run: RebuttalRunResponse,
    judge_result: JudgeResult,
    blocked_sides: list[str] | None,
) -> WorkflowState:
    """Build the workflow object for one completed run.

    ``blocked_sides`` is the list of initial advocates that did not complete,
    exactly as the Judge gate computes it. It is passed in rather than
    recomputed so the workflow object and the Judge gate can never disagree
    about whether the case was decidable.
    """
    advocates = SideStages(
        rider=_advocate_stage(advocate_run.rider),
        driver=_advocate_stage(advocate_run.driver),
    )

    if blocked_sides is not None:
        # The dependency gate fired. Nothing downstream was attempted, and
        # saying so is the whole point: BLOCKED, not NOT_RUN.
        reason = REASON_ADVOCATE_INPUT_INCOMPLETE
        rebuttals = SideStages(
            rider=_blocked_stage(rebuttal_run.rider),
            driver=_blocked_stage(rebuttal_run.driver),
        )
        judge = StageState(
            status="BLOCKED",
            reason=reason,
            detail=judge_result.failure_reason
            or (
                "The Judge did not run because "
                + " and ".join(blocked_sides)
                + " produced no argument to decide on."
            ),
        )
    else:
        rebuttals = SideStages(
            rider=_rebuttal_stage(rebuttal_run.rider),
            driver=_rebuttal_stage(rebuttal_run.driver),
        )
        judge = _judge_stage(judge_result)

    return WorkflowState(
        deterministic=StageState(status="COMPLETE", detail=_DETERMINISTIC_DETAIL),
        advocates=advocates,
        rebuttals=rebuttals,
        judge=judge,
        final_resolution=StageState(status="COMPLETE", detail=_FINAL_DETAIL),
        explanation=StageState(status="COMPLETE", detail=_EXPLANATION_DETAIL),
    )


def _advocate_stage(side: AdvocateSideResult) -> StageState:
    if side.status == "COMPLETE":
        return StageState(
            status="COMPLETE",
            detail=(
                f"{len(side.verified_claims)} claim(s) passed verification; "
                f"{len(side.rejected_claims)} rejected by code."
            ),
        )
    return StageState(
        status="FAILED",
        detail=side.failure_reason,
        failure_code=_failure_code(side),
    )


def _rebuttal_stage(side: RebuttalSideResult) -> StageState:
    if side.status == "COMPLETE":
        return StageState(
            status="COMPLETE",
            detail=(
                f"{len(side.verified_rebuttals)} response(s) passed verification; "
                f"{len(side.rejected_rebuttals)} rejected by code."
            ),
        )
    if side.status == "FAILED":
        return StageState(
            status="FAILED",
            detail=side.failure_reason,
            failure_code=_failure_code(side),
        )
    # NOT_RUN without a blocking dependency is a deliberate skip, not a fault.
    return StageState(status="SKIPPED", detail=side.failure_reason)


def _blocked_stage(side: RebuttalSideResult) -> StageState:
    """A rebuttal that was never attempted because an advocate failed.

    The side's own message already explains why, so it is carried through
    unchanged rather than rewritten here — one sentence, one author.
    """
    return StageState(
        status="BLOCKED",
        reason=REASON_ADVOCATE_INPUT_INCOMPLETE,
        detail=side.failure_reason,
    )


def _judge_stage(judge: JudgeResult) -> StageState:
    if judge.status == "COMPLETE":
        return StageState(
            status="COMPLETE",
            detail="The Judge's advice passed deterministic validation.",
        )
    if judge.status == "PENDING_HUMAN_REVIEW":
        return StageState(
            status="PENDING_HUMAN_REVIEW",
            reason=judge.skip_reason,
            detail=(
                "The Judge's advice passed validation, but code has routed this "
                "case to a human. No AI stage can override that."
            ),
        )
    if judge.status == "FAILED":
        return StageState(
            status="FAILED",
            detail=judge.failure_reason,
            failure_code=_failure_code(judge),
        )
    # NOT_RUN reached without a blocking dependency. Report it as a deliberate
    # skip rather than inventing a failure.
    return StageState(
        status="SKIPPED", reason=judge.skip_reason, detail=judge.failure_reason
    )


def _failure_code(result: object) -> str | None:
    """Read the failure code off whichever carrier the result uses."""
    execution = getattr(result, "execution", None)
    return getattr(execution, "failure_code", None) if execution else None


__all__ = ["build_workflow_state"]
