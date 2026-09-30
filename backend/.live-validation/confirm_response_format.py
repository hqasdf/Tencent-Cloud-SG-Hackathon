"""Replicate the response_format finding and probe the fix direction.

Three requests, no retries, sequential:
  A2  no response_format                       -> expect 200 (replication)
  B2  response_format {"type": "json_object"}  -> expect 503 (replication)
  B3  response_format {"type": "json_schema"}  -> does the strict variant work?

Also resolves the API host so we can see whether it is Google infrastructure or
a local stub.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.agents.config import AgentSettings  # noqa: E402
from app.env import load_local_env  # noqa: E402
from diagnostic_matrix import SAFE_HEADERS, TINY_PROMPT, _safe_error_category, _send  # noqa: E402

HOST = "generativelanguage.googleapis.com"

TINY_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "tiny",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
}


def main() -> None:
    load_local_env()
    settings = AgentSettings.from_environment()
    model = settings.model or ""
    api_key = settings.api_key or ""
    url = f"{(settings.base_url or '').rstrip('/')}/chat/completions"

    dns: dict = {}
    try:
        dns["addresses"] = sorted({i[4][0] for i in socket.getaddrinfo(HOST, 443)})
    except Exception as error:  # noqa: BLE001 - diagnostic
        dns["error"] = type(error).__name__
    try:
        with socket.create_connection((HOST, 443), timeout=5) as sock:
            dns["peerAddress"] = f"{sock.getpeername()[0]}:{sock.getpeername()[1]}"
    except Exception as error:  # noqa: BLE001 - diagnostic
        dns["peerError"] = type(error).__name__

    messages = [{"role": "user", "content": TINY_PROMPT}]
    cases = (
        ("A2_NO_RESPONSE_FORMAT", {"model": model, "messages": messages, "temperature": 0.0}),
        (
            "B2_JSON_OBJECT",
            {"model": model, "messages": messages, "temperature": 0.0,
             "response_format": {"type": "json_object"}},
        ),
        (
            "B3_JSON_SCHEMA",
            {"model": model, "messages": messages, "temperature": 0.0,
             "response_format": TINY_SCHEMA},
        ),
    )

    results = []
    for label, payload in cases:
        outcome = _send(label, payload, url, api_key)
        results.append(outcome)
        print(f"--- {label}: {json.dumps(outcome)}", flush=True)

    print("\n=== DNS ===")
    print(json.dumps(dns, indent=2))
    print("\n=== RESULTS ===")
    print(json.dumps({"results": results}, indent=2))


if __name__ == "__main__":
    main()
