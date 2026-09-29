"""Deterministic call-count planning.

Answers "how many provider calls will this cost?" before anything runs, with no
network access and no model involved. The count is derived from the mode by
arithmetic over the pipeline's fixed shape, so it cannot disagree with what the
orchestrator actually does — and if it ever did, the test that pins both would
fail.

This exists because the quota is the scarce resource. A developer choosing a
replay mode should be able to see the price first, and a plan that had to ask a
model how many models it would need would be its own joke.
"""

from __future__ import annotations

from app.models.replay import NO_REPLAY, ReplayMode, ReplayModel

# The fixed shape of the Stage 6 pipeline. Five calls, and the pipeline has no
# branches that change that: both advocates always run, one rebuttal round
# always runs, and the Judge always runs unless an advocate failed.
LIVE_CALLS_PER_STAGE: dict[str, int] = {
    "ADVOCATES": 2,
    "REBUTTALS": 2,
    "JUDGE": 1,
}

TOTAL_LIVE_CALLS = sum(LIVE_CALLS_PER_STAGE.values())


class ReplayPlan(ReplayModel):
    """What a run will cost, and where each call goes."""

    mode: ReplayMode = NO_REPLAY
    advocates_replayed: bool = False
    rebuttals_replayed: bool = False
    judge_replayed: bool = False
    live_stages: list[str] = []
    replayed_stages: list[str] = []
    expected_provider_calls: int = TOTAL_LIVE_CALLS
    note: str = ""


def replayed_stages_for(mode: ReplayMode) -> set[str]:
    """Which stages a mode serves from artefacts."""
    if mode == "ADVOCATES":
        return {"ADVOCATES"}
    if mode == "ADVOCATES_AND_REBUTTALS":
        return {"ADVOCATES", "REBUTTALS"}
    if mode == "FULL_AI":
        return {"ADVOCATES", "REBUTTALS", "JUDGE"}
    return set()


def build_plan(mode: ReplayMode) -> ReplayPlan:
    """The plan for one mode. Pure, offline, and exhaustive over ``ReplayMode``."""
    replayed = replayed_stages_for(mode)
    live = [stage for stage in LIVE_CALLS_PER_STAGE if stage not in replayed]
    calls = sum(LIVE_CALLS_PER_STAGE[stage] for stage in live)

    return ReplayPlan(
        mode=mode,
        advocates_replayed="ADVOCATES" in replayed,
        rebuttals_replayed="REBUTTALS" in replayed,
        judge_replayed="JUDGE" in replayed,
        live_stages=live,
        replayed_stages=[stage for stage in LIVE_CALLS_PER_STAGE if stage in replayed],
        expected_provider_calls=calls,
        note=_note(mode, calls),
    )


def expected_provider_calls(mode: ReplayMode) -> int:
    """Convenience for the common case."""
    return build_plan(mode).expected_provider_calls


def _note(mode: ReplayMode, calls: int) -> str:
    if mode == NO_REPLAY:
        return (
            f"All {calls} AI stages run against the configured provider. "
            "Deterministic analysis and execution are computed live."
        )
    if mode == "FULL_AI":
        return (
            "No provider calls. Every AI stage is served from stored artefacts, "
            "which are revalidated against the current case state. Deterministic "
            "analysis, resolution, confidence and escalation are still recomputed."
        )
    return (
        f"{calls} provider call(s) remain live; the rest are revalidated from "
        "stored artefacts. Deterministic analysis and execution are computed live."
    )


__all__ = [
    "LIVE_CALLS_PER_STAGE",
    "TOTAL_LIVE_CALLS",
    "ReplayPlan",
    "build_plan",
    "expected_provider_calls",
    "replayed_stages_for",
]
