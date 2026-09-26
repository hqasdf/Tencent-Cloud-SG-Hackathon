"""Tests for the mock provider and the provider abstraction.

Mock mode is the default for Milestone 4, so its determinism is a product
property, not a test convenience. Two things matter most:

  * the mock provider returns the side it was ASSIGNED, never guessed
  * no credentials, no network, fully deterministic across runs
"""

import json

import pytest

from app.agents.config import AgentConfigurationError, AgentSettings
from app.agents.provider import AgentCompletionRequest, LlmProviderError
from app.agents.providers.mock import MockLlmProvider
from app.agents.providers.openai_compatible import parse_json_object
from app.agents.providers.registry import get_provider
from app.agents.context_builder import AdvocateContextBuilder
from app.data.cases import MOCK_CASES
from app.services.dispute_analysis import DisputeAnalysisService

PROVIDER = MockLlmProvider()


def context_json_for(case_id: str) -> str:
    case = next(item for item in MOCK_CASES if item.id == case_id)
    analysis = DisputeAnalysisService().analyze(case)
    context = AdvocateContextBuilder().build(case, analysis)
    return context.model_dump_json(by_alias=True)


def request_for(case_id: str, side: str) -> AgentCompletionRequest:
    return AgentCompletionRequest(
        system_prompt=f"You are the {side.lower()} advocate.",
        user_prompt="Argue the case.",
        response_schema={},
        temperature=0.0,
        max_tokens=1024,
        metadata={"context_json": context_json_for(case_id), "side": side},
    )


def test_mock_provider_reports_its_name() -> None:
    assert PROVIDER.name == "mock"


def test_mock_provider_returns_the_assigned_side_not_a_guessed_one() -> None:
    """Side must come from metadata.

    An earlier revision guessed the side from prompt prose and returned RIDER for
    the driver because the shared rules text contains the word "advocate". This
    test locks in the explicit-metadata contract.
    """
    rider = json.loads(PROVIDER.complete(request_for("CASE-2026-1041", "RIDER")).raw_text)
    driver = json.loads(PROVIDER.complete(request_for("CASE-2026-1041", "DRIVER")).raw_text)
    assert rider["side"] == "RIDER"
    assert driver["side"] == "DRIVER"


def test_mock_provider_is_deterministic_across_calls() -> None:
    first = PROVIDER.complete(request_for("CASE-2026-1041", "RIDER")).raw_text
    second = PROVIDER.complete(request_for("CASE-2026-1041", "RIDER")).raw_text
    assert first == second


def test_mock_provider_uses_only_trusted_context_values() -> None:
    """Every number the mock cites must come from the trusted context."""
    payload = json.loads(PROVIDER.complete(request_for("CASE-2026-1041", "RIDER")).raw_text)
    asserted = {
        item["fact"]: item["value"]
        for claim in payload["claims"]
        for item in claim["assertedFacts"]
    }
    assert asserted["DEVIATION_PERCENTAGE"] == 22.41
    assert asserted["UNEXPLAINED_DEVIATION_KM"] == 0.8
    assert asserted["EXPLAINED_DEVIATION_KM"] == 0.5


def test_mock_provider_acknowledges_adverse_facts() -> None:
    """Both advocates must surface facts that weaken their own position."""
    rider = json.loads(PROVIDER.complete(request_for("CASE-2026-1041", "RIDER")).raw_text)
    driver = json.loads(PROVIDER.complete(request_for("CASE-2026-1041", "DRIVER")).raw_text)

    # The rider admits explained deviation weakens them.
    assert any("EXPLAINED_DEVIATION_KM" in json.dumps(claim) for claim in rider["claims"])
    # The driver admits an unexplained remainder exists.
    assert any("UNEXPLAINED_DEVIATION_KM" in json.dumps(claim) for claim in driver["claims"])


def test_mock_provider_reports_different_outcomes_per_side_on_a_contestable_case() -> None:
    rider = json.loads(PROVIDER.complete(request_for("CASE-2026-1041", "RIDER")).raw_text)
    driver = json.loads(PROVIDER.complete(request_for("CASE-2026-1041", "DRIVER")).raw_text)
    assert rider["requestedOutcome"] == "PARTIAL_REFUND"
    assert driver["requestedOutcome"] == "NO_REFUND"


def test_mock_provider_requires_context_metadata() -> None:
    with pytest.raises(ValueError):
        PROVIDER.complete(
            AgentCompletionRequest(
                system_prompt="s",
                user_prompt="u",
                response_schema={},
                temperature=0.0,
                max_tokens=16,
                metadata={},
            )
        )


def test_mock_provider_emits_nothing_but_the_contract_fields() -> None:
    payload = json.loads(PROVIDER.complete(request_for("CASE-2026-1043", "DRIVER")).raw_text)
    assert set(payload.keys()) == {
        "side",
        "summary",
        "claims",
        "requestedOutcome",
        "contextAcknowledged",
    }
    # No answer fields leak into agent output.
    serialized = json.dumps(payload)
    assert "confidence" not in serialized
    assert "resolutionMode" not in serialized
    assert "escalationReasons" not in serialized


# ---------------------------------------------------------------------------
# Settings and registry
# ---------------------------------------------------------------------------

def test_default_mode_is_mock(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_MODE", raising=False)
    settings = AgentSettings.from_environment()
    assert settings.mode == "mock"
    assert settings.provider == "mock"


def test_registry_returns_the_mock_provider_in_mock_mode(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_MODE", raising=False)
    provider = get_provider(AgentSettings.from_environment())
    assert provider.name == "mock"


def test_real_mode_without_credentials_fails_loudly(monkeypatch) -> None:
    """Real mode must never silently fall back to mock."""
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "openai_compatible")
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)

    settings = AgentSettings.from_environment()
    with pytest.raises(AgentConfigurationError):
        settings.validate()
    with pytest.raises(AgentConfigurationError):
        get_provider(settings)


def test_unknown_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "not_a_real_provider")
    monkeypatch.setenv("AGENT_API_KEY", "x")
    monkeypatch.setenv("AGENT_MODEL", "m")
    settings = AgentSettings.from_environment()
    with pytest.raises(AgentConfigurationError):
        get_provider(settings)


def test_settings_description_never_includes_the_api_key(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AGENT_API_KEY", "super-secret-value")
    monkeypatch.setenv("AGENT_MODEL", "some-model")
    described = AgentSettings.from_environment().describe()
    assert "super-secret-value" not in json.dumps(described)
    assert "api_key" not in json.dumps(described).lower()


def test_mock_mode_ignores_credentials_entirely(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_API_KEY", "unused")
    settings = AgentSettings.from_environment()
    settings.validate()  # must not raise
    assert settings.describe()["mode"] == "mock"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_parse_json_object_handles_a_bare_object() -> None:
    assert parse_json_object('{"side": "RIDER"}') == {"side": "RIDER"}


def test_parse_json_object_strips_markdown_fences() -> None:
    fenced = '```json\n{"side": "DRIVER"}\n```'
    assert parse_json_object(fenced) == {"side": "DRIVER"}


def test_parse_json_object_raises_value_error_on_garbage() -> None:
    """The parser itself raises ValueError; normalisation happens at the provider.

    ``parse_json_object`` is a low-level helper, so it reports a plain ValueError.
    ``OpenAiCompatibleProvider.complete`` is the boundary that converts every
    transport/auth/timeout/malformed failure into a single ``LlmProviderError``,
    so callers only ever handle one exception type. Keeping the split means the
    helper stays trivially testable.
    """
    with pytest.raises(ValueError):
        parse_json_object("I am afraid I cannot help with that.")


# ---------------------------------------------------------------------------
# Real provider: configuration and failure normalisation (no network required)
# ---------------------------------------------------------------------------

def test_real_provider_requires_all_credentials() -> None:
    from app.agents.providers.openai_compatible import OpenAiCompatibleProvider

    for kwargs in (
        {"base_url": "", "model": "m", "api_key": "k"},
        {"base_url": "u", "model": "", "api_key": "k"},
        {"base_url": "u", "model": "m", "api_key": ""},
    ):
        with pytest.raises(LlmProviderError):
            OpenAiCompatibleProvider(**kwargs)


def test_real_provider_normalises_transport_failure_to_provider_error() -> None:
    """A dead endpoint must surface as one normalised error, not a raw httpx error."""
    from app.agents.providers.openai_compatible import OpenAiCompatibleProvider

    provider = OpenAiCompatibleProvider(
        base_url="http://127.0.0.1:9",  # discard port: refuses instantly
        model="some-model",
        api_key="not-a-real-key",
        timeout_seconds=1.0,
        max_retries=0,
    )
    with pytest.raises(LlmProviderError) as caught:
        provider.complete(
            AgentCompletionRequest(
                system_prompt="s",
                user_prompt="u",
                response_schema={},
                temperature=0.0,
                max_tokens=16,
                metadata={},
            )
        )
    # The error must not leak the API key.
    assert "not-a-real-key" not in str(caught.value)
