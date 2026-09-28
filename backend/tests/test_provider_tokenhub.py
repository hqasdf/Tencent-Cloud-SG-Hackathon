"""Stage 4B — real provider integration, tested entirely offline.

Automated tests must never reach a live paid API, so every test here injects a
stub HTTP client into the provider. That is exactly why
``OpenAiCompatibleProvider`` accepts a ``client_factory``.

What this file proves:

  * the configured base URL and model are what actually get sent
  * the credential is transmitted but never surfaced anywhere else
  * every documented real-mode failure is normalised into one error type with a
    machine-readable code
  * token usage is parsed when present and stays ``None`` when absent
  * a real provider failure degrades one advocate and never silently falls back
    to mock
  * hallucinated evidence is still rejected when the claim comes from a real
    model response rather than the mock provider
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.agents.base_advocate import AdvocateAgentError, AdvocateOutputErrorCode
from app.agents.config import (
    TOKENHUB_API_KEY_ENV,
    AgentConfigurationError,
    AgentSettings,
)
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.provider import (
    AgentCompletionRequest,
    LlmProviderError,
    ProviderErrorCode,
)
from app.agents.providers.diagnostics import (
    STATUS_AUTH_FAILED,
    STATUS_CREDENTIAL_REQUIRED,
    STATUS_MOCK_MODE,
    STATUS_MODEL_UNAVAILABLE,
    STATUS_OK,
    verify_provider_access,
)
from app.agents.providers.openai_compatible import OpenAiCompatibleProvider, parse_json_object
from app.agents.providers.registry import get_provider
from app.agents.providers.tencent_tokenhub import DEFAULT_BASE_URL, TencentTokenHubProvider
from app.agents.rider_advocate import RiderAdvocateAgent
from app.data.cases import MOCK_CASES
from app.models.agent import NoShowFacts, RouteDeviationFacts
from app.services.advocate_orchestrator import AdvocateOrchestratorService
from app.services.dispute_analysis import DisputeAnalysisService

# A deliberately recognisable fake credential. Several tests assert this string
# never appears in an error, a completion, or a serialized response.
SECRET = "tok_live_FAKE_do_not_log_9f3a1c"
MODEL = "hy4-preview"


# ---------------------------------------------------------------------------
# Offline HTTP stub
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: object = None,
        json_error: Exception | None = None,
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        # A plain dict, which is why the provider reads headers case-insensitively.
        self.headers = headers or {}

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _StubClient:
    """Minimal stand-in for httpx.Client. Records every outbound request.

    The response queue is held by reference, not copied, because the provider
    builds a fresh client per attempt and per advocate. A copied queue would
    replay the first canned response for every call.
    """

    def __init__(self, responses: list[_StubResponse], recorded: list[dict], raise_on_request: Exception | None):
        self._responses = responses
        self._recorded = recorded
        self._raise = raise_on_request

    def __enter__(self) -> "_StubClient":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def _handle(self, method: str, url: str, headers: dict | None, payload: dict | None) -> _StubResponse:
        self._recorded.append({"method": method, "url": url, "headers": headers, "json": payload})
        if self._raise is not None:
            raise self._raise
        if not self._responses:
            raise AssertionError("stub client ran out of canned responses")
        return self._responses.pop(0)

    def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> _StubResponse:
        return self._handle("POST", url, headers, json)

    def get(self, url: str, headers: dict | None = None) -> _StubResponse:
        return self._handle("GET", url, headers, None)


def make_provider(
    responses: list[_StubResponse] | None = None,
    *,
    model: str = MODEL,
    api_key: str = SECRET,
    base_url: str | None = None,
    raise_on_request: Exception | None = None,
    **kwargs: object,
) -> tuple[TencentTokenHubProvider, list[dict]]:
    recorded: list[dict] = []
    # One shared queue: the provider creates a client per attempt and per
    # advocate, so the canned responses must be consumed across all of them.
    queue: list[_StubResponse] = list(responses or [])

    def factory(**_ignored: object) -> _StubClient:
        return _StubClient(queue, recorded, raise_on_request)

    provider = TencentTokenHubProvider(
        model=model,
        api_key=api_key,
        base_url=base_url,
        client_factory=factory,
        # Never actually sleep: the backoff schedule is asserted from the
        # recorded delays, not from wall-clock time.
        sleeper=kwargs.pop("sleeper", lambda _seconds: None),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )
    return provider, recorded


def completion_body(content: str, usage: dict | None = None, finish_reason: str = "stop") -> dict:
    body: dict = {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return body


def request(**overrides: object) -> AgentCompletionRequest:
    payload = {"system_prompt": "system rules", "user_prompt": "case context"}
    payload.update(overrides)  # type: ignore[arg-type]
    return AgentCompletionRequest(**payload)  # type: ignore[arg-type]


def ok(
    content: str = '{"side": "RIDER"}',
    usage: dict | None = None,
    finish_reason: str = "stop",
) -> _StubResponse:
    return _StubResponse(payload=completion_body(content, usage, finish_reason))


# ---------------------------------------------------------------------------
# Configuration and registry
# ---------------------------------------------------------------------------


def test_tokenhub_defaults_to_the_singapore_base_url() -> None:
    provider, _ = make_provider()
    assert provider.base_url == DEFAULT_BASE_URL
    assert provider.base_url == "https://tokenhub-intl.tencentmaas.com/v1"


def test_explicit_base_url_overrides_the_default() -> None:
    provider, _ = make_provider(base_url="https://gateway.internal.example/v1")
    assert provider.base_url == "https://gateway.internal.example/v1"


def test_registry_builds_a_tokenhub_provider() -> None:
    settings = AgentSettings(
        mode="real",
        provider="tencent_tokenhub",
        model=MODEL,
        api_key=SECRET,
        base_url="https://tokenhub-intl.tencentmaas.com/v1",
    )
    provider = get_provider(settings)
    assert isinstance(provider, TencentTokenHubProvider)
    # Metadata must name the configured provider, not the reused transport.
    assert provider.name == "tencent_tokenhub"


def test_registry_rejects_tokenhub_without_real_mode() -> None:
    settings = AgentSettings(mode="mock", provider="tencent_tokenhub", model=MODEL, api_key=SECRET)
    with pytest.raises(AgentConfigurationError):
        get_provider(settings)


def test_real_mode_requires_a_model() -> None:
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", api_key=SECRET)
    with pytest.raises(AgentConfigurationError) as caught:
        settings.validate()
    assert "AGENT_MODEL" in str(caught.value)


def test_real_mode_requires_a_credential() -> None:
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=MODEL)
    with pytest.raises(AgentConfigurationError) as caught:
        settings.validate()
    # The message must point at the documented alias without revealing a value.
    assert TOKENHUB_API_KEY_ENV in str(caught.value)


def test_tokenhub_does_not_require_an_explicit_base_url() -> None:
    """TokenHub has a documented default, so AGENT_BASE_URL is optional."""
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=MODEL, api_key=SECRET)
    settings.validate()


def test_documented_alias_supplies_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "tencent_tokenhub")
    monkeypatch.setenv("AGENT_MODEL", MODEL)
    monkeypatch.setenv(TOKENHUB_API_KEY_ENV, SECRET)

    settings = AgentSettings.from_environment()
    assert settings.api_key == SECRET
    settings.validate()


def test_explicit_agent_api_key_wins_over_the_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "tencent_tokenhub")
    monkeypatch.setenv("AGENT_MODEL", MODEL)
    monkeypatch.setenv("AGENT_API_KEY", "tok_explicit")
    monkeypatch.setenv(TOKENHUB_API_KEY_ENV, SECRET)

    assert AgentSettings.from_environment().api_key == "tok_explicit"


def test_the_alias_is_not_borrowed_by_other_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only TokenHub may fall back to the TokenHub alias.

    Otherwise an unrelated provider could silently authenticate with a
    credential that was never meant for it.
    """
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setenv(TOKENHUB_API_KEY_ENV, SECRET)

    assert AgentSettings.from_environment().api_key is None


def test_health_description_reports_presence_but_never_the_key() -> None:
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=MODEL, api_key=SECRET)
    described = settings.describe()
    assert described["credential_configured"] is True
    assert SECRET not in json.dumps(described)


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


def test_completion_posts_to_the_configured_base_url_and_model() -> None:
    provider, recorded = make_provider([ok()])
    provider.complete(request())

    assert len(recorded) == 1
    sent = recorded[0]
    assert sent["method"] == "POST"
    assert sent["url"] == f"{DEFAULT_BASE_URL}/chat/completions"
    assert sent["json"]["model"] == MODEL


def test_a_different_model_is_sent_when_configured() -> None:
    """No model may be baked into the integration."""
    provider, recorded = make_provider([ok()], model="kimi-k3")
    provider.complete(request())
    assert recorded[0]["json"]["model"] == "kimi-k3"


def test_credential_is_sent_as_a_bearer_header() -> None:
    provider, recorded = make_provider([ok()])
    provider.complete(request())
    assert recorded[0]["headers"]["Authorization"] == f"Bearer {SECRET}"


def test_system_and_user_prompts_are_sent_as_separate_messages() -> None:
    provider, recorded = make_provider([ok()])
    provider.complete(request(system_prompt="SYS", user_prompt="USER"))
    messages = recorded[0]["json"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == "SYS"
    assert messages[1]["content"] == "USER"


def test_json_object_response_format_is_requested() -> None:
    provider, recorded = make_provider([ok()])
    provider.complete(request())
    assert recorded[0]["json"]["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Successful responses and token usage
# ---------------------------------------------------------------------------


def test_successful_response_is_returned_as_a_completion() -> None:
    provider, _ = make_provider([ok('{"side": "RIDER"}')])
    completion = provider.complete(request())
    assert completion.raw_text == '{"side": "RIDER"}'
    assert completion.provider_name == "tencent_tokenhub"
    assert completion.model_name == MODEL
    assert completion.duration_ms >= 0


def test_openai_style_usage_is_parsed() -> None:
    usage = {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540}
    provider, _ = make_provider([ok(usage=usage)])
    completion = provider.complete(request())
    assert (completion.input_tokens, completion.output_tokens, completion.total_tokens) == (1200, 340, 1540)


def test_alternate_usage_naming_is_parsed() -> None:
    usage = {"input_tokens": 900, "output_tokens": 110, "total_tokens": 1010}
    provider, _ = make_provider([ok(usage=usage)])
    completion = provider.complete(request())
    assert (completion.input_tokens, completion.output_tokens, completion.total_tokens) == (900, 110, 1010)


def test_total_tokens_is_derived_when_the_gateway_omits_it() -> None:
    provider, _ = make_provider([ok(usage={"prompt_tokens": 10, "completion_tokens": 5})])
    completion = provider.complete(request())
    assert completion.total_tokens == 15


def test_missing_usage_is_reported_as_unknown_not_zero() -> None:
    """A gateway that reports no usage must not look like one reporting zero."""
    provider, _ = make_provider([ok()])
    completion = provider.complete(request())
    assert completion.input_tokens is None
    assert completion.output_tokens is None
    assert completion.total_tokens is None


def test_malformed_usage_values_are_ignored() -> None:
    provider, _ = make_provider([ok(usage={"prompt_tokens": "many", "completion_tokens": None})])
    completion = provider.complete(request())
    assert completion.total_tokens is None


# ---------------------------------------------------------------------------
# Failure normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ProviderErrorCode.AUTHENTICATION_FAILED, False),
        (403, ProviderErrorCode.ACCESS_DENIED, False),
        (404, ProviderErrorCode.MODEL_NOT_FOUND, False),
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (500, ProviderErrorCode.SERVER_ERROR, True),
        (503, ProviderErrorCode.SERVER_ERROR, True),
        (400, ProviderErrorCode.REQUEST_REJECTED, False),
    ],
)
def test_http_status_is_classified(status: int, code: str, retryable: bool) -> None:
    provider, _ = make_provider([_StubResponse(status_code=status, payload={"error": "nope"})])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == code
    assert caught.value.retryable is retryable


def test_authentication_failure_names_the_provider() -> None:
    provider, _ = make_provider([_StubResponse(status_code=401, payload={})])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.provider == "tencent_tokenhub"


def test_timeout_is_normalised() -> None:
    provider, _ = make_provider(raise_on_request=httpx.ReadTimeout("too slow"))
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.TIMEOUT
    assert caught.value.retryable is True


def test_network_error_is_normalised() -> None:
    provider, _ = make_provider(raise_on_request=httpx.ConnectError("no route to host"))
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.NETWORK_ERROR


def test_non_json_body_is_reported_as_malformed() -> None:
    provider, _ = make_provider([_StubResponse(payload=None, json_error=ValueError("not json"))])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.MALFORMED_RESPONSE


def test_response_without_choices_is_reported_as_malformed() -> None:
    provider, _ = make_provider([_StubResponse(payload={"id": "x"})])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.MALFORMED_RESPONSE


def test_empty_content_is_rejected_rather_than_parsed() -> None:
    provider, _ = make_provider([ok("   ")])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.EMPTY_CONTENT


def test_rate_limit_is_retried_when_the_server_says_when_to_come_back() -> None:
    """A 429 carrying Retry-After is worth one bounded retry."""
    provider, recorded = make_provider(
        [_StubResponse(status_code=429, payload={}, headers={"Retry-After": "0"}), ok()],
        max_retries=1,
    )
    completion = provider.complete(request())
    assert completion.raw_text == '{"side": "RIDER"}'
    assert len(recorded) == 2


def test_rate_limit_without_retry_after_fails_safely() -> None:
    """No Retry-After means no basis for a delay, so do not spend more quota.

    Retrying a rate limit immediately is the worst option: it consumes another
    request from the same exhausted allowance and usually fails the same way.
    """
    provider, recorded = make_provider(
        [_StubResponse(status_code=429, payload={}), ok()],
        max_retries=2,
    )
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.RATE_LIMITED
    assert len(recorded) == 1
    assert caught.value.attempts == 1


def test_authentication_failure_is_never_retried() -> None:
    """Retrying a bad key only burns latency and can trip lockouts."""
    provider, recorded = make_provider([_StubResponse(status_code=401, payload={})], max_retries=2)
    with pytest.raises(LlmProviderError):
        provider.complete(request())
    assert len(recorded) == 1


def test_error_messages_never_contain_the_credential() -> None:
    provider, _ = make_provider([_StubResponse(status_code=401, payload={"echo": SECRET})])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert SECRET not in str(caught.value)
    assert "Bearer" not in str(caught.value)


def test_upstream_error_body_is_never_surfaced() -> None:
    """A gateway error body can echo the request headers it received."""
    provider, _ = make_provider(
        [_StubResponse(status_code=500, payload={"detail": f"Authorization: Bearer {SECRET}"})]
    )
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert SECRET not in str(caught.value)
    assert "Authorization" not in str(caught.value)


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


def test_list_models_returns_model_identifiers() -> None:
    payload = {"data": [{"id": "hy4-preview"}, {"id": "kimi-k3"}, {"nope": 1}]}
    provider, recorded = make_provider([_StubResponse(payload=payload)])
    assert provider.list_models() == ["hy4-preview", "kimi-k3"]
    assert recorded[0]["url"] == f"{DEFAULT_BASE_URL}/models"
    assert recorded[0]["method"] == "GET"


def test_list_models_requires_authentication() -> None:
    provider, _ = make_provider([_StubResponse(status_code=401, payload={})])
    with pytest.raises(LlmProviderError) as caught:
        provider.list_models()
    assert caught.value.code == ProviderErrorCode.AUTHENTICATION_FAILED


def test_list_models_rejects_a_body_without_a_model_array() -> None:
    provider, _ = make_provider([_StubResponse(payload={"object": "list"})])
    with pytest.raises(LlmProviderError) as caught:
        provider.list_models()
    assert caught.value.code == ProviderErrorCode.MALFORMED_RESPONSE


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def test_markdown_fenced_json_is_accepted() -> None:
    """Real models wrap JSON in fences even when asked for a bare object."""
    fenced = '```json\n{"side": "RIDER", "claims": []}\n```'
    assert parse_json_object(fenced) == {"side": "RIDER", "claims": []}


def test_bare_json_is_accepted() -> None:
    assert parse_json_object('{"side": "DRIVER"}') == {"side": "DRIVER"}


def test_prose_around_json_is_still_a_failure() -> None:
    with pytest.raises(ValueError):
        parse_json_object('Sure! Here is the JSON: {"side": "RIDER"}')


def test_a_json_array_is_not_an_object() -> None:
    with pytest.raises(ValueError):
        parse_json_object("[1, 2, 3]")


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_diagnostics_reports_mock_mode_without_contacting_anything() -> None:
    diagnostic = verify_provider_access(AgentSettings(mode="mock", provider="mock"))
    assert diagnostic.status == STATUS_MOCK_MODE
    assert diagnostic.ok is False


def test_diagnostics_reports_credential_required_without_a_key() -> None:
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=MODEL)
    diagnostic = verify_provider_access(settings)
    assert diagnostic.status == STATUS_CREDENTIAL_REQUIRED
    assert "CREDENTIAL REQUIRED" in diagnostic.message


def test_diagnostics_confirms_a_working_provider_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": [{"id": MODEL}, {"id": "kimi-k3"}]}

    def factory(**_ignored: object) -> _StubClient:
        return _StubClient([_StubResponse(payload=payload)], [], None)

    monkeypatch.setattr(
        "app.agents.providers.diagnostics.get_provider",
        lambda settings: TencentTokenHubProvider(
            model=settings.model or "", api_key=settings.api_key or "", client_factory=factory
        ),
    )
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=MODEL, api_key=SECRET)
    diagnostic = verify_provider_access(settings)
    assert diagnostic.status == STATUS_OK
    assert diagnostic.model_available is True
    assert diagnostic.model_count == 2


def test_diagnostics_flags_a_model_the_account_cannot_see(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": [{"id": "kimi-k3"}]}

    def factory(**_ignored: object) -> _StubClient:
        return _StubClient([_StubResponse(payload=payload)], [], None)

    monkeypatch.setattr(
        "app.agents.providers.diagnostics.get_provider",
        lambda settings: TencentTokenHubProvider(
            model=settings.model or "", api_key=settings.api_key or "", client_factory=factory
        ),
    )
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=MODEL, api_key=SECRET)
    diagnostic = verify_provider_access(settings)
    assert diagnostic.status == STATUS_MODEL_UNAVAILABLE
    assert diagnostic.model_available is False


def test_diagnostics_reports_a_rejected_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(**_ignored: object) -> _StubClient:
        return _StubClient([_StubResponse(status_code=401, payload={})], [], None)

    monkeypatch.setattr(
        "app.agents.providers.diagnostics.get_provider",
        lambda settings: TencentTokenHubProvider(
            model=settings.model or "", api_key=settings.api_key or "", client_factory=factory
        ),
    )
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=MODEL, api_key=SECRET)
    diagnostic = verify_provider_access(settings)
    assert diagnostic.status == STATUS_AUTH_FAILED
    assert SECRET not in json.dumps(diagnostic.as_dict())


def _diagnostic_for_listing(monkeypatch: pytest.MonkeyPatch, *, configured: str, listed: list[str]):
    """Run the availability check against a canned /models listing."""
    payload = {"data": [{"id": model_id} for model_id in listed]}

    def factory(**_ignored: object) -> _StubClient:
        return _StubClient([_StubResponse(payload=payload)], [], None)

    monkeypatch.setattr(
        "app.agents.providers.diagnostics.get_provider",
        lambda settings: TencentTokenHubProvider(
            model=settings.model or "", api_key=settings.api_key or "", client_factory=factory
        ),
    )
    settings = AgentSettings(mode="real", provider="tencent_tokenhub", model=configured, api_key=SECRET)
    return verify_provider_access(settings)


def test_diagnostics_matches_a_model_listed_with_the_models_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini lists `models/gemini-3.8-flash`; AGENT_MODEL is written bare.

    Comparing the raw strings reported a configured model as unavailable when it
    was in fact present, so the comparison ignores the prefix.
    """
    diagnostic = _diagnostic_for_listing(
        monkeypatch, configured="gemini-3.8-flash", listed=["models/gemini-3.8-flash"]
    )
    assert diagnostic.status == STATUS_OK
    assert diagnostic.model_available is True


def test_diagnostics_matches_a_model_configured_with_the_models_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tolerance must work in the other direction too."""
    diagnostic = _diagnostic_for_listing(
        monkeypatch, configured="models/gemini-3.8-flash", listed=["gemini-3.8-flash"]
    )
    assert diagnostic.status == STATUS_OK


def test_the_prefix_tolerance_does_not_match_a_different_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalising the prefix must not decay into a fuzzy match.

    If it did, a typo'd or superseded model would be silently accepted and the
    failure would only surface later, mid-run, as a confusing provider error.
    """
    diagnostic = _diagnostic_for_listing(
        monkeypatch,
        configured="gemini-3.8-flash",
        listed=["models/gemini-3.7-flash", "models/gemini-3.8-flash-lite"],
    )
    assert diagnostic.status == STATUS_MODEL_UNAVAILABLE
    assert diagnostic.model_available is False


# ---------------------------------------------------------------------------
# End to end through the orchestrator, with a stubbed provider
# ---------------------------------------------------------------------------


def clean_case(dispute_type: str):
    """Pick a fixture whose evidence is entirely Verified.

    Selection is by property rather than by ID so this keeps working when the
    fixture set changes.
    """
    for case in MOCK_CASES:
        if case.dispute_type == dispute_type and all(item.status == "Verified" for item in case.evidence):
            return case
    raise AssertionError(f"no clean {dispute_type} fixture")


def context_for(case):
    analysis = DisputeAnalysisService().analyze(case)
    return AdvocateContextBuilder().build(case, analysis), analysis


def advocate_json(context, side: str, *, evidence_ids: list[str] | None = None, claim_id: str = "C1") -> str:
    """Build a schema-valid advocate response grounded in the trusted context."""
    facts = context.facts
    if isinstance(facts, RouteDeviationFacts):
        asserted = [{"fact": "DEVIATION_PERCENTAGE", "value": facts.deviation_percentage}]
    elif isinstance(facts, NoShowFacts):
        asserted = [{"fact": "WAITING_DURATION_SECONDS", "value": facts.waiting_duration_seconds}]
    else:  # pragma: no cover - defensive
        asserted = []
    payload = {
        "side": side,
        "summary": f"{side} summary.",
        "claims": [
            {
                "claimId": claim_id,
                "claim": "The trusted measurement supports this argument.",
                "evidenceIds": evidence_ids if evidence_ids is not None else [context.evidence[0].id],
                "policyRefs": [context.policy.rules[0].rule_id],
                "reasoningSummary": "Derived from the trusted facts only.",
                "importance": "HIGH",
                "assertedFacts": asserted,
                "disputedEvidenceIds": [],
            }
        ],
        "requestedOutcome": "NO_REFUND",
        "contextAcknowledged": True,
    }
    return json.dumps(payload)


def real_settings() -> AgentSettings:
    return AgentSettings(
        mode="real",
        provider="tencent_tokenhub",
        model=MODEL,
        api_key=SECRET,
        base_url=DEFAULT_BASE_URL,
    )


def test_real_mode_runs_both_advocates_and_verifies_their_claims() -> None:
    case = clean_case("route_deviation")
    context, analysis = context_for(case)
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    provider, _ = make_provider(
        [
            ok(advocate_json(context, "RIDER"), usage),
            ok(advocate_json(context, "DRIVER"), usage),
        ]
    )
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.agent_run.mode == "real"
    assert result.agent_run.provider == "tencent_tokenhub"
    assert result.agent_run.model == MODEL
    assert result.rider.status == "COMPLETE"
    assert result.driver.status == "COMPLETE"
    assert result.verification_summary.verified_count == 2
    assert result.verification_summary.rejected_count == 0


def test_real_mode_records_latency_tokens_and_claim_counts_per_side() -> None:
    case = clean_case("no_show_charge")
    context, analysis = context_for(case)
    usage = {"prompt_tokens": 700, "completion_tokens": 300, "total_tokens": 1000}
    provider, _ = make_provider(
        [ok(advocate_json(context, "RIDER"), usage), ok(advocate_json(context, "DRIVER"), usage)]
    )
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.rider.execution is not None
    assert result.rider.execution.provider == "tencent_tokenhub"
    assert result.rider.execution.model == MODEL
    assert result.rider.execution.total_tokens == 1000
    assert result.rider.execution.generated_claim_count == 1
    assert result.rider.execution.verified_claim_count == 1
    assert result.rider.execution.rejected_claim_count == 0
    assert result.rider.execution.malformed_output is False

    # The run-level totals aggregate both advocates.
    assert result.agent_run.total_tokens == 2000
    assert result.agent_run.input_tokens == 1400
    assert result.agent_run.output_tokens == 600


def test_hallucinated_evidence_from_a_real_model_is_still_rejected() -> None:
    """The verification layer is unchanged by the provider swap."""
    case = clean_case("route_deviation")
    context, analysis = context_for(case)
    provider, _ = make_provider(
        [
            ok(advocate_json(context, "RIDER", evidence_ids=["E99"])),
            ok(advocate_json(context, "DRIVER")),
        ]
    )
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.rider.status == "COMPLETE"
    assert result.rider.verified_claims == []
    assert len(result.rider.rejected_claims) == 1
    assert result.rider.rejected_claims[0].reason == "EVIDENCE_ID_NOT_FOUND"
    assert result.rider.rejected_claims[0].evidence_ids == ["E99"]
    assert result.rider.execution.rejected_claim_count == 1
    assert result.rider.execution.rejection_reasons == ["EVIDENCE_ID_NOT_FOUND"]
    # The other advocate and the deterministic analysis are unaffected.
    assert result.driver.status == "COMPLETE"
    assert result.verification_summary.verified_count == 1


def test_contradicted_structured_fact_from_a_real_model_is_rejected() -> None:
    case = clean_case("no_show_charge")
    context, analysis = context_for(case)
    payload = json.loads(advocate_json(context, "RIDER"))
    payload["claims"][0]["assertedFacts"] = [{"fact": "WAITING_DURATION_SECONDS", "value": 999999}]
    provider, _ = make_provider([ok(json.dumps(payload)), ok(advocate_json(context, "DRIVER"))])
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.rider.verified_claims == []
    assert result.rider.rejected_claims[0].reason == "FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS"


def test_a_provider_failure_degrades_one_side_and_never_falls_back_to_mock() -> None:
    """No silent fallback: the failure must be visible and honest."""
    case = clean_case("route_deviation")
    context, analysis = context_for(case)

    responses = [
        _StubResponse(status_code=401, payload={}),
        ok(advocate_json(context, "DRIVER")),
    ]
    provider, _ = make_provider(responses)
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.rider.status == "FAILED"
    assert result.rider.verified_claims == []
    assert result.rider.execution.failure_code == ProviderErrorCode.AUTHENTICATION_FAILED
    assert result.rider.execution.malformed_output is False

    # Still reports the real provider: it did not quietly become mock.
    assert result.agent_run.mode == "real"
    assert result.agent_run.provider == "tencent_tokenhub"
    assert "mock" not in (result.rider.failure_reason or "").lower()

    # The other advocate is untouched.
    assert result.driver.status == "COMPLETE"
    assert result.driver.verified_claims


def test_unusable_model_output_is_flagged_as_malformed_not_as_an_outage() -> None:
    case = clean_case("route_deviation")
    context, analysis = context_for(case)
    provider, _ = make_provider([ok("not json at all"), ok(advocate_json(context, "DRIVER"))])
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.rider.status == "FAILED"
    assert result.rider.execution.malformed_output is True
    assert result.rider.execution.failure_code == AdvocateOutputErrorCode.MALFORMED_JSON


def test_a_wrong_side_response_is_rejected_by_the_agent() -> None:
    case = clean_case("route_deviation")
    context, analysis = context_for(case)
    provider, _ = make_provider([ok(advocate_json(context, "DRIVER")), ok(advocate_json(context, "DRIVER"))])
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.rider.status == "FAILED"
    assert result.rider.execution.failure_code == AdvocateOutputErrorCode.WRONG_SIDE


def test_agent_raises_a_coded_error_for_malformed_output() -> None:
    provider, _ = make_provider([ok("still not json")])
    agent = RiderAdvocateAgent(provider, real_settings())
    with pytest.raises(AdvocateAgentError) as caught:
        agent.argue(context_for(clean_case("route_deviation"))[0])
    assert caught.value.code == AdvocateOutputErrorCode.MALFORMED_JSON


def test_no_secret_appears_anywhere_in_the_real_mode_response() -> None:
    case = clean_case("no_show_charge")
    context, analysis = context_for(case)
    provider, _ = make_provider([ok(advocate_json(context, "RIDER")), ok(advocate_json(context, "DRIVER"))])
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    serialized = result.model_dump_json(by_alias=True)
    assert SECRET not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
    assert "api_key" not in serialized
    assert "apiKey" not in serialized


def test_deterministic_analysis_is_unchanged_by_a_real_mode_run() -> None:
    case = clean_case("route_deviation")
    context, analysis = context_for(case)
    before = analysis.model_dump_json(by_alias=True)

    provider, _ = make_provider([ok(advocate_json(context, "RIDER")), ok(advocate_json(context, "DRIVER"))])
    AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert analysis.model_dump_json(by_alias=True) == before


# ---------------------------------------------------------------------------
# Truncation
#
# Learned from a real local run: qwen3.8:27b is a thinking model and Ollama
# counts reasoning tokens toward the completion budget. At the original
# 2000-token cap the JSON was cut off mid-string, and the failure surfaced as
# "malformed JSON" — which points the operator at the prompt instead of at the
# token budget.
# ---------------------------------------------------------------------------


def test_finish_reason_is_carried_through() -> None:
    provider, _ = make_provider([ok(finish_reason="length")])
    completion = provider.complete(request())
    assert completion.finish_reason == "length"
    assert completion.truncated is True


def test_normal_completion_is_not_reported_as_truncated() -> None:
    provider, _ = make_provider([ok()])
    completion = provider.complete(request())
    assert completion.finish_reason == "stop"
    assert completion.truncated is False


def test_a_truncated_response_is_reported_as_truncation_not_bad_json() -> None:
    """The diagnosis must name the real cause."""
    provider, _ = make_provider([ok('{"side": "RIDER", "summary": "unterminated', finish_reason="length")])
    agent = RiderAdvocateAgent(provider, real_settings())
    with pytest.raises(AdvocateAgentError) as caught:
        agent.argue(context_for(clean_case("route_deviation"))[0])
    assert caught.value.code == AdvocateOutputErrorCode.OUTPUT_TRUNCATED
    assert "AGENT_MAX_TOKENS" in str(caught.value)


def test_truncated_output_is_flagged_and_keeps_its_token_metrics() -> None:
    """A model that runs out of budget must still show its cost."""
    case = clean_case("route_deviation")
    context, analysis = context_for(case)
    usage = {"prompt_tokens": 3000, "completion_tokens": 2000, "total_tokens": 5000}
    provider, _ = make_provider(
        [
            ok('{"side": "RIDER", "summary": "cut off', usage, finish_reason="length"),
            ok(advocate_json(context, "DRIVER")),
        ]
    )
    result = AdvocateOrchestratorService(settings=real_settings(), provider=provider).run(case, analysis)

    assert result.rider.status == "FAILED"
    execution = result.rider.execution
    assert execution.failure_code == AdvocateOutputErrorCode.OUTPUT_TRUNCATED
    assert execution.malformed_output is True
    # Metrics survive the failure, which is the whole point.
    assert execution.total_tokens == 5000
    assert execution.output_tokens == 2000
    assert execution.model == MODEL
    # And the other advocate is unaffected.
    assert result.driver.status == "COMPLETE"


def test_configured_max_tokens_is_actually_sent() -> None:
    context = context_for(clean_case("route_deviation"))[0]
    settings = AgentSettings(mode="mock", max_tokens=8192)
    provider, recorded = make_provider([ok(advocate_json(context, "RIDER"))])
    RiderAdvocateAgent(provider, settings).argue(context)
    assert recorded[0]["json"]["max_tokens"] == 8192


def test_max_tokens_is_configurable_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "8192")
    assert AgentSettings.from_environment().max_tokens == 8192


def test_default_max_tokens_is_unchanged_for_cloud_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "")
    assert AgentSettings.from_environment().max_tokens == 2000


def test_a_non_numeric_max_tokens_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "lots")
    with pytest.raises(AgentConfigurationError):
        AgentSettings.from_environment()


# ---------------------------------------------------------------------------
# Retries
#
# Observed against the live Gemini endpoint: it answers a perfectly valid
# request with HTTP 503 UNAVAILABLE ("This model is currently experiencing high
# demand") under load. The provider already classified 503 as retryable, but
# `max_retries` was unreachable from configuration, so an advocate failed on a
# purely transient condition. These tests pin the wiring.
# ---------------------------------------------------------------------------


def test_max_retries_defaults_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default behaviour must not change: no retries unless asked for."""
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_MAX_RETRIES", "")
    assert AgentSettings.from_environment().max_retries == 0


def test_max_retries_is_configurable_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_MAX_RETRIES", "2")
    assert AgentSettings.from_environment().max_retries == 2


def test_a_negative_max_retries_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_MAX_RETRIES", "-1")
    with pytest.raises(AgentConfigurationError):
        AgentSettings.from_environment()


def test_a_transient_server_error_is_retried_and_can_succeed() -> None:
    """503 then 200 must produce a completion, not a failure."""
    provider, recorded = make_provider(
        [_StubResponse(status_code=503, payload={}), ok()],
        max_retries=2,
    )
    completion = provider.complete(request())
    assert completion.raw_text == '{"side": "RIDER"}'
    assert len(recorded) == 2


def test_a_server_error_still_fails_once_retries_are_exhausted() -> None:
    """Retrying must not turn a real outage into a silent success."""
    provider, recorded = make_provider(
        [_StubResponse(status_code=503, payload={}) for _ in range(5)],
        max_retries=2,
    )
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.SERVER_ERROR
    # 1 initial attempt + 2 retries, then stop.
    assert len(recorded) == 3


def test_max_retries_never_applies_to_an_authentication_failure() -> None:
    """A bad credential must fail immediately however many retries are allowed."""
    provider, recorded = make_provider([_StubResponse(status_code=401, payload={})], max_retries=2)
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.AUTHENTICATION_FAILED
    assert len(recorded) == 1


def test_the_registry_passes_max_retries_through() -> None:
    """Configuration must actually reach the provider, not just be parsed."""
    settings = AgentSettings(
        mode="real",
        provider="openai_compatible",
        model=MODEL,
        api_key=SECRET,
        base_url="https://gateway.internal.example/v1",
        max_retries=2,
    )
    provider = get_provider(settings)
    assert provider._max_retries == 2  # noqa: SLF001 - wiring test


# ---------------------------------------------------------------------------
# Backoff schedule and Retry-After
#
# Retrying without waiting is the worst option for a rate limit: it spends
# another request from the same exhausted allowance and usually fails the same
# way. These tests pin the delay policy, not just the retry count.
# ---------------------------------------------------------------------------


def _recording_sleeper() -> tuple[list[float], object]:
    delays: list[float] = []
    return delays, (lambda seconds: delays.append(seconds))


def test_server_error_retries_use_exponential_backoff() -> None:
    delays, sleeper = _recording_sleeper()
    provider, recorded = make_provider(
        [_StubResponse(status_code=503, payload={}) for _ in range(3)],
        max_retries=2,
        retry_backoff_seconds=1.0,
        sleeper=sleeper,
    )
    with pytest.raises(LlmProviderError):
        provider.complete(request())
    assert len(recorded) == 3
    assert delays == [1.0, 2.0]


def test_backoff_is_capped() -> None:
    delays, sleeper = _recording_sleeper()
    provider, _ = make_provider(
        [_StubResponse(status_code=503, payload={}) for _ in range(3)],
        max_retries=2,
        retry_backoff_seconds=30.0,
        retry_max_backoff_seconds=5.0,
        sleeper=sleeper,
    )
    with pytest.raises(LlmProviderError):
        provider.complete(request())
    assert delays == [5.0, 5.0]


def test_retry_after_seconds_is_honoured() -> None:
    delays, sleeper = _recording_sleeper()
    provider, recorded = make_provider(
        [_StubResponse(status_code=429, payload={}, headers={"Retry-After": "3"}), ok()],
        max_retries=1,
        sleeper=sleeper,
    )
    completion = provider.complete(request())
    assert completion.raw_text == '{"side": "RIDER"}'
    assert delays == [3.0]
    assert len(recorded) == 2


def test_retry_after_is_capped_by_the_configured_ceiling() -> None:
    """A server must not be able to park the client for an hour."""
    delays, sleeper = _recording_sleeper()
    provider, _ = make_provider(
        [_StubResponse(status_code=429, payload={}, headers={"Retry-After": "3600"}), ok()],
        max_retries=1,
        retry_max_backoff_seconds=20.0,
        sleeper=sleeper,
    )
    provider.complete(request())
    assert delays == [20.0]


def test_retry_after_accepts_an_http_date() -> None:
    """RFC 9110 allows a date as well as a delay in seconds."""
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    future = datetime.now(timezone.utc) + timedelta(seconds=2)
    delays, sleeper = _recording_sleeper()
    provider, _ = make_provider(
        [
            _StubResponse(
                status_code=429,
                payload={},
                headers={"Retry-After": format_datetime(future, usegmt=True)},
            ),
            ok(),
        ],
        max_retries=1,
        sleeper=sleeper,
    )
    provider.complete(request())
    assert len(delays) == 1
    assert 0.0 <= delays[0] <= 3.0


def test_an_unparseable_retry_after_fails_safely() -> None:
    """An unusable header must not become a guessed delay."""
    delays, sleeper = _recording_sleeper()
    provider, recorded = make_provider(
        [_StubResponse(status_code=429, payload={}, headers={"Retry-After": "soon"}), ok()],
        max_retries=2,
        sleeper=sleeper,
    )
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.code == ProviderErrorCode.RATE_LIMITED
    assert delays == []
    assert len(recorded) == 1


def test_a_header_lookup_is_case_insensitive() -> None:
    """A plain dict stub must not hide a lower-cased retry-after header."""
    delays, sleeper = _recording_sleeper()
    provider, _ = make_provider(
        [_StubResponse(status_code=429, payload={}, headers={"retry-after": "2"}), ok()],
        max_retries=1,
        sleeper=sleeper,
    )
    provider.complete(request())
    assert delays == [2.0]


def test_attempt_count_is_recorded_on_success() -> None:
    provider, _ = make_provider(
        [_StubResponse(status_code=503, payload={}), ok()],
        max_retries=2,
        sleeper=lambda _s: None,
    )
    completion = provider.complete(request())
    assert completion.attempt_count == 2
    assert completion.retry_count == 1


def test_attempt_count_is_one_when_no_retry_was_needed() -> None:
    provider, _ = make_provider([ok()])
    completion = provider.complete(request())
    assert completion.attempt_count == 1
    assert completion.retry_count == 0


def test_attempt_count_is_recorded_on_failure() -> None:
    provider, _ = make_provider(
        [_StubResponse(status_code=503, payload={}) for _ in range(3)],
        max_retries=2,
        sleeper=lambda _s: None,
    )
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request())
    assert caught.value.attempts == 3


# ---------------------------------------------------------------------------
# Optional provider-specific request fields
#
# Added for the Gemini switch. Gemini 3.x Flash is a thinking model, so the
# operator may want to ask for lighter hidden reasoning. That is a gateway-only
# knob, so it travels through `extra_body` rather than becoming a first-class
# concept on the generic LlmProvider contract.
#
# Two properties matter and are both tested here:
#   * the default request body is byte-for-byte unchanged when the knob is unset
#   * a gateway knob can never redefine the fields the agent owns
#
# The second is the security-relevant one: if `extra_body` could set `model` or
# `messages`, then a configuration value could silently change which model
# answered, or what it was asked, while every log still showed the configured
# values.
# ---------------------------------------------------------------------------

_RESERVED = ("model", "messages", "temperature", "response_format", "max_tokens", "stream")


def test_extra_body_fields_reach_the_request_payload() -> None:
    provider, recorded = make_provider([ok()])
    provider.complete(request(extra_body={"reasoning_effort": "low"}))
    assert recorded[0]["json"]["reasoning_effort"] == "low"


def test_extra_body_is_omitted_entirely_when_empty() -> None:
    """The default body must not grow a stray field just because the seam exists."""
    provider, recorded = make_provider([ok()])
    provider.complete(request())
    assert "reasoning_effort" not in recorded[0]["json"]


@pytest.mark.parametrize("reserved", _RESERVED)
def test_extra_body_cannot_override_a_reserved_field(reserved: str) -> None:
    provider, _ = make_provider([ok()])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request(extra_body={reserved: "hijacked"}))
    assert caught.value.code == ProviderErrorCode.REQUEST_REJECTED
    assert reserved in str(caught.value)


def test_a_reserved_field_override_is_rejected_before_anything_is_sent() -> None:
    """Fail closed: the request must not leave the process at all."""
    provider, recorded = make_provider([ok()])
    with pytest.raises(LlmProviderError):
        provider.complete(request(extra_body={"model": "someone-elses-model"}))
    assert recorded == []


def test_a_reserved_field_rejection_never_echoes_the_credential() -> None:
    provider, _ = make_provider([ok()])
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(request(extra_body={"messages": "hijacked"}))
    assert SECRET not in str(caught.value)


def test_a_gateway_knob_cannot_change_the_configured_model() -> None:
    """The payload's model is always the configured one, never the extra."""
    provider, recorded = make_provider([ok()])
    provider.complete(request(extra_body={"reasoning_effort": "low"}))
    assert recorded[0]["json"]["model"] == MODEL


def test_default_agent_request_sends_no_reasoning_effort() -> None:
    """Unset must mean 'do not ask', not 'ask for the default'."""
    context = context_for(clean_case("route_deviation"))[0]
    provider, recorded = make_provider([ok(advocate_json(context, "RIDER"))])
    RiderAdvocateAgent(provider, AgentSettings(mode="mock")).argue(context)
    assert "reasoning_effort" not in recorded[0]["json"]


def test_a_configured_reasoning_effort_is_sent_by_the_agent() -> None:
    context = context_for(clean_case("route_deviation"))[0]
    settings = AgentSettings(mode="mock", reasoning_effort="low")
    provider, recorded = make_provider([ok(advocate_json(context, "RIDER"))])
    RiderAdvocateAgent(provider, settings).argue(context)
    assert recorded[0]["json"]["reasoning_effort"] == "low"


def test_reasoning_effort_is_configurable_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "low")
    assert AgentSettings.from_environment().reasoning_effort == "low"


def test_reasoning_effort_is_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "")
    assert AgentSettings.from_environment().reasoning_effort is None


def test_reasoning_effort_is_not_part_of_the_completion_contract() -> None:
    """A gateway knob must not leak into what the provider reports back.

    `LlmCompletion` is the generic result every provider shares; a Gemini-only
    request hint has no business appearing there.
    """
    provider, _ = make_provider([ok()])
    completion = provider.complete(request(extra_body={"reasoning_effort": "low"}))
    assert not hasattr(completion, "reasoning_effort")
