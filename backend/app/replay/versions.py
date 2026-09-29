"""Replay format versions.

Compatibility is decided by an explicit string, never inferred from a Python
class name or a module path. Renaming a class is a refactor; it must not
silently invalidate every stored artefact, and it must not silently *validate*
one either. The version is the contract.

The naming follows the existing prompt-version convention
(``advocate_prompts_v1``, ``judge_prompts_v1``, ``rebuttal_prompts_v1``): a short
name plus ``-vN``.
"""

from __future__ import annotations

REPLAY_FORMAT_VERSION = "1"
"""Version of the envelope and storage layout."""

ADVOCATE_SCHEMA_VERSION = "stage4-v1"
"""Version of the stored advocate payload (Stage 4's ``AdvocateOutput``)."""

REBUTTAL_SCHEMA_VERSION = "stage6-v1"
"""Version of the stored rebuttal payload (Stage 6's ``RebuttalOutput``)."""

JUDGE_SCHEMA_VERSION = "stage5-v1"
"""Version of the stored Judge payload (Stage 5's ``JudgeOutput``)."""

SUPPORTED_REPLAY_VERSIONS: frozenset[str] = frozenset({REPLAY_FORMAT_VERSION})

# Stage name -> the schema version its payload must declare.
STAGE_SCHEMA_VERSIONS: dict[str, str] = {
    "ADVOCATES": ADVOCATE_SCHEMA_VERSION,
    "REBUTTALS": REBUTTAL_SCHEMA_VERSION,
    "JUDGE": JUDGE_SCHEMA_VERSION,
}

__all__ = [
    "ADVOCATE_SCHEMA_VERSION",
    "JUDGE_SCHEMA_VERSION",
    "REBUTTAL_SCHEMA_VERSION",
    "REPLAY_FORMAT_VERSION",
    "STAGE_SCHEMA_VERSIONS",
    "SUPPORTED_REPLAY_VERSIONS",
]
