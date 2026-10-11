"""Test-session environment hygiene.

The backend loads ``backend/.env`` at startup (see ``app.env``), and
``app.main`` is imported by the API tests. Without this guard, a developer whose
local ``.env`` points at a real provider would have the offline suite silently
make live model calls — slow, non-deterministic, and potentially billable.

Two protections:

1. ``AGENT_MODE`` and ``AGENT_PROVIDER`` are forced to ``mock`` for the whole
   session. They must be *set*, not merely left unset: ``load_dotenv`` uses
   ``override=False``, so an explicit environment variable cannot be replaced by
   the file. Setting only the mode would still let the file supply
   ``AGENT_PROVIDER=openai_compatible``, which is rejected in mock mode.
2. Provider credentials for the intake pipeline are removed, so a developer with
   a real key exported cannot have intake tests reach a paid API.

Every value is set to the empty string rather than deleted. ``load_dotenv``
skips a key that is already *present* in the environment, regardless of whether
its value is empty — so an empty marker is what actually blocks the file.
Deleting the key instead would let ``load_dotenv`` refill it when ``app.main``
is imported by the API tests.

This runs at conftest import time, which pytest guarantees happens before test
modules are collected and therefore before ``app.main`` is imported.

Tests that need a specific configuration use ``monkeypatch.setenv``, which
takes precedence over everything here.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Keep the ordinary full-suite command isolated from developer intake, replay
# artifacts and signed checkpoints. Individual tests still inject their own stores.
_scratch = Path(__file__).resolve().parents[1] / ".pytest-scratch"
_scratch.mkdir(exist_ok=True)
_session_storage = tempfile.TemporaryDirectory(prefix="offline-session-", dir=_scratch)
_storage = Path(_session_storage.name)
from app.repositories import intake_repository
intake_repository.DB_PATH = _storage / "intake.db"
os.environ["AGENT_CHECKPOINT_DIR"] = str(_storage / "checkpoints")
os.environ["REPLAY_ARTIFACT_DIR"] = str(_storage / "replay")
os.environ["REPLAY_API_ENABLED"] = "0"
os.environ["AGENT_MAX_TOKENS"] = "1024"
os.environ["APP_ENV"] = "test"
os.environ["CORS_ALLOWED_ORIGINS"] = ""
os.environ["INTAKE_DB_PATH"] = ""

# Force the advocates onto the deterministic, offline provider.
os.environ["AGENT_MODE"] = "mock"
os.environ["AGENT_PROVIDER"] = "mock"

# Blank markers: present-but-empty blocks load_dotenv from supplying a value.
# AGENT_REASONING_EFFORT is included because it now shapes the outbound request
# body, so a developer with it exported in their shell would otherwise get a
# different payload from the one the tests describe.
for _name in ("AGENT_MODEL", "AGENT_BASE_URL", "AGENT_API_KEY", "AGENT_REASONING_EFFORT"):
    os.environ[_name] = ""

# Never let an exported credential turn an offline test into a live API call.
for _name in (
    "TENCENT_TOKENHUB_API_KEY",
    "TENCENT_TOKENHUB_BASE_URL",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
):
    os.environ[_name] = ""
