"""Diagnostic: does egress go through a local proxy, and is that proxy healthy?

Makes NO Gemini API call. Uses a neutral Google endpoint (generate_204) so no
model quota is touched. The point is to test the transport, not the model.

Answers the §20 question: "could our own client/proxy be synthesising the 503?"
"""

from __future__ import annotations

import json
import os
import socket
import time

import httpx

TARGET = "https://www.google.com/generate_204"
PROXY_PORT = 60811
SAFE_HEADERS = {
    "server",
    "via",
    "x-cache",
    "content-type",
    "date",
    "x-request-id",
    "retry-after",
    "proxy-authenticate",
    "x-squid-error",
    "content-length",
}

report: dict = {}

# 1. Which proxy variables are present in this process?
report["env"] = {
    key: value
    for key, value in os.environ.items()
    if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
}

# 2. Is the proxy port actually listening?
try:
    with socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=3):
        report["proxyTcpConnect"] = "ok"
except Exception as error:  # noqa: BLE001 - diagnostic
    report["proxyTcpConnect"] = f"{type(error).__name__}: {error}"

# 3. DNS resolution of the target host
try:
    addresses = sorted({info[4][0] for info in socket.getaddrinfo("www.google.com", 443)})
    report["dns"] = addresses
except Exception as error:  # noqa: BLE001 - diagnostic
    report["dns"] = f"{type(error).__name__}: {error}"


def attempt(label: str, *, trust_env: bool) -> None:
    """One GET, recording status, timing and only non-sensitive headers."""
    outcome: dict = {"trustEnv": trust_env}
    started = time.monotonic()
    try:
        with httpx.Client(timeout=20.0, trust_env=trust_env) as client:
            response = client.get(TARGET)
        outcome["status"] = response.status_code
        outcome["headers"] = {
            key: value
            for key, value in response.headers.items()
            if key.lower() in SAFE_HEADERS
        }
        outcome["bodyBytes"] = len(response.content)
    except Exception as error:  # noqa: BLE001 - diagnostic
        outcome["error"] = f"{type(error).__name__}: {error}"
    outcome["ms"] = int((time.monotonic() - started) * 1000)
    report[label] = outcome


attempt("throughProxy", trust_env=True)
attempt("directNoProxy", trust_env=False)

print(json.dumps(report, indent=2))
