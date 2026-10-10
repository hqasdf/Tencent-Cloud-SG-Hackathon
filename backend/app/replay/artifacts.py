"""Replay artefact schemas.

An artefact stores the **model's raw output** for one AI stage, plus the hashes
and versions needed to decide whether that output is still applicable.

The choice to store raw output rather than a verified/rejected split is the
central decision in this module, and it is what makes replay safe rather than
merely convenient. Consider the alternative: store ``verified_claims`` and
``rejected_claims`` as they were at capture time, then reload them. The stored
split is then an *assertion about a past verification run*, and a later change to
the verification rules would leave it silently wrong — a claim that the current
code would reject would still be fed to the Judge, because nothing recomputed it.

By storing the raw ``AdvocateOutput``, the verified/rejected split does not exist
on disk at all. It is produced by running the current verification service
against the current context, every time. So:

  * there is no ``verified`` flag to go stale, because there is no flag;
  * a change to the verification rules takes effect on the next replay, not the
    next capture;
  * rejected material is still fully reconstructible for audit and UI, because
    rejection is recomputed rather than stored.

The same reasoning applies to rebuttals and to the Judge. A stored artefact
records what the model said. Whether that is trustworthy is always decided now.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import AliasChoices, Field

from app.models.advocate import AdvocateOutput
from app.models.agent import AdvocateSide, AgentCallMetadata
from app.models.judge import JudgeOutput
from app.models.rebuttal import RebuttalOutput
from app.models.replay import ReplayModel

ReplayStage = Literal["ADVOCATES", "REBUTTALS", "JUDGE"]

STAGES: tuple[str, ...] = ("ADVOCATES", "REBUTTALS", "JUDGE")


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


class StoredRejection(ReplayModel):
    """One item verification refused at capture time."""

    item_id: str = Field(serialization_alias="itemId", validation_alias="itemId")
    reason: str


class VerificationFingerprint(ReplayModel):
    """What verification concluded at capture time — for comparison only.

    This is **not a trust input, and replay never reads it to decide whether
    anything is trusted.** Verification is recomputed from scratch on every
    load, and the recomputed result is the one that is used.

    It exists to answer a different question: *has the verdict changed?* Without
    it, a change to the verification rules would silently alter what a replay
    means. A stored argument that was verified last week and would be rejected
    today would replay into a different pipeline than the one that was captured,
    and nothing would say so.

    With it, that situation is reported as ``CLAIM_REVALIDATION_FAILED`` (or the
    rebuttal equivalent) and the replay is refused, which tells the developer the
    honest thing: the rules moved, re-capture. The stored fingerprint is an
    expectation about the past, never an assertion about the present.
    """

    verified_signatures: list[str] = Field(
        default_factory=list,
        serialization_alias="verifiedSignatures",
        validation_alias="verifiedSignatures",
    )
    rejected: list[StoredRejection] = Field(default_factory=list)

    @classmethod
    def of(
        cls, verified_signatures: list[str], rejected: list[tuple[str, str]]
    ) -> VerificationFingerprint:
        """Build a fingerprint. Entries are compact per-item signatures.

        For claims a signature is the claim ID; for rebuttals it is
        ``id|target|stance``, so a stance changing without the count changing is
        still detected.
        """
        return cls(
            verified_signatures=list(verified_signatures),
            rejected=[
                StoredRejection(item_id=item_id, reason=reason)
                for item_id, reason in rejected
            ],
        )

    def matches(
        self, verified_signatures: list[str], rejected: list[tuple[str, str]]
    ) -> bool:
        return self.verified_signatures == list(verified_signatures) and [
            (item.item_id, item.reason) for item in self.rejected
        ] == list(rejected)


class StoredAdvocateSide(ReplayModel):
    """One advocate's raw model output.

    ``output`` is required. A side that failed is not captured at all: the
    advocates block the rest of the pipeline when either fails, so a stored
    advocate artefact with a missing side would describe a case state that can
    never be replayed into anything useful — and storing it would create an
    artefact that looks reusable but is not.
    """

    side: AdvocateSide
    output: AdvocateOutput
    captured_verification: VerificationFingerprint = Field(
        default_factory=VerificationFingerprint,
        serialization_alias="capturedVerification",
        validation_alias="capturedVerification",
    )
    execution: AgentCallMetadata | None = None


class StoredAdvocatePayload(ReplayModel):
    rider: StoredAdvocateSide
    driver: StoredAdvocateSide


class StoredRebuttalSide(ReplayModel):
    """One side's raw rebuttal output.

    Unlike the advocates, ``output`` is optional and ``status`` is stored. A
    rebuttal failing is a normal, non-blocking outcome — the Judge still runs —
    so "this side produced nothing" is a state a replay has to be able to
    reproduce faithfully. Reconstructing it as a failure is honest; inventing an
    empty rebuttal to fill the gap would not be.
    """

    side: AdvocateSide
    status: Literal["COMPLETE", "FAILED", "NOT_RUN"]
    output: RebuttalOutput | None = None
    captured_verification: VerificationFingerprint = Field(
        default_factory=VerificationFingerprint,
        serialization_alias="capturedVerification",
        validation_alias="capturedVerification",
    )
    failure_reason: str | None = None
    execution: AgentCallMetadata | None = None


class StoredRebuttalPayload(ReplayModel):
    rider: StoredRebuttalSide
    driver: StoredRebuttalSide


class StoredJudgePayload(ReplayModel):
    """The Judge's raw output.

    Deliberately *not* a ``JudgeResult``. A ``JudgeResult`` carries ``status``,
    ``executable`` and the validated recommendation — all of which are derived
    from a verification run. Storing them would store the conclusion. Storing
    ``JudgeOutput`` stores only what the model said, and the conclusion is
    recomputed by the current validator against the current context.
    """

    output: JudgeOutput
    execution: AgentCallMetadata | None = None


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


class ReplayArtifactBase(ReplayModel):
    """The metadata every artefact carries.

    ``captured_mode``, ``captured_provider`` and ``captured_model`` record how the
    output was produced. They are deliberately **not** part of replay validity: an
    artefact is validated by re-running current deterministic code over it, so a
    model swap does not make a stored argument untrustworthy — it makes it old.
    The frontend is told which it is through ``ReplayMetadata`` rather than being
    left to assume.

    They *are* the test for **live eligibility**. An artefact may be replayed
    without all three — a mock capture is a perfectly valid artefact — but it may
    only be *reported as live output* when all three are present. Without that
    rule a mock capture, or a capture whose provider was never recorded, could be
    presented as evidence of what the model actually said. ``is_live_eligible``
    is the single place that question is answered.
    """

    replay_version: str = Field(
        serialization_alias="replayVersion", validation_alias="replayVersion"
    )
    schema_version: str = Field(
        serialization_alias="schemaVersion", validation_alias="schemaVersion"
    )
    created_at: str = Field(
        serialization_alias="createdAt", validation_alias="createdAt"
    )
    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: str = Field(
        serialization_alias="disputeType", validation_alias="disputeType"
    )
    deterministic_analysis_hash: str = Field(
        serialization_alias="deterministicAnalysisHash",
        validation_alias="deterministicAnalysisHash",
    )
    trusted_context_hash: str = Field(
        serialization_alias="trustedContextHash",
        validation_alias="trustedContextHash",
    )
    captured_mode: str | None = Field(
        default=None,
        serialization_alias="capturedMode",
        validation_alias=AliasChoices("capturedMode", "mode"),
    )
    captured_provider: str = Field(
        default="",
        serialization_alias="capturedProvider",
        validation_alias=AliasChoices("capturedProvider", "provider"),
    )
    captured_model: str | None = Field(
        default=None,
        serialization_alias="capturedModel",
        validation_alias=AliasChoices("capturedModel", "model"),
    )

    # Read-only aliases for the pre-``captured*`` names. Kept so existing callers
    # and artefacts written before this field set keep working unchanged.
    @property
    def provider(self) -> str:
        return self.captured_provider

    @property
    def model(self) -> str | None:
        return self.captured_model

    @property
    def is_live_eligible(self) -> bool:
        """May this capture be reported as live model output?

        All three must be present. ``captured_mode`` must be ``"real"`` — a mock
        capture is a valid artefact but is never live evidence. A blank provider
        or a missing model means the capture cannot name what produced it, which
        is exactly the case that must not be dressed up as live.
        """
        return (
            self.captured_mode == "real"
            and bool(self.captured_provider)
            and self.captured_provider != "mock"
            and bool(self.captured_model)
        )


class AdvocateArtifact(ReplayArtifactBase):
    stage: Literal["ADVOCATES"] = "ADVOCATES"
    payload: StoredAdvocatePayload


class RebuttalArtifact(ReplayArtifactBase):
    stage: Literal["REBUTTALS"] = "REBUTTALS"
    payload: StoredRebuttalPayload

    @property
    def is_live_eligible(self) -> bool:
        """A provenance-complete capture is not proof both rebuttals succeeded.

        Failure is intentionally storable for replay/audit, but an artefact with
        either side FAILED or NOT_RUN is not a successful *live stage* and must
        not be advertised as one. COMPLETE also requires the raw output that the
        current validator can check on replay.
        """
        return super().is_live_eligible and all(
            side.status == "COMPLETE" and side.output is not None
            for side in (self.payload.rider, self.payload.driver)
        )


class JudgeArtifact(ReplayArtifactBase):
    stage: Literal["JUDGE"] = "JUDGE"
    payload: StoredJudgePayload


ReplayArtifact = AdvocateArtifact | RebuttalArtifact | JudgeArtifact

_ARTIFACT_BY_STAGE: dict[str, type] = {
    "ADVOCATES": AdvocateArtifact,
    "REBUTTALS": RebuttalArtifact,
    "JUDGE": JudgeArtifact,
}

# The filename each stage is stored under, inside its case directory.
FILENAME_BY_STAGE: dict[str, str] = {
    "ADVOCATES": "advocates.json",
    "REBUTTALS": "rebuttals.json",
    "JUDGE": "judge.json",
}


def now_iso() -> str:
    """Capture time, UTC. The only timestamp anywhere in an artefact.

    It is metadata, not input: it is excluded from both hashes, so two captures
    of an unchanged case differ in this field and in nothing that affects
    validity.
    """
    return datetime.now(timezone.utc).isoformat()


def artifact_class_for_stage(stage: str) -> type | None:
    return _ARTIFACT_BY_STAGE.get(stage)


__all__ = [
    "AdvocateArtifact",
    "FILENAME_BY_STAGE",
    "JudgeArtifact",
    "RebuttalArtifact",
    "ReplayArtifact",
    "ReplayArtifactBase",
    "ReplayStage",
    "STAGES",
    "StoredAdvocatePayload",
    "StoredAdvocateSide",
    "StoredJudgePayload",
    "StoredRebuttalPayload",
    "StoredRebuttalSide",
    "StoredRejection",
    "VerificationFingerprint",
    "artifact_class_for_stage",
    "now_iso",
]
