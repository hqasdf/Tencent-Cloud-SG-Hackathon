"""Server-side gate for the replay API.

Replay is a developer facility. Exposing it on the resolution endpoint without a
gate would mean any client could ask the service to serve stored AI output
instead of calling a model, which is a capability the production path should not
have.

So the API gate is **off unless explicitly enabled** by an environment variable.
Three properties follow, and all three matter:

* the default deployment behaves exactly as it did before Stage 6B existed;
* enabling it is a deliberate act by whoever runs the server, not a field a
  client can set;
* the CLI is unaffected, because it does not go through HTTP at all — which is
  why the CLI remains the primary way to capture and replay.

The refusal is a 403 with a message naming the variable, not a 404. A developer
who forgot the flag should be told which flag, and a silent "not found" would
send them looking for a missing artefact instead.
"""

from __future__ import annotations

import os

REPLAY_API_ENV = "REPLAY_API_ENABLED"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def replay_api_enabled() -> bool:
    """Whether the resolution endpoint may be asked to replay.

    Read per call rather than cached, so a test can toggle it with
    ``monkeypatch.setenv`` and a long-running server picks up a change without a
    restart.
    """
    return (os.getenv(REPLAY_API_ENV) or "").strip().lower() in _TRUTHY


__all__ = ["REPLAY_API_ENV", "replay_api_enabled"]
