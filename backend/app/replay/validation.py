"""Deterministic validity checks for a stored replay artefact.

Validity is decided entirely by comparison against the *current* case state. No
field in an artefact asserts its own validity, so there is nothing to trust and
nothing to forge.

The checks are ordered cheapest-and-most-fundamental first, but **all** of them
run rather than stopping at the first failure. That is a deliberate departure
from the "first issue wins" style used elsewhere in the pipeline, because the
audiences differ: a claim rejection is shown to one human reviewing one case,
whereas a replay failure is read by a developer who needs to know whether the
artefact is stale, mis-versioned, or for the wrong case — often all at once.

A refused replay reports ``REPLAY_INVALID`` with reason codes. It never falls
back to a live call. Falling back would spend exactly the quota this feature
exists to conserve, and it would make the run's provenance unknowable after the
fact: a reviewer could no longer tell whether the Judge's argument came from the
stored artefact or from a model that was silently called instead.
"""

from __future__ import annotations

from app.models.analysis import CaseAnalysisResponse
from app.models.agent import AgentCaseContext
from app.models.case import DisputeCase
from app.models.replay import (
    ANALYSIS_HASH_MISMATCH,
    CASE_ID_MISMATCH,
    DISPUTE_TYPE_MISMATCH,
    REPLAY_VERSION_UNSUPPORTED,
    SCHEMA_VERSION_MISMATCH,
    TRUSTED_CONTEXT_HASH_MISMATCH,
    ReplayValidationResult,
)
from app.replay.artifacts import ReplayArtifactBase
from app.replay.hashing import deterministic_analysis_hash, trusted_context_hash
from app.replay.versions import STAGE_SCHEMA_VERSIONS, SUPPORTED_REPLAY_VERSIONS


class ReplayValidationService:
    """Checks an artefact's envelope against the current case state. Pure."""

    def validate(
        self,
        artifact: ReplayArtifactBase,
        *,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
    ) -> ReplayValidationResult:
        reasons: list[str] = []

        if artifact.replay_version not in SUPPORTED_REPLAY_VERSIONS:
            reasons.append(REPLAY_VERSION_UNSUPPORTED)

        expected_schema = STAGE_SCHEMA_VERSIONS.get(artifact.stage)
        if expected_schema is not None and artifact.schema_version != expected_schema:
            reasons.append(SCHEMA_VERSION_MISMATCH)

        if artifact.case_id != case.id:
            reasons.append(CASE_ID_MISMATCH)

        if artifact.dispute_type != case.dispute_type:
            reasons.append(DISPUTE_TYPE_MISMATCH)

        # Freshness. These two are the checks that actually decide whether the
        # stored AI output still describes this case.
        if artifact.deterministic_analysis_hash != deterministic_analysis_hash(
            case, analysis
        ):
            reasons.append(ANALYSIS_HASH_MISMATCH)

        if artifact.trusted_context_hash != trusted_context_hash(context):
            reasons.append(TRUSTED_CONTEXT_HASH_MISMATCH)

        return ReplayValidationResult(valid=not reasons, reason_codes=sorted(set(reasons)))


__all__ = ["ReplayValidationService"]
