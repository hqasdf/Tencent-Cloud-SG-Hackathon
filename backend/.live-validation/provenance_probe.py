"""Provenance probe: is the 503 coming from Google, or from something local?

Sends the SAME minimal payload twice:
  P1  through the environment proxy   (trust_env=True  -- production behaviour)
  P2  directly, bypassing the proxy   (trust_env=False)

Compares status, the `Server` banner and timing. If the two differ, something
between us and Google is answering, and the 503 is not necessarily Google's.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.agents.config import AgentSettings  # noqa: E402
from app.env import load_local_env  # noqa: E402

TINY_PROMPT = "Reply with the single word: ok"
SAFE = ("server", "via", "content-type", "retry-after", "x-request-id", "date", "content-length")


def send(label: str, *, trust_env: bool, url: str, model: str, api_key: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": TINY_PROMPT}],
        "temperature": 0.0,
    }
    out: dict = {"label": label, "trustEnv": trust_env}
    started = time.monotonic()
    try:
        with httpx.Client(timeout=60.0, trust_env=trust_env) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        out["status"] = response.status_code
        out["headers"] = {k: v for k, v in response.headers.items() if k.lower() in SAFE}
        out["bodyBytes"] = len(response.content)
        out["bodyStartsWith"] = response.text[:60].replace("\n", " ")
    except Exception as error:  # noqa: BLE001 - diagnostic
        out["error"] = f"{type(error).__name__}"
    out["ms"] = int((time.monotonic() - started) * 1000)
    return out


def main() -> None:
    load_local_env()
    settings = AgentSettings.from_environment()
    model = settings.model or ""
    api_key = settings.api_key or ""
    url = f"{(settings.base_url or '').rstrip('/')}/chat/completions"

    results = [
        send("P1_VIA_PROXY", trust_env=True, url=url, model=model, api_key=api_key),
        send("P2_DIRECT_NO_PROXY", trust_env=False, url=url, model=model, api_key=api_key),
    ]
    print(json.dumps({"url": url, "results": results}, indent=2))


if __name__ == "__main__":
    main()
