"""Resume a validation session from the checkpoints it already wrote.

A checkpoint is **not a new file format**. It is the replay artefact the
orchestrator already writes for every successful AI stage --
``replay_artifacts/<case>/<stage>.json`` -- which already carries the case id,
the schema and format versions, the deterministic analysis hash, the trusted
context hash, the provider/model/mode provenance, the raw model output, the
execution metadata and the verification fingerprint. Inventing a second
checkpoint format would mean two things to keep in step, and two places for a
stale artefact to hide.

Three properties this module deliberately does **not** invent, because the
replay layer already enforces them:

* **Revalidation under the current code.** An artefact stores the model's raw
  output and nothing else; the verified/rejected split is recomputed on every
  load, by the *current* verification service. ``ReplayService`` is therefore the
  authority on whether a checkpoint is still usable, and this module *calls it*
  rather than reimplementing it. A second copy of a validator drifts, and the
  weaker copy becomes the effective guarantee -- so there is exactly one copy,
  and it is the one the orchestrator will use.

* **Dependency invalidation.** ``ReplayMode`` is a *prefix* chain --
  ``ADVOCATES`` ⊂ ``ADVOCATES_AND_REBUTTALS`` ⊂ ``FULL_AI``. There is no mode that
  replays the rebuttals without the advocates, and none that replays the Judge
  without both. A stage whose predecessor must run live therefore cannot be
  replayed *by construction*. The chain is not a policy applied here; it is the
  shape of the thing being used.

* **Case, hash, version and dispute-type freshness.**
  ``ReplayValidationService`` compares every one of them against the current case
  state, and it runs inside the ``ReplayService`` call this module makes.

What this module adds is the one check the replay layer leaves out on purpose:
**provenance**. ``ReplayValidationService`` asks "does this still describe this
case?"; it does not ask "did this come from the model under test?". A mock
capture is a perfectly valid artefact and must never be presented as live
evidence -- that is not a hypothetical, it is the exact shape of the incident
that destroyed the DISP-005 captures on 2026-10-05. The distinction is a property
of a *validation session*, not of the product, so it lives here and not in
``app/replay``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.judge_context_builder import JudgeContextBuilder
from app.models.agent import AgentCaseContext
from app.models.analysis import CaseAnalysisResponse
from app.models.case import DisputeCase
from app.models.replay import ARTIFACT_NOT_FOUND, NO_REPLAY, ReplayMode
from app.replay.artifacts import ReplayArtifactBase
from app.replay.service import ReplayRefused, ReplayService
from app.replay.store import ReplayArtifactStore

# The order checkpoints are attempted in. It is a chain rather than a set
# because each stage consumes the previous stage's output: the rebuttals
# cross-examine the advocates' claims, and the Judge reads both.
CHAIN: tuple[str, ...] = ("ADVOCATES", "REBUTTALS", "JUDGE")

# The longest reusable prefix of ``CHAIN`` maps onto exactly one replay mode.
# ``MODE_FOR_PREFIX[n]`` is the mode that serves the first ``n`` stages from
# storage. That these are prefixes is the dependency rule, expressed as data.
#
# ``NO_REPLAY`` is imported rather than written as a string, and that is not
# tidiness: the constant's *value* is ``"NONE"``, so the obvious literal
# ``"NO_REPLAY"`` is a string the orchestrator rejects at runtime with a
# pydantic ``literal_error``. Typing the mapping as ``ReplayMode`` makes the
# mistake unrepresentable instead of merely unlikely.
MODE_FOR_PREFIX: dict[int, ReplayMode] = {
    0: NO_REPLAY,
    1: "ADVOCATES",
    2: "ADVOCATES_AND_REBUTTALS",
    3: "FULL_AI",
}

# Verdicts.
REUSED = "REUSED"
"""Validated against the current case state, and captured by the model under test."""
ABSENT = "ABSENT"
"""No artefact on disk. Nothing to reuse."""
REFUSED = "REFUSED"
"""An artefact exists but the current code will not accept it. Re-capture."""
NOT_ELIGIBLE = "NOT_ELIGIBLE"
"""An artefact exists and may even validate, but was not captured by the model
under test -- a mock or a different provider. Never live evidence."""
SKIPPED = "SKIPPED"
"""Not attempted: an earlier stage in the chain must run live."""


@dataclass(frozen=True)
class Provenance:
    """The model under test, as this session declares it."""

    mode: str
    provider: str
    model: str


@dataclass
class Verdict:
    """What was decided about one checkpoint, and why."""

    stage: str
    status: str
    reason: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    captured_at: str | None = None
    captured_mode: str | None = None
    captured_provider: str | None = None
    captured_model: str | None = None
    deterministic_analysis_hash: str | None = None
    schema_version: str | None = None

    @property
    def reusable(self) -> bool:
        return self.status == REUSED

    def line(self) -> str:
        """One line of the run's own commentary."""
        if self.status == REUSED:
            return (
                f"{self.stage:<9} CHECKPOINT REUSED"
                f"   ({self.captured_model or '?'} @ {self.captured_at or '?'})"
            )
        if self.status == ABSENT:
            return f"{self.stage:<9} no checkpoint — will run live"
        if self.status == REFUSED:
            codes = ", ".join(self.reason_codes) or "REPLAY_INVALID"
            return f"{self.stage:<9} checkpoint REFUSED ({codes}) — will run live"
        if self.status == NOT_ELIGIBLE:
            return f"{self.stage:<9} checkpoint NOT ELIGIBLE ({self.reason}) — will run live"
        return f"{self.stage:<9} not attempted ({self.reason}) — will run live"

    def report(self) -> dict:
        """The checkpoint record. Every field is read off the artefact, never stored twice."""
        return {
            "stage": self.stage,
            "status": self.status,
            "reason": self.reason,
            "reasonCodes": self.reason_codes,
            "capturedAt": self.captured_at,
            "capturedMode": self.captured_mode,
            "capturedProvider": self.captured_provider,
            "capturedModel": self.captured_model,
            "deterministicAnalysisHash": self.deterministic_analysis_hash,
            "schemaVersion": self.schema_version,
            "reusable": self.reusable,
        }


@dataclass
class ResumePlan:
    """Which stages may be reused, and which must run live."""

    mode: ReplayMode
    verdicts: list[Verdict]

    @property
    def reused(self) -> list[str]:
        return [verdict.stage for verdict in self.verdicts if verdict.reusable]

    @property
    def live(self) -> list[str]:
        return [stage for stage in CHAIN if stage not in self.reused]

    def lines(self) -> list[str]:
        return [verdict.line() for verdict in self.verdicts]

    def report(self) -> dict:
        return {
            "replayMode": self.mode,
            "reused": self.reused,
            "live": self.live,
            "checkpoints": [verdict.report() for verdict in self.verdicts],
        }


def provenance_problem(artifact: ReplayArtifactBase, expected: Provenance) -> str | None:
    """Why this artefact is not the model under test, or ``None`` if it is.

    Ordered cheapest-and-most-likely first, and each branch names both the value
    found and the value expected: a developer reading "NOT ELIGIBLE" needs to see
    *which* field disagreed, not merely that one did.

    ``is_live_eligible`` is consulted last and is not redundant. For a rebuttal
    artefact it additionally requires that both sides actually produced output --
    provenance alone would accept a capture where one side failed, which is not a
    checkpoint of a stage that succeeded.
    """
    if artifact.captured_mode != expected.mode:
        return (
            f"capturedMode={artifact.captured_mode!r}, expected {expected.mode!r}"
        )
    if artifact.captured_provider != expected.provider:
        return (
            f"capturedProvider={artifact.captured_provider!r}, "
            f"expected {expected.provider!r}"
        )
    if artifact.captured_model != expected.model:
        return (
            f"capturedModel={artifact.captured_model!r}, expected {expected.model!r}"
        )
    if not artifact.is_live_eligible:
        return "the artefact does not describe a stage that completed on every side"
    return None


def _identity(artifact: ReplayArtifactBase) -> dict:
    """The artefact's own description of itself, for the report."""
    return {
        "captured_at": artifact.created_at,
        "captured_mode": artifact.captured_mode,
        "captured_provider": artifact.captured_provider or None,
        "captured_model": artifact.captured_model,
        "deterministic_analysis_hash": artifact.deterministic_analysis_hash,
        "schema_version": artifact.schema_version,
    }


def _unreadable(stage: str, load_reasons: list[str]) -> Verdict:
    """Distinguish "nothing there" from "there, and unusable".

    Both arrive as ``(None, [reason])`` from the store, and collapsing them would
    lose the only thing a developer needs to know: whether to re-run the stage
    (nothing to lose) or to look at the file first (something is there and it is
    wrong).
    """
    codes = sorted(set(load_reasons))
    if codes == [ARTIFACT_NOT_FOUND]:
        return Verdict(stage=stage, status=ABSENT, reason_codes=codes)
    return Verdict(
        stage=stage,
        status=REFUSED,
        reason="the artefact is present but could not be read",
        reason_codes=codes,
    )


def _gate(
    stage: str,
    artifact: ReplayArtifactBase | None,
    load_reasons: list[str],
    expected: Provenance,
) -> Verdict | None:
    """Decide without asking the replay layer, when the answer is already known.

    Returns a ``Verdict`` when the checkpoint is unusable for a reason visible
    from the file alone, and ``None`` when the replay layer still has to rule.
    """
    if artifact is None:
        return _unreadable(stage, load_reasons)
    problem = provenance_problem(artifact, expected)
    if problem is not None:
        return Verdict(
            stage=stage, status=NOT_ELIGIBLE, reason=problem, **_identity(artifact)
        )
    return None


def _refused(stage: str, artifact: ReplayArtifactBase, refusal: ReplayRefused) -> Verdict:
    return Verdict(
        stage=stage,
        status=REFUSED,
        reason="the current code will not accept this artefact",
        reason_codes=list(refusal.reason_codes),
        **_identity(artifact),
    )


def plan_resume(
    *,
    case: DisputeCase,
    analysis: CaseAnalysisResponse,
    advocate_context: AgentCaseContext,
    store: ReplayArtifactStore,
    replays: ReplayService,
    expected: Provenance,
) -> ResumePlan:
    """Decide how far the stored checkpoints reach, and where live work resumes.

    The walk is strictly sequential and strictly a prefix: as soon as one stage
    cannot be reused, every later stage is reported ``SKIPPED`` without being
    examined. That is the dependency rule, and it is why this function cannot
    produce a plan the orchestrator could not execute -- it returns a mode, and
    the modes are prefixes.

    Asking ``ReplayService`` is a read, not a dry run with a side effect: loading
    and re-verifying an artefact performs no network call and no write. No audit
    trail is passed, so nothing is recorded anywhere -- which is why the caller is
    free to ask this question as often as it likes.
    """
    verdicts: list[Verdict] = []
    advocates = None
    rebuttals = None

    # -- ADVOCATES --------------------------------------------------------
    artifact, load_reasons = store.read(case.id, "ADVOCATES")
    verdict = _gate("ADVOCATES", artifact, load_reasons, expected)
    if verdict is None:
        assert artifact is not None
        try:
            advocates, _ = replays.replay_advocates(case, analysis, advocate_context)
            verdict = Verdict(stage="ADVOCATES", status=REUSED, **_identity(artifact))
        except ReplayRefused as refusal:
            verdict = _refused("ADVOCATES", artifact, refusal)
    verdicts.append(verdict)

    # -- REBUTTALS --------------------------------------------------------
    # ``replay_rebuttals`` needs the advocates' own output, so a live advocates
    # stage makes this stage unreachable regardless of what is on disk.
    if not verdict.reusable:
        verdicts.append(
            Verdict("REBUTTALS", SKIPPED, "the advocates must run live")
        )
    else:
        artifact, load_reasons = store.read(case.id, "REBUTTALS")
        verdict = _gate("REBUTTALS", artifact, load_reasons, expected)
        if verdict is None:
            assert artifact is not None
            try:
                rebuttals, _ = replays.replay_rebuttals(
                    case, analysis, advocate_context, advocates
                )
                verdict = Verdict(stage="REBUTTALS", status=REUSED, **_identity(artifact))
            except ReplayRefused as refusal:
                verdict = _refused("REBUTTALS", artifact, refusal)
        verdicts.append(verdict)

    # -- JUDGE ------------------------------------------------------------
    # The Judge artefact is validated against a context built from *this*
    # pipeline's advocates and rebuttals, so it is only reachable when both
    # precede it in the reuse chain.
    if not verdict.reusable:
        verdicts.append(Verdict("JUDGE", SKIPPED, "the rebuttals must run live"))
    else:
        artifact, load_reasons = store.read(case.id, "JUDGE")
        verdict = _gate("JUDGE", artifact, load_reasons, expected)
        if verdict is None:
            assert artifact is not None
            judge_context = JudgeContextBuilder().build(
                case, analysis, advocate_context, advocates.rider, advocates.driver, rebuttals
            )
            try:
                replays.replay_judge(judge_context)
                verdict = Verdict(stage="JUDGE", status=REUSED, **_identity(artifact))
            except ReplayRefused as refusal:
                verdict = _refused("JUDGE", artifact, refusal)
        verdicts.append(verdict)

    reused = [item for item in verdicts if item.reusable]
    # Guarded rather than assumed: a non-prefix result would mean this function
    # had invented a plan the orchestrator cannot run.
    assert [item.stage for item in reused] == list(CHAIN[: len(reused)]), (
        "reuse must be a prefix of the chain; a mixed plan is not representable "
        "as a ReplayMode"
    )
    return ResumePlan(mode=MODE_FOR_PREFIX[len(reused)], verdicts=verdicts)


__all__ = [
    "ABSENT",
    "CHAIN",
    "MODE_FOR_PREFIX",
    "NOT_ELIGIBLE",
    "Provenance",
    "REFUSED",
    "REUSED",
    "ResumePlan",
    "SKIPPED",
    "Verdict",
    "plan_resume",
    "provenance_problem",
]
