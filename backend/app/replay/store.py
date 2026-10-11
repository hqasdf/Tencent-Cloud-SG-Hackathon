"""On-disk storage for replay artefacts.

Layout::

    backend/replay_artifacts/
        DISP-005/
            advocates.json
            rebuttals.json
            judge.json

A second, read-only root (``backend/demo_replays/``) is searched as a fallback.
It exists so a curated synthetic artefact could later be committed deliberately
for demo resilience without any tooling writing there. Nothing in this codebase
writes to it, and nothing commits automatically.

Raw artefacts are gitignored. They are a local development convenience derived
from a live run, not a source of truth, and a committed one would rot silently
as soon as the deterministic fixtures moved.

Writing is guarded. The artefact is serialized once, then scanned for key names
that must never be persisted — credentials, authorization headers, prompts and
model reasoning. The scan is over the *serialized* document rather than over the
Python objects, because the serialized document is what actually reaches disk:
checking the object graph would leave the encoder free to add a field the check
never saw.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.models.replay import ARTIFACT_NOT_FOUND, MALFORMED_ARTIFACT
from app.replay.artifacts import (
    FILENAME_BY_STAGE,
    ReplayArtifactBase,
    artifact_class_for_stage,
)

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "replay_artifacts"
_CURATED_ROOT = Path(__file__).resolve().parents[2] / "demo_replays"

# Key names that must never be written. Compared case-insensitively after
# stripping separators, so `apiKey`, `api_key` and `API-KEY` are one entry.
_FORBIDDEN_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "headers",
        "prompt",
        "systemprompt",
        "userprompt",
        "rawresponse",
        "chainofthought",
        "reasoning",
        "secret",
        "token",
        "credential",
        "password",
    }
)

# Keys whose *value* would be a credential even under an innocent name.
_FORBIDDEN_VALUE_PREFIXES = ("Bearer ", "AIza", "sk-")
_CREDENTIAL_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    re.compile(r"AKID[A-Za-z0-9]{32}"),
    re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)


class ReplayArtifactError(ValueError):
    """Raised when an artefact cannot be written safely."""


def _normalise_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def forbidden_keys_in(payload: object) -> list[str]:
    """Collect forbidden key names anywhere in a JSON-shaped structure."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and _normalise_key(key) in _FORBIDDEN_KEYS:
                    found.append(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return sorted(set(found))


def forbidden_values_in(payload: object) -> list[str]:
    """Collect values that look like credentials regardless of their key."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            if any(pattern.search(node) for pattern in _CREDENTIAL_PATTERNS):
                found.append("embedded credential")
                return
            for prefix in _FORBIDDEN_VALUE_PREFIXES:
                if node.startswith(prefix):
                    found.append(prefix.strip())
                    return

    walk(payload)
    return sorted(set(found))


class ReplayArtifactStore:
    """Reads and writes artefacts under one writable root plus a curated fallback."""

    def __init__(
        self,
        root: Path | str | None = None,
        curated_root: Path | str | None = None,
    ) -> None:
        configured = os.environ.get("REPLAY_ARTIFACT_DIR")
        self._root = Path(root or configured or _DEFAULT_ROOT)
        self._curated = Path(curated_root or _CURATED_ROOT)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def curated_root(self) -> Path:
        return self._curated

    def path_for(self, case_id: str, stage: str) -> Path:
        return self._safe_path(self._root, case_id, stage)

    @staticmethod
    def _safe_path(root: Path, case_id: str, stage: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", case_id) or stage not in FILENAME_BY_STAGE:
            raise ReplayArtifactError("Invalid replay case or stage")
        path = root / case_id / FILENAME_BY_STAGE[stage]
        if not path.resolve().is_relative_to(root.resolve()):
            raise ReplayArtifactError("Replay path is outside its configured storage")
        return path

    # -- write ------------------------------------------------------------

    def write(self, artifact: ReplayArtifactBase) -> Path:
        """Persist one artefact, refusing to write anything unsafe."""
        document = artifact.model_dump(by_alias=True, mode="json")

        unsafe_keys = forbidden_keys_in(document)
        if unsafe_keys:
            raise ReplayArtifactError(
                "Refusing to write a replay artefact containing "
                f"{', '.join(unsafe_keys)}. Replay artefacts hold model output only."
            )
        unsafe_values = forbidden_values_in(document)
        if unsafe_values:
            raise ReplayArtifactError(
                "Refusing to write a replay artefact containing what looks like a "
                f"credential ({', '.join(unsafe_values)})."
            )

        path = self.path_for(artifact.case_id, artifact.stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return path

    # -- read -------------------------------------------------------------

    def read(self, case_id: str, stage: str) -> tuple[ReplayArtifactBase | None, list[str]]:
        """Load one artefact.

        Returns ``(artifact, [])`` on success and ``(None, [reason])`` when the
        file is missing or unparseable. A malformed artefact is reported rather
        than raised, so the caller can surface it as ``REPLAY_INVALID`` with a
        code instead of a traceback — the developer asked for a replay, and the
        answer to "may I have one?" should be a reason code either way.
        """
        try:
            candidates = self._candidates(case_id, stage)
        except ReplayArtifactError:
            return None, [MALFORMED_ARTIFACT]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                if candidate.stat().st_size > 2 * 1024 * 1024:
                    return None, [MALFORMED_ARTIFACT]
                document = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None, [MALFORMED_ARTIFACT]
            return self._parse(document, stage)
        return None, [ARTIFACT_NOT_FOUND]

    def exists(self, case_id: str, stage: str) -> bool:
        return any(candidate.is_file() for candidate in self._candidates(case_id, stage))

    def _candidates(self, case_id: str, stage: str) -> list[Path]:
        return [self._safe_path(root, case_id, stage) for root in (self._root, self._curated)]

    @staticmethod
    def _parse(
        document: object, stage: str
    ) -> tuple[ReplayArtifactBase | None, list[str]]:
        if not isinstance(document, dict):
            return None, [MALFORMED_ARTIFACT]
        # The file's own `stage` field must agree with the slot it was found in,
        # otherwise a copied file could satisfy a request it does not describe.
        if document.get("stage") != stage:
            return None, [MALFORMED_ARTIFACT]
        artifact_class = artifact_class_for_stage(stage)
        if artifact_class is None:
            return None, [MALFORMED_ARTIFACT]
        try:
            return artifact_class.model_validate(document), []
        except Exception:  # noqa: BLE001 - any schema failure is one outcome here
            return None, [MALFORMED_ARTIFACT]


__all__ = [
    "ReplayArtifactError",
    "ReplayArtifactStore",
    "forbidden_keys_in",
    "forbidden_values_in",
]
