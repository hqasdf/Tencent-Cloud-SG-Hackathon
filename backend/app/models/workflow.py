"""The explicit pipeline state machine.

Before this module existed, a consumer could only learn what happened by reading
a flat ``pipeline`` list of ``COMPLETE`` / ``FAILED`` / ``NOT_RUN`` entries and
inferring dependencies from it. That is enough to describe *what ran*, but not
*why something did not*, and the difference matters: "the rebuttals did not run
because the advocates failed" and "the rebuttals did not run because the case
never asked for them" are the same three letters in the old vocabulary.

The states here are deliberately explicit about that difference.

    NOT_STARTED           nothing has been attempted yet
    RUNNING               in flight (a live run is synchronous today, so this
                          is reserved for a future streaming/async producer)
    COMPLETE              the stage ran and produced a usable result
    FAILED                the stage ran and did not produce a usable result
    BLOCKED               the stage was *not* attempted because a dependency
                          failed; retrying it alone cannot help
    SKIPPED               the stage was deliberately not attempted, and no
                          dependency is at fault
    PENDING_HUMAN_REVIEW  the stage ran successfully and reached a conclusion
                          that only a human may act on

``BLOCKED`` is the load-bearing addition. It is the state that lets the UI say
"cross-examination did not happen because the arguments are incomplete" instead
of the misleading "not run", and it is what stops a progress indicator from
counting a stage that never executed as ready.

Only the orchestrator writes these states, and it writes them from the objects
the stages actually returned — never from the mere existence of a response.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StageStatus = Literal[
    "NOT_STARTED",
    "RUNNING",
    "COMPLETE",
    "FAILED",
    "BLOCKED",
    "SKIPPED",
    "PENDING_HUMAN_REVIEW",
]

#: The states in which a stage produced a usable result and the workflow may
#: move past it. ``PENDING_HUMAN_REVIEW`` counts: the stage ran, and code — not
#: the model — decided a human must sign off.
SETTLED_OK: frozenset[str] = frozenset({"COMPLETE", "PENDING_HUMAN_REVIEW"})

#: The states that mean the stage will never produce a result in this run.
SETTLED_NOT_OK: frozenset[str] = frozenset({"FAILED", "BLOCKED", "SKIPPED"})

#: Machine-readable reason codes. Kept as constants so the UI can key off them
#: without matching on prose.
REASON_ADVOCATE_INPUT_INCOMPLETE = "ADVOCATE_INPUT_INCOMPLETE"
REASON_REBUTTAL_INPUT_INCOMPLETE = "REBUTTAL_INPUT_INCOMPLETE"


class WorkflowModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class StageState(WorkflowModel):
    """One stage's outcome, as the orchestrator observed it.

    ``detail`` is a sentence written for an operator. It is produced by code
    from the stage's own result, never by a model, and it never contains a
    credential, a header or a stack trace.
    """

    status: StageStatus
    reason: str | None = None
    detail: str | None = None
    failure_code: str | None = Field(
        default=None, serialization_alias="failureCode", validation_alias="failureCode"
    )


class SideStages(WorkflowModel):
    """The two independent sides of a paired stage (advocates, or rebuttals)."""

    rider: StageState
    driver: StageState

    @property
    def both_settled_ok(self) -> bool:
        return self.rider.status in SETTLED_OK and self.driver.status in SETTLED_OK

    @property
    def any_failed(self) -> bool:
        return (
            self.rider.status in SETTLED_NOT_OK
            or self.driver.status in SETTLED_NOT_OK
        )


class WorkflowState(WorkflowModel):
    """The single source of truth for workflow status.

    The frontend renders this and does not infer status from the presence of a
    response object. Every field is derived from the object the stage returned,
    so "we received a 200" can never be mistaken for "the stage completed".
    """

    deterministic: StageState
    advocates: SideStages
    rebuttals: SideStages
    judge: StageState
    final_resolution: StageState = Field(
        serialization_alias="finalResolution", validation_alias="finalResolution"
    )
    explanation: StageState

    @property
    def advocates_complete(self) -> bool:
        return self.advocates.both_settled_ok

    @property
    def rebuttals_blocked(self) -> bool:
        return (
            self.rebuttals.rider.status == "BLOCKED"
            and self.rebuttals.driver.status == "BLOCKED"
        )


__all__ = [
    "REASON_ADVOCATE_INPUT_INCOMPLETE",
    "REASON_REBUTTAL_INPUT_INCOMPLETE",
    "SETTLED_NOT_OK",
    "SETTLED_OK",
    "SideStages",
    "StageState",
    "StageStatus",
    "WorkflowModel",
    "WorkflowState",
]
