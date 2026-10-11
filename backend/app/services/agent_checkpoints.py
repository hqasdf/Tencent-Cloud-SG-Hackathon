"""Per-role recovery, separate from developer-controlled historical replay.

Only schema-parsed, fully verified outputs are persisted. Every load is signed,
context-bound, parsed and verified again. Each successful role is durably written
before the next role starts; an OS-backed SQLite lock prevents concurrent calls
for the same case across threads/processes and is released on process exit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

from app.agents.config import AgentSettings
from app.agents.provider import LlmCompletion
from app.models.advocate import AdvocateOutput
from app.models.judge import JudgeOutput
from app.models.rebuttal import RebuttalOutput
from app.replay.hashing import canonical_json, hash_payload
from app.replay.store import forbidden_keys_in, forbidden_values_in

ROLES = frozenset({"RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL", "JUDGE"})
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "agent_checkpoints"


class CheckpointBusy(RuntimeError):
    """Another request already owns this case; do not buy duplicate calls."""


class CheckpointStorageError(RuntimeError):
    """Recovery storage unavailable before execution starts."""


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AgentCheckpointStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("AGENT_CHECKPOINT_DIR") or DEFAULT_ROOT)

    @contextmanager
    def request_lock(self, case_id: str):
        directory = self.root / hashlib.sha256(case_id.encode()).hexdigest()
        lock = None
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock = sqlite3.connect(directory / "request-lock.sqlite3", timeout=0.1)
            try:
                lock.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                raise CheckpointBusy("Analysis for this case is already running.") from error
        except (OSError, sqlite3.Error) as error:
            if lock is not None:
                lock.close()
            raise CheckpointStorageError("Checkpoint storage is unavailable.") from error
        except Exception:
            if lock is not None:
                lock.close()
            raise
        try:
            yield directory
        finally:
            lock.rollback()
            lock.close()

    @contextmanager
    def session(self, case_id: str, settings: AgentSettings, provider: str, analysis_hash: str):
        with self.request_lock(case_id) as directory:
            try:
                key_path = directory / "integrity.key"
                if not key_path.exists():
                    _atomic_write(key_path, secrets.token_bytes(32))
                key = key_path.read_bytes()
                if len(key) != 32:
                    raise CheckpointStorageError("Checkpoint integrity key is invalid.")
            except OSError:
                raise CheckpointStorageError("Checkpoint storage is unavailable.") from None
            yield CheckpointSession(directory, key, settings, provider, analysis_hash)


class CheckpointSession:
    def __init__(self, directory, key, settings, provider, analysis_hash):
        self.directory, self.key = directory, key
        self.settings, self.provider = settings, provider
        self.events: dict[str, str] = {}
        prompts = Path(__file__).resolve().parents[1] / "agents" / "prompts"
        self.identity = {
            "version": 2, "analysis": analysis_hash,
            "mode": settings.mode, "provider": provider, "configuredProvider": settings.provider,
            "model": settings.model, "endpointHash": hash_payload(settings.base_url),
            "temperature": settings.temperature, "maxTokens": settings.max_tokens,
            "reasoningEffort": settings.reasoning_effort, "promptVersion": settings.prompt_version,
            "prompts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(prompts.glob("*.md"))},
            "schemas": [model.model_json_schema() for model in (AdvocateOutput, RebuttalOutput, JudgeOutput)],
        }

    def run(self, role, context, invoke, parse, validate):
        assert role in ROLES
        identity = hash_payload({"configuration": self.identity, "role": role,
                                 "context": context.model_dump(by_alias=True, mode="json")})
        path = self.directory / (role.lower() + ".json")
        eligible = self.provider == self.settings.provider and (
            self.settings.mode == "mock" or (self.provider != "mock" and bool(self.settings.model))
        )
        if not eligible:
            self.events[role] = "INELIGIBLE_PROVENANCE"
        if eligible and path.exists():
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                document = envelope["document"]
                expected = hmac.new(self.key, canonical_json(document).encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, envelope["signature"]):
                    raise ValueError("integrity")
                if document["identity"] != identity or document["role"] != role:
                    raise ValueError("context")
                completion = LlmCompletion(raw_text=canonical_json(document["output"]), **document["execution"])
                if completion.provider_name != self.provider or completion.truncated:
                    raise ValueError("provenance")
                if self.settings.mode == "real" and not completion.model_name:
                    raise ValueError("model")
                output = parse(completion.raw_text, completion)
                if not validate(output):
                    raise ValueError("verification")
                self.events[role] = "REUSED"
                return SimpleNamespace(output=output, completion=replace(
                    completion, reused=True, duration_ms=0, input_tokens=0,
                    output_tokens=0, total_tokens=0, attempt_count=0,
                ))
            except Exception:  # untrusted/corrupt/stale storage is never a valid result
                self.events[role] = "REJECTED_CHECKPOINT"

        argument = invoke()
        completion = argument.completion
        # Never store partially rejected output: the next retry must obtain a
        # fresh argument instead of recycling any rejected material.
        verified = validate(argument.output)
        if eligible and completion.provider_name == self.provider and not completion.truncated and verified:
            if self.settings.mode == "real" and not completion.model_name:
                return argument
            execution = asdict(completion)
            execution.pop("raw_text")
            document = {"role": role, "identity": identity,
                        "output": argument.output.model_dump(by_alias=True, mode="json"),
                        "execution": execution}
            if forbidden_keys_in(document) or forbidden_values_in(document):
                self.events[role] = "CHECKPOINT_UNSAFE"
                return argument
            signature = hmac.new(self.key, canonical_json(document).encode(), hashlib.sha256).hexdigest()
            try:
                _atomic_write(path, canonical_json({"document": document, "signature": signature}).encode())
                self.events[role] = "SAVED"
            except OSError:
                self.events[role] = "CHECKPOINT_WRITE_FAILED"
        elif eligible and not verified:
            self.events[role] = "REJECTED_OUTPUT"
        elif eligible and completion.provider_name != self.provider:
            self.events[role] = "PROVIDER_MISMATCH"
        return argument


def run_checked(session, role, context, invoke, parse, validate):
    return invoke() if session is None else session.run(role, context, invoke, parse, validate)
