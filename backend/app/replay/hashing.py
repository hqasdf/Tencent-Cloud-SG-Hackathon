"""Canonical hashing for replay validity.

A replay is only usable while the trusted input it was captured against is
unchanged. This module answers "unchanged" with a SHA-256 over a canonical JSON
projection, and the single most important design decision is **what goes into
that projection**.

The hash input is an allow-list, built the same way the agent contexts are.
Including a whole ``CaseAnalysisResponse`` would have been easier, but it carries
``resolutionRecommendation`` and ``confidence`` — the *answers* — and hashing
them would mean a refund recalculating invalidates a replay that is still
perfectly valid. The deterministic answer is recomputed on every run anyway, so
it has no business being part of a freshness check.

What is included (the trusted input, and nothing else):

    case id, dispute type, currency
    the deterministic facts
    the evidence the agents saw, with its content and status
    the applicable policy and its rule set
    PolicyTwin's rule-by-rule evaluation, including required values
    the resolution mode
    the allowed outcome vocabulary
    the schema version

What is excluded, and must stay excluded:

    latency, token counts, provider and model names, timestamps,
    audit metadata, run durations, and every computed answer

Excluding the last group is what makes the hash *stable*: two runs of the same
unchanged case produce the same hash even though their timings and token counts
differ. That property is tested directly, because a hash that drifted with
latency would make replay useless while still appearing to work.

``trusted_context_hash`` is a second, narrower check over the exact
``AgentCaseContext`` the agents saw. The two are not redundant: the analysis hash
covers the deterministic state, while the context hash covers the projection
actually placed in front of the model. A change to the context builder — a new
field, a dropped one — moves the context hash without necessarily moving the
analysis hash, and that is precisely the kind of change that should invalidate a
stored artefact.
"""

from __future__ import annotations

import hashlib
import json

from app.models.analysis import CaseAnalysisResponse
from app.models.agent import AgentCaseContext
from app.models.case import DisputeCase
from app.models.judge import JUDGE_ALLOWED_OUTCOMES
from app.policies import NO_SHOW_POLICY_V1, ROUTE_DEVIATION_POLICY_V1
from app.replay.versions import (
    ADVOCATE_SCHEMA_VERSION,
    JUDGE_SCHEMA_VERSION,
    REBUTTAL_SCHEMA_VERSION,
)

# The schema version a payload is hashed under. The three stages share one
# deterministic analysis, so the analysis hash carries all three: an artefact
# captured under any of them must be re-captured if any payload schema moves.
_SCHEMA_TAG = f"{ADVOCATE_SCHEMA_VERSION}+{REBUTTAL_SCHEMA_VERSION}+{JUDGE_SCHEMA_VERSION}"


def canonical_json(payload: object) -> str:
    """Serialize deterministically: sorted keys, no whitespace, ASCII only.

    ``sort_keys`` removes dict-ordering as a source of drift, and the compact
    separators remove whitespace. ``ensure_ascii`` keeps the byte sequence
    independent of the writer's locale, so the same logical payload hashes the
    same on any machine.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_payload(payload: object) -> str:
    """SHA-256 of the canonical form, hex encoded."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def deterministic_analysis_hash(
    case: DisputeCase, analysis: CaseAnalysisResponse
) -> str:
    """Hash the trusted deterministic input for one case.

    Changing any of the inputs listed in the module docstring changes this
    value; changing latency, tokens or timestamps does not.
    """
    return hash_payload(_analysis_projection(case, analysis))


def trusted_context_hash(context: AgentCaseContext) -> str:
    """Hash the exact trusted context handed to the agents.

    ``by_alias=True`` because the camelCase form is what was actually
    serialized into the model's request; hashing the snake_case form would
    describe something that was never sent.
    """
    return hash_payload(context.model_dump(by_alias=True, mode="json"))


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _analysis_projection(case: DisputeCase, analysis: CaseAnalysisResponse) -> dict:
    policy = (
        ROUTE_DEVIATION_POLICY_V1
        if case.dispute_type == "route_deviation"
        else NO_SHOW_POLICY_V1
    )
    return {
        "schemaVersion": _SCHEMA_TAG,
        "caseId": case.id,
        "disputeType": case.dispute_type,
        "currency": case.fare.currency,
        # All deterministic facts, including required/missing/supporting
        # evidence IDs and any verified conditions.
        "facts": analysis.analysis.model_dump(by_alias=True, mode="json"),
        # The evidence as the agents saw it. Sorted by id so list order in the
        # case record cannot change the hash.
        "evidence": sorted(
            (
                {
                    "id": item.id,
                    "type": item.type,
                    "timestamp": item.timestamp,
                    "source": item.source,
                    "summary": item.summary,
                    "status": item.status,
                }
                for item in case.evidence
            ),
            key=lambda item: item["id"],
        ),
        "policy": {
            "policyId": policy.policy_id,
            "policyVersion": policy.version,
            "disputeType": case.dispute_type,
        },
        # PolicyTwin's evaluation carries required_value, which is where a
        # threshold change shows up.
        "policyEvaluation": analysis.policy_evaluation.model_dump(
            by_alias=True, mode="json"
        ),
        "resolutionMode": analysis.resolution_mode,
        "allowedOutcomes": sorted(JUDGE_ALLOWED_OUTCOMES[case.dispute_type]),
    }


__all__ = [
    "canonical_json",
    "deterministic_analysis_hash",
    "hash_payload",
    "trusted_context_hash",
]
