"""Local ``.env`` loading.

The backend reads its configuration from the process environment (see
``app.agents.config`` and ``app.llm``). For local development it is far more
convenient to keep those settings in ``backend/.env`` than to export them in
every shell and every IDE launch configuration.

Two deliberate choices:

* **``override=False``** — a real environment variable always wins over the
  file. ``AGENT_MODE=real python -m pytest`` therefore behaves as the caller
  intended, and CI can inject configuration without the file interfering.
* **The path is derived from this module's location**, not from the current
  working directory, so it resolves identically whether uvicorn was started
  from the repository root, from ``backend/``, or from an IDE.

This module is intentionally *not* imported by ``app.agents.config``. Loading a
developer's local ``.env`` during a test run would let a real credential or a
live provider leak into the offline test suite. Tests scrub their own
environment instead (see ``tests/conftest.py``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# backend/app/env.py -> backend/.env
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


@lru_cache(maxsize=1)
def load_local_env() -> bool:
    """Load ``backend/.env`` into the process environment.

    Idempotent: repeated calls are a no-op. Returns ``True`` when the file
    existed and was loaded, ``False`` when there was nothing to do.

    A missing ``python-dotenv`` is tolerated rather than fatal so that a
    production-style deployment that injects real environment variables is not
    blocked by a development convenience.
    """
    if not ENV_FILE.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(ENV_FILE, override=False)
    return True


def describe_env_source() -> str:
    """Human-readable description of where configuration comes from."""
    return str(ENV_FILE) if ENV_FILE.is_file() else "(no .env file; using process environment)"
