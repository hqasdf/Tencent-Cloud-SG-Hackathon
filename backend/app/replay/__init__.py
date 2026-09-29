"""Stage 6B replay: safe reuse of stored AI-stage output.

A developer-only facility for reducing repeated provider usage without weakening
any trust guarantee. It substitutes the *source* of an AI stage's output and
nothing else — deterministic analysis, verification, the Judge's validation and
the deterministic execution all run exactly as they always did.

Read ``app.replay.artifacts`` first: the decision to store raw model output and
recompute the trust split on every load is what makes this safe rather than
merely convenient.
"""

from __future__ import annotations

from app.replay.plan import ReplayPlan, build_plan, expected_provider_calls
from app.replay.service import ReplayRefused, ReplayService
from app.replay.store import ReplayArtifactError, ReplayArtifactStore

__all__ = [
    "ReplayArtifactError",
    "ReplayArtifactStore",
    "ReplayPlan",
    "ReplayRefused",
    "ReplayService",
    "build_plan",
    "expected_provider_calls",
]
