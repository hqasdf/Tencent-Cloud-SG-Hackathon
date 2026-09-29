"""Stage 6B replay contracts.

Replay exists for one reason: a complete live Stage 6 run costs five model
calls, and iterating on the frontend, the Judge prompt or the demo does not need
to re-ask the advocates the same question about the same unchanged case.

The design is deliberately narrow, and one sentence states the whole boundary:

    **Replay substitutes the source of an AI stage's output. It substitutes
    nothing else.**

Concretely, that means:

* Only AI-stage artefacts are stored — the model's own ``AdvocateOutput``,
  ``RebuttalOutput`` and ``JudgeOutput``. Never a refund, never a confidence
  score, never an escalation reason, never a final action, never a
  ``resolutionRecommendation``. Those are recomputed from the current
  deterministic analysis on every run, replayed or not.

* **No stored trust flag exists.** The artefact holds the model's raw output and
  nothing about whether it passed. Verification is re-run against the *current*
  code and the *current* context on every load, so "verified" is never a stored
  assertion that could go stale. This goes one step further than refusing to
  trust an old ``validated=true`` bit: there is no bit to trust.

* Replay is invalidated by the trusted input changing. The artefact records a
  SHA-256 of an allow-list projection of the deterministic analysis and of the
  exact trusted context the agents saw. If either hash differs, replay is
  ``REPLAY_INVALID`` and **no live call is made in its place** — silently
  falling back would spend the quota the feature exists to protect and would
  make the run's provenance a lie.

The two enums below are the vocabulary the API, the CLI, the frontend and the
audit trail all share.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

ReplayMode = Literal["NONE", "ADVOCATES", "ADVOCATES_AND_REBUTTALS", "FULL_AI"]
"""How much of the AI chain is served from stored artefacts.

Named for the *stage boundary* that is replayed, not for the number of calls it
saves, because the call count is a consequence: ``ADVOCATES`` replays the two
advocates, so the two rebuttals and the Judge still run, which is three calls.
"""

REPLAY_MODES: tuple[str, ...] = (
    "NONE",
    "ADVOCATES",
    "ADVOCATES_AND_REBUTTALS",
    "FULL_AI",
)

# The mode that means "do exactly what Stage 6 always did". Spelled out as a
# constant so the default is visible at every call site rather than being an
# implicit falsy value.
NO_REPLAY: ReplayMode = "NONE"


# ---------------------------------------------------------------------------
# Validation reason codes
# ---------------------------------------------------------------------------

CASE_ID_MISMATCH = "CASE_ID_MISMATCH"
DISPUTE_TYPE_MISMATCH = "DISPUTE_TYPE_MISMATCH"
ANALYSIS_HASH_MISMATCH = "ANALYSIS_HASH_MISMATCH"
TRUSTED_CONTEXT_HASH_MISMATCH = "TRUSTED_CONTEXT_HASH_MISMATCH"
SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
REPLAY_VERSION_UNSUPPORTED = "REPLAY_VERSION_UNSUPPORTED"
MALFORMED_ARTIFACT = "MALFORMED_ARTIFACT"
MISSING_REQUIRED_STAGE = "MISSING_REQUIRED_STAGE"
CLAIM_REVALIDATION_FAILED = "CLAIM_REVALIDATION_FAILED"
REBUTTAL_REVALIDATION_FAILED = "REBUTTAL_REVALIDATION_FAILED"
JUDGE_REVALIDATION_FAILED = "JUDGE_REVALIDATION_FAILED"
ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"

REPLAY_INVALID = "REPLAY_INVALID"
"""The overall status when a requested replay could not be trusted.

Reported instead of falling back to live calls. See ``ReplayValidationResult``.
"""

REASON_CODES: tuple[str, ...] = (
    CASE_ID_MISMATCH,
    DISPUTE_TYPE_MISMATCH,
    ANALYSIS_HASH_MISMATCH,
    TRUSTED_CONTEXT_HASH_MISMATCH,
    SCHEMA_VERSION_MISMATCH,
    REPLAY_VERSION_UNSUPPORTED,
    MALFORMED_ARTIFACT,
    MISSING_REQUIRED_STAGE,
    CLAIM_REVALIDATION_FAILED,
    REBUTTAL_REVALIDATION_FAILED,
    JUDGE_REVALIDATION_FAILED,
    ARTIFACT_NOT_FOUND,
)


class ReplayModel(BaseModel):
    """Base for replay contracts. Built by us, never parsed from model output."""

    model_config = ConfigDict(populate_by_name=True)


class ReplayValidationResult(ReplayModel):
    """Whether a stored artefact may be used, and why not if it may not.

    ``reason_codes`` is a list rather than a single code because the failures are
    genuinely independent: an artefact can be for the wrong case *and* the wrong
    schema, and collapsing that to one code would hide half the problem from the
    person trying to fix it.
    """

    valid: bool
    reason_codes: list[str] = Field(
        default_factory=list, serialization_alias="reasonCodes", validation_alias="reasonCodes"
    )

    @property
    def status(self) -> str:
        return "REPLAY_VALID" if self.valid else REPLAY_INVALID


class ReplayMetadata(ReplayModel):
    """Provenance for one resolution run.

    This is what lets a reader tell a live model response from a replayed one,
    and it is attached to *every* run — including ``NONE`` — so the absence of
    replay is stated rather than inferred from a missing field. A frontend that
    had to guess would eventually guess wrong, and "we showed a stored argument
    as if the model had just produced it" is the one presentation error this
    feature must not make possible.
    """

    mode: ReplayMode = NO_REPLAY
    replayed: bool = False
    # camelCase on the wire, like every other multi-word field in this API. The
    # single-word fields above need no alias; these three do, and without it the
    # response said ``advocates_replayed`` while the frontend read
    # ``advocatesReplayed`` — so a replayed run rendered a REPLAYED badge with an
    # empty stage list.
    advocates_replayed: bool = Field(
        default=False, serialization_alias="advocatesReplayed", validation_alias="advocatesReplayed"
    )
    rebuttals_replayed: bool = Field(
        default=False, serialization_alias="rebuttalsReplayed", validation_alias="rebuttalsReplayed"
    )
    judge_replayed: bool = Field(
        default=False, serialization_alias="judgeReplayed", validation_alias="judgeReplayed"
    )
    valid: bool = True
    reason_codes: list[str] = Field(
        default_factory=list, serialization_alias="reasonCodes", validation_alias="reasonCodes"
    )
    current_analysis_hash: str = Field(
        default="", serialization_alias="currentAnalysisHash", validation_alias="currentAnalysisHash"
    )
    artifact_analysis_hash: str | None = Field(
        default=None, serialization_alias="artifactAnalysisHash", validation_alias="artifactAnalysisHash"
    )
    artifact_created_at: str | None = Field(
        default=None, serialization_alias="artifactCreatedAt", validation_alias="artifactCreatedAt"
    )
    artifact_version: str | None = Field(
        default=None, serialization_alias="artifactVersion", validation_alias="artifactVersion"
    )
    captured_provider: str | None = Field(
        default=None, serialization_alias="capturedProvider", validation_alias="capturedProvider"
    )
    captured_model: str | None = Field(
        default=None, serialization_alias="capturedModel", validation_alias="capturedModel"
    )
    note: str = ""

    @classmethod
    def live(cls, *, current_analysis_hash: str) -> ReplayMetadata:
        """The honest description of a run that used no stored material."""
        return cls(
            mode=NO_REPLAY,
            replayed=False,
            current_analysis_hash=current_analysis_hash,
            note="All AI stages ran against the configured provider in this request.",
        )

    @classmethod
    def invalid(
        cls, *, mode: ReplayMode, current_analysis_hash: str, reason_codes: list[str]
    ) -> ReplayMetadata:
        """A requested replay that was refused. No live call was made in its place."""
        return cls(
            mode=mode,
            replayed=False,
            valid=False,
            reason_codes=sorted(set(reason_codes)),
            current_analysis_hash=current_analysis_hash,
            note=(
                "Replay was requested but the stored artefact could not be trusted for "
                "this case state. No provider call was made in its place."
            ),
        )


class ResolutionRunRequest(ReplayModel):
    """Optional body for the resolution endpoint.

    Absent, or present with ``replayMode: "NONE"``, means the ordinary live
    pipeline. Replay is opt-in per request and is additionally gated behind a
    developer flag on the server, so the production path cannot become a replay
    by accident or by a client sending a field.
    """

    replay_mode: ReplayMode = Field(
        default=NO_REPLAY, serialization_alias="replayMode", validation_alias="replayMode"
    )


__all__ = [
    "ANALYSIS_HASH_MISMATCH",
    "ARTIFACT_NOT_FOUND",
    "CASE_ID_MISMATCH",
    "CLAIM_REVALIDATION_FAILED",
    "DISPUTE_TYPE_MISMATCH",
    "JUDGE_REVALIDATION_FAILED",
    "MALFORMED_ARTIFACT",
    "MISSING_REQUIRED_STAGE",
    "NO_REPLAY",
    "REASON_CODES",
    "REBUTTAL_REVALIDATION_FAILED",
    "REPLAY_INVALID",
    "REPLAY_MODES",
    "REPLAY_VERSION_UNSUPPORTED",
    "ReplayMetadata",
    "ReplayMode",
    "ReplayModel",
    "ReplayValidationResult",
    "ResolutionRunRequest",
    "SCHEMA_VERSION_MISMATCH",
    "TRUSTED_CONTEXT_HASH_MISMATCH",
]
