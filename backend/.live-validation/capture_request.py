"""Offline capture of the EXACT advocate request shape. Zero network.

Two stages, both local:

1. Ask the real advocate agent what it would send, using a capturing provider
   that records the ``AgentCompletionRequest`` instead of transmitting it.
2. Feed that request through the real ``OpenAiCompatibleProvider`` with a
   recording stub client, so the JSON body is built by production code and
   merely intercepted before it leaves the process.

A dummy API key is used deliberately: no real credential is loaded, and nothing
is transmitted. Output is redacted -- header NAMES only, never values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.agents.context_builder import AdvocateContextBuilder  # noqa: E402
from app.agents.driver_advocate import DriverAdvocateAgent  # noqa: E402
from app.agents.provider import AgentCompletionRequest  # noqa: E402
from app.agents.providers.openai_compatible import (  # noqa: E402
    OpenAiCompatibleProvider,
)
from app.agents.rider_advocate import RiderAdvocateAgent  # noqa: E402
from app.env import load_local_env  # noqa: E402
from app.repositories.case_repository import MockCaseRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402

CASE_ID = "DISP-005"
DUMMY_KEY = "DUMMY-NOT-A-REAL-KEY"


class _Capture(Exception):
    """Raised to stop the agent once its request has been recorded."""


class _CapturingProvider:
    """Records the request the agent builds, then aborts."""

    name = "capturing"

    def __init__(self) -> None:
        self.requests: list[AgentCompletionRequest] = []

    def complete(self, request: AgentCompletionRequest) -> object:
        self.requests.append(request)
        raise _Capture()


class _StubResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.headers: dict = {}

    def json(self) -> dict:
        return self._body


class _RecordingClient:
    """Stands in for httpx.Client; records instead of transmitting."""

    def __init__(self, recorder: list, response: _StubResponse) -> None:
        self._recorder = recorder
        self._response = response

    def __enter__(self) -> "_RecordingClient":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, headers: dict | None = None, json: dict | None = None):
        self._recorder.append({"url": url, "headerNames": sorted((headers or {}).keys()), "body": json})
        return self._response


def _agent_request(agent, context) -> AgentCompletionRequest:
    provider = _CapturingProvider()
    agent._provider = provider  # noqa: SLF001 - diagnostic probe
    try:
        agent.argue_with_trace(context)
    except _Capture:
        pass
    return provider.requests[0]


def _provider_payload(request: AgentCompletionRequest, model: str) -> tuple[dict, dict]:
    recorder: list = []
    provider = OpenAiCompatibleProvider(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model=model,
        api_key=DUMMY_KEY,
        timeout_seconds=60.0,
        max_retries=0,
        client_factory=lambda **kwargs: _RecordingClient(
            recorder, _StubResponse(200, {"choices": [{"message": {"content": "{}"}}]})
        ),
    )
    provider.complete(request)
    entry = recorder[0]
    return entry, {"url": entry["url"], "headerNames": entry["headerNames"]}


def _describe(label: str, request: AgentCompletionRequest, model: str) -> dict:
    entry, meta = _provider_payload(request, model)
    body = entry["body"]
    messages = body["messages"]
    raw = json.dumps(body, ensure_ascii=False)
    return {
        "label": label,
        "url": meta["url"],
        "method": "POST",
        "headerNames": meta["headerNames"],
        "contentType": "application/json",
        "model": body["model"],
        "temperature": body["temperature"],
        "max_tokens": body.get("max_tokens"),
        "response_format": body.get("response_format"),
        "reasoning_effort_present": "reasoning_effort" in body,
        "stream_present": "stream" in body,
        "messageCount": len(messages),
        "roleSequence": [m["role"] for m in messages],
        "charsPerMessage": [len(m["content"]) for m in messages],
        "systemPromptChars": len(request.system_prompt),
        "userPromptChars": len(request.user_prompt),
        "schemaChars": len(json.dumps(request.response_schema or {})),
        "requestBodyBytes": len(raw.encode("utf-8")),
        "estimatedInputTokens_charsDiv4": len(raw) // 4,
        "payloadFieldNames": sorted(body.keys()),
    }


def main() -> None:
    load_local_env()
    service = CaseService(MockCaseRepository())
    case = service._load_case(CASE_ID)  # noqa: SLF001 - canonical app path
    analysis = service.get_analysis(CASE_ID)
    context = AdvocateContextBuilder().build(case, analysis)

    from app.agents.config import AgentSettings

    settings = AgentSettings.from_environment()
    model = settings.model or "gemini-3.8-flash"

    out = {"caseId": CASE_ID, "model": model, "cases": []}
    for label, agent in (
        ("RIDER", RiderAdvocateAgent(_CapturingProvider(), settings)),
        ("DRIVER", DriverAdvocateAgent(_CapturingProvider(), settings)),
    ):
        request = _agent_request(agent, context)
        out["cases"].append(_describe(label, request, model))

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
