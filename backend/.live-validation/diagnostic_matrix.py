"""Live differential diagnostic matrix. Minimum calls, no retries, sequential.

Three cases, each one single HTTP attempt:

  A_MINIMAL            tiny prompt, NO response_format      -> is the endpoint
                                                              reachable at all?
  B_STRUCTURED_MINIMAL identical prompt + response_format   -> does json_object
                                                              break it?
  C_FULL_RIDER         the exact production Rider payload   -> does request
                                                              complexity break it?

Interpretation:
  A fails 503        -> provider/service capacity, not our request
  A ok, B fails      -> response_format incompatibility
  A+B ok, C fails    -> request complexity / timeout / payload

Safety: the request body is never printed. Only a fixed set of non-sensitive
response headers, the status code, body LENGTH, and Google's canonical error
status enum are recorded. The free-text upstream message is deliberately NOT
read, preserving the provider's "never surface the body" property.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.agents.config import AgentSettings  # noqa: E402
from app.env import load_local_env  # noqa: E402

TINY_PROMPT = "Reply with the single word: ok"
SAFE_HEADERS = (
    "server",
    "via",
    "content-type",
    "retry-after",
    "x-request-id",
    "x-goog-request-id",
    "date",
    "content-length",
    "x-cloud-trace-context",
)


def _safe_error_category(response: httpx.Response) -> str | None:
    """Google's canonical error enum, never the free-text message.

    `error.status` is a fixed vocabulary (UNAVAILABLE, RESOURCE_EXHAUSTED,
    INVALID_ARGUMENT, ...). It is a category, not prose, and cannot echo a
    request header.
    """
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - diagnostic
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        status = error.get("status")
        if isinstance(status, str):
            return status
        code = error.get("code")
        if isinstance(code, int):
            return f"code={code}"
    return None


def _send(label: str, payload: dict, url: str, api_key: str) -> dict:
    result: dict = {"label": label, "payloadBytes": len(json.dumps(payload).encode())}
    started = time.monotonic()
    try:
        with httpx.Client(timeout=60.0, trust_env=True) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        result["status"] = response.status_code
        result["headers"] = {
            k: v for k, v in response.headers.items() if k.lower() in SAFE_HEADERS
        }
        result["bodyBytes"] = len(response.content)
        result["errorCategory"] = _safe_error_category(response)
        if response.status_code < 400:
            result["outcome"] = "SUCCESS"
            result["choicesPresent"] = bool(
                isinstance(response.json().get("choices"), list)
                if isinstance(response.json(), dict)
                else False
            )
        else:
            result["outcome"] = "HTTP_ERROR"
    except httpx.TimeoutException as error:
        result["outcome"] = "TIMEOUT"
        result["errorType"] = type(error).__name__
    except Exception as error:  # noqa: BLE001 - diagnostic
        result["outcome"] = "TRANSPORT_ERROR"
        result["errorType"] = f"{type(error).__name__}"
    result["ms"] = int((time.monotonic() - started) * 1000)
    return result


def build_full_rider_payload(model: str, api_key: str, url: str) -> dict:
    """Rebuild the production Rider payload by intercepting the real provider."""
    from app.agents.context_builder import AdvocateContextBuilder
    from app.agents.provider import AgentCompletionRequest
    from app.agents.providers.openai_compatible import OpenAiCompatibleProvider
    from app.agents.rider_advocate import RiderAdvocateAgent
    from app.repositories.case_repository import MockCaseRepository
    from app.services.case_service import CaseService

    captured: list = []

    class _Capture(Exception):
        pass

    class _CapturingProvider:
        name = "capturing"

        def complete(self, request: AgentCompletionRequest) -> object:
            captured.append(request)
            raise _Capture()

    service = CaseService(MockCaseRepository())
    case = service._load_case("DISP-005")  # noqa: SLF001
    context = AdvocateContextBuilder().build(case, service.get_analysis("DISP-005"))

    settings = AgentSettings.from_environment()
    agent = RiderAdvocateAgent(_CapturingProvider(), settings)
    try:
        agent.argue_with_trace(context)
    except _Capture:
        pass
    request = captured[0]

    payloads: list = []

    class _StubResponse:
        status_code = 200
        headers: dict = {}

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"content": "{}"}}]}

    class _RecordingClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, u, headers=None, json=None):
            payloads.append(json)
            return _StubResponse()

    provider = OpenAiCompatibleProvider(
        base_url=url.removesuffix("/chat/completions"),
        model=model,
        api_key=api_key,
        timeout_seconds=60.0,
        max_retries=0,
        client_factory=lambda **kwargs: _RecordingClient(),
    )
    provider.complete(request)
    return payloads[0]


def main() -> None:
    load_local_env()
    settings = AgentSettings.from_environment()
    model = settings.model or ""
    api_key = settings.api_key or ""
    url = f"{(settings.base_url or '').rstrip('/')}/chat/completions"

    plan = {
        "url": url,
        "model": model,
        "sequential": True,
        "retries": 0,
        "cases": ["A_MINIMAL", "B_STRUCTURED_MINIMAL", "C_FULL_RIDER"],
        "stopRule": "stop immediately if A returns 503, or if any case returns 429",
    }
    print("=== PLAN ===")
    print(json.dumps(plan, indent=2), flush=True)

    messages = [{"role": "user", "content": TINY_PROMPT}]
    payload_a = {"model": model, "messages": messages, "temperature": 0.0}
    payload_b = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    payload_c = build_full_rider_payload(model, api_key, url)

    results = []
    for label, payload in (
        ("A_MINIMAL", payload_a),
        ("B_STRUCTURED_MINIMAL", payload_b),
        ("C_FULL_RIDER", payload_c),
    ):
        outcome = _send(label, payload, url, api_key)
        results.append(outcome)
        print(f"--- {label}: {json.dumps(outcome)}", flush=True)
        if outcome.get("status") == 429:
            print("STOP: 429 observed.", flush=True)
            break
        if label == "A_MINIMAL" and outcome.get("status") == 503:
            print("STOP: 503 on the MINIMAL request.", flush=True)
            break

    print("\n=== RESULTS ===")
    print(json.dumps({"results": results}, indent=2))


if __name__ == "__main__":
    main()
