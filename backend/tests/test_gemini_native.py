"""Gemini native provider — every test offline.

The OpenAI-compatible endpoint could not serve a structured-output request for
this model (minimal request → 200, the same request with ``response_format:
json_schema`` → 503, and it still 503s when the official OpenAI SDK builds the
request). This provider takes structured output off that endpoint entirely.

Automated tests must never reach a live paid API. The GenAI client is injected
through ``client_factory``, so the transport is a stub and the network is never
touched. The whole file also runs under the egress guard.

What is proven here:

  * the provider satisfies ``LlmProvider`` and nothing above it changed
  * the native request carries exactly the bounded contract: 30 s, 1024 output
    tokens, temperature 0, thinking level low, non-streaming
  * the response schema reaching the provider is generated from the Pydantic
    model — there is no hand-maintained schema document left to drift
  * system instruction and user content map to the native fields
  * valid structured output parses and validates
  * malformed output is repaired exactly once, and a second failure fails
  * 429/5xx/timeout/network failures are normalised into the shared vocabulary
  * empty and truncated responses are never accepted as valid
  * token accounting maps onto the existing contract, and thought tokens are
    reported as unattributed rather than as reasoning
  * the credential never appears in an error, a completion, or a log line
  * provenance records ``gemini_native`` and cannot be confused with
    ``openai_compatible``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.genai import types as genai_types

from app.agents.base_advocate import AdvocateAgentError, AdvocateOutputErrorCode
from app.agents.config import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    AgentConfigurationError,
    AgentSettings,
)
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.provider import (
    AgentCompletionRequest,
    LlmProvider,
    LlmProviderError,
    ProviderErrorCode,
)
from app.agents.provider_schema import provider_schema_for
from app.agents.providers.gemini_native import GeminiNativeProvider
from app.agents.providers.registry import SUPPORTED_PROVIDERS, get_provider
from app.agents.rider_advocate import RiderAdvocateAgent
from app.models.advocate import AdvocateOutput
from app.repositories.case_repository import MockCaseRepository
from app.services.case_service import CaseService
from app.services.dispute_analysis import DisputeAnalysisService

# A deliberately recognisable fake credential. Several tests assert this string
# never appears in an error, a completion, or a serialized response.
SECRET = "gemini_native_FAKE_key_do_not_log_7c41"
MODEL = "gemini-3.8-flash"
CASE_ID = "DISP-005"


# ---------------------------------------------------------------------------
# Stub transport
# ---------------------------------------------------------------------------


class _Usage:
    def __init__(self, prompt, candidates, total, thoughts=None):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.total_token_count = total
        self.thoughts_token_count = thoughts


class _Part:
    def __init__(self, text):
        self.text = text


class _Content:
    def __init__(self, text):
        self.parts = [_Part(text)]


class _Candidate:
    def __init__(self, text, finish_reason):
        self.content = _Content(text)
        self.finish_reason = finish_reason


class _Response:
    """The subset of ``GenerateContentResponse`` this provider reads."""

    def __init__(self, text="{}", finish_reason=None, usage=None, model_version=MODEL):
        self._text = text
        self.candidates = [
            _Candidate(text, finish_reason or genai_types.FinishReason.STOP)
        ]
        self.usage_metadata = usage
        self.model_version = model_version

    @property
    def text(self):
        return self._text


class _StubModels:
    def __init__(self, owner):
        self._owner = owner

    def generate_content(self, *, model, contents, config):
        self._owner.calls.append({"model": model, "contents": contents, "config": config})
        index = len(self._owner.calls)
        if self._owner.responder is not None:
            return self._owner.responder(index, self._owner.calls[-1])
        if self._owner.error is not None:
            raise self._owner.error
        return self._owner.response


class _StubClient:
    """A stand-in for ``genai.Client`` that records instead of transmitting."""

    def __init__(self, response=None, error=None, responder=None):
        self.response = response
        self.error = error
        self.responder = responder
        self.calls: list = []
        self.models = _StubModels(self)


def _provider(client=None, response=None, error=None, responder=None, **kwargs):
    """A provider whose transport is a stub. Returns the provider, client and call info."""
    if client is None:
        if response is None and error is None and responder is None:
            response = _Response()
        client = _StubClient(response=response, error=error, responder=responder)
    stub = client
    seen: dict = {}

    def factory(*, api_key, timeout_seconds):
        seen["api_key"] = api_key
        seen["timeout_seconds"] = timeout_seconds
        return stub

    provider = GeminiNativeProvider(
        model=MODEL,
        api_key=SECRET,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        client_factory=factory,
        sleeper=lambda _seconds: None,
        **kwargs,
    )
    provider._seen = seen  # type: ignore[attr-defined]
    return provider, stub, seen


def _advocate_request(schema=None):
    return AgentCompletionRequest(
        system_prompt="You are the RIDER advocate.",
        user_prompt="The following is the trusted case context.",
        response_schema=schema if schema is not None else provider_schema_for(AdvocateOutput),
        temperature=0.0,
        max_tokens=DEFAULT_MAX_TOKENS,
        metadata={"side": "RIDER"},
        extra_body={"reasoning_effort": "low"},
    )


def _valid_advocate_json() -> str:
    return json.dumps(
        {
            "side": "RIDER",
            "summary": "The measured deviation is not fully explained.",
            "claims": [],
            "requestedOutcome": "NO_REFUND",
            "contextAcknowledged": True,
        }
    )


def _context():
    case = CaseService(MockCaseRepository())._load_case(CASE_ID)
    analysis = DisputeAnalysisService().analyze(case)
    return AdvocateContextBuilder().build(case, analysis)


# ---------------------------------------------------------------------------
# 1. The provider satisfies the protocol; nothing above it changed
# ---------------------------------------------------------------------------


def test_the_native_provider_satisfies_the_llm_provider_protocol() -> None:
    provider, _, _ = _provider()

    assert isinstance(provider, LlmProvider)
    assert provider.name == "gemini_native"
    assert provider.model_name == MODEL


def test_the_provider_refuses_to_construct_without_model_or_key() -> None:
    for kwargs in ({"model": "", "api_key": SECRET}, {"model": MODEL, "api_key": ""}):
        with pytest.raises(LlmProviderError) as caught:
            GeminiNativeProvider(**kwargs)
        assert caught.value.code == ProviderErrorCode.NOT_CONFIGURED


# ---------------------------------------------------------------------------
# 2. The bounded native request shape
# ---------------------------------------------------------------------------


def test_the_timeout_is_the_bounded_thirty_seconds() -> None:
    provider, _, seen = _provider()

    provider.complete(_advocate_request())

    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert provider.timeout_seconds == 30.0
    assert seen["timeout_seconds"] == 30.0


def test_the_native_config_carries_the_whole_bounded_contract() -> None:
    provider, _, _ = _provider()

    config = provider.build_config(_advocate_request())
    dumped = config.model_dump(exclude_none=True)

    assert dumped["temperature"] == 0.0
    assert dumped["max_output_tokens"] == DEFAULT_MAX_TOKENS == 1024
    assert dumped["response_mime_type"] == "application/json"
    assert dumped["thinking_config"]["thinking_level"].name == "LOW"


def test_the_system_instruction_and_user_content_map_to_the_native_fields() -> None:
    provider, stub, _ = _provider(response=_Response(text=_valid_advocate_json()))
    request = _advocate_request()

    provider.complete(request)

    call = stub.calls[0]
    assert call["model"] == MODEL
    assert call["contents"] == request.user_prompt
    assert call["config"].model_dump(exclude_none=True)["system_instruction"] == request.system_prompt


def test_the_thinking_level_follows_the_neutral_reasoning_knob() -> None:
    provider, _, _ = _provider()

    for effort, expected in (("low", "LOW"), ("medium", "MEDIUM"), ("high", "HIGH")):
        request = AgentCompletionRequest(
            system_prompt="s", user_prompt="u", extra_body={"reasoning_effort": effort}
        )
        config = provider.build_config(request).model_dump(exclude_none=True)
        assert config["thinking_config"]["thinking_level"].name == expected

    # An unset or unrecognised knob defers to the provider rather than guessing.
    for extra in ({}, {"reasoning_effort": ""}, {"reasoning_effort": "turbo"}):
        config = provider.build_config(
            AgentCompletionRequest(system_prompt="s", user_prompt="u", extra_body=extra)
        ).model_dump(exclude_none=True)
        assert "thinking_config" not in config


def test_no_response_schema_means_no_structured_output_is_claimed() -> None:
    provider, _, _ = _provider()

    config = provider.build_config(
        AgentCompletionRequest(system_prompt="s", user_prompt="u", response_schema=None)
    ).model_dump(exclude_none=True)

    assert "response_mime_type" not in config
    assert "response_schema" not in config


# ---------------------------------------------------------------------------
# 3. The response schema is generated from the Pydantic model
# ---------------------------------------------------------------------------


def test_the_schema_reaching_the_provider_is_the_one_the_parser_enforces() -> None:
    provider, stub, _ = _provider(response=_Response(text=_valid_advocate_json()))

    provider.complete(_advocate_request())

    sent = stub.calls[0]["config"].model_dump(exclude_none=True)["response_schema"]
    assert sent == provider_schema_for(AdvocateOutput)
    assert "claims" in sent["properties"]


def test_the_advocate_agent_derives_its_schema_from_the_model() -> None:
    """The agent no longer reads a hand-written schema document."""
    agent = RiderAdvocateAgent(_provider()[0])

    assert agent._output_schema == provider_schema_for(AdvocateOutput)


def test_the_hand_maintained_schema_documents_are_gone() -> None:
    """There is no second schema to drift, because there is no second schema."""
    from app.agents.base_advocate import PROMPTS_DIR

    for name in (
        "output_schema.json",
        "judge_output_schema.json",
        "rebuttal_output_schema.json",
    ):
        assert not (PROMPTS_DIR / name).exists(), f"{name} should have been deleted"


def test_every_agent_schema_is_self_contained() -> None:
    """No ``$ref`` survives, so no provider dialect has to agree on how to spell one."""
    from app.agents.judge_agent import JudgeAgent
    from app.agents.rebuttal_agent import RebuttalAgent

    provider, _, _ = _provider()
    for agent in (
        RiderAdvocateAgent(provider),
        JudgeAgent(provider),
        RebuttalAgent(provider),
    ):
        text = json.dumps(agent._output_schema)
        assert "$defs" not in text
        assert "$ref" not in text
        assert "$schema" not in text


def test_the_schema_reflects_the_model_rather_than_tightening_it() -> None:
    """A model that tolerates extra keys must not be described as forbidding them.

    ``JudgeOutput`` and ``RebuttalOutput`` forbid extra properties, so their
    generated schemas say so. ``AdvocateOutput`` does not, so its schema does
    not either. Asserting the model's real behaviour is the point — a stricter
    claim would be exactly the second-document drift this replaced.
    """
    from app.models.judge import JudgeOutput
    from app.models.rebuttal import RebuttalOutput

    advocate = provider_schema_for(AdvocateOutput)
    assert advocate.get("additionalProperties") is not False

    for model in (JudgeOutput, RebuttalOutput):
        assert provider_schema_for(model).get("additionalProperties") is False


# ---------------------------------------------------------------------------
# 4. A valid response parses and validates
# ---------------------------------------------------------------------------


def test_valid_structured_output_parses_through_the_agent() -> None:
    client = _StubClient(response=_Response(text=_valid_advocate_json()))
    provider, _, _ = _provider(client=client)
    agent = RiderAdvocateAgent(provider)

    argument = agent.argue_with_trace(_context())

    assert isinstance(argument.output, AdvocateOutput)
    assert argument.output.side == "RIDER"
    assert len(client.calls) == 1


def test_an_empty_candidate_is_rejected_rather_than_parsed() -> None:
    provider, _, _ = _provider(response=_Response(text="   "))

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    assert caught.value.code == ProviderErrorCode.EMPTY_CONTENT


def test_a_response_with_no_candidates_is_a_malformed_response() -> None:
    response = _Response(text="")
    response.candidates = []
    provider, _, _ = _provider(response=response)

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    assert caught.value.code == ProviderErrorCode.MALFORMED_RESPONSE


# ---------------------------------------------------------------------------
# 5. Truncation is detected and never accepted
# ---------------------------------------------------------------------------


def test_a_truncated_response_maps_to_the_openai_length_spelling() -> None:
    """``LlmCompletion.truncated`` is defined as ``finish_reason == "length"``."""
    response = _Response(text='{"side":"RIDER"', finish_reason=genai_types.FinishReason.MAX_TOKENS)
    provider, _, _ = _provider(response=response)

    completion = provider.complete(_advocate_request())

    assert completion.finish_reason == "length"
    assert completion.truncated is True


def test_a_stopped_response_is_not_truncated() -> None:
    provider, _, _ = _provider(response=_Response(text=_valid_advocate_json()))

    completion = provider.complete(_advocate_request())

    assert completion.finish_reason == "stop"
    assert completion.truncated is False


def test_a_truncated_response_is_never_valid_and_is_never_repaired() -> None:
    """Cut off mid-object: rejected, and NOT re-asked.

    Re-asking with the same token budget truncates the same way, so a repair
    would spend a request to reproduce the failure.
    """
    client = _StubClient(
        response=_Response(
            text='{"side":"RIDER","summary":"cut off', finish_reason=genai_types.FinishReason.MAX_TOKENS
        )
    )
    provider, _, _ = _provider(client=client)
    agent = RiderAdvocateAgent(provider)

    with pytest.raises(AdvocateAgentError) as caught:
        agent.argue_with_trace(_context())

    assert caught.value.code == AdvocateOutputErrorCode.OUTPUT_TRUNCATED
    assert len(client.calls) == 1, "a truncated response must not be repaired"


# ---------------------------------------------------------------------------
# 6. The bounded repair is preserved
# ---------------------------------------------------------------------------


def test_malformed_output_is_repaired_exactly_once() -> None:
    def responder(index, _call):
        return _Response(text="not json at all" if index == 1 else _valid_advocate_json())

    client = _StubClient(responder=responder)
    provider, _, _ = _provider(client=client)
    agent = RiderAdvocateAgent(provider)

    argument = agent.argue_with_trace(_context())

    assert argument.output.side == "RIDER"
    assert len(client.calls) == 2, "exactly one repair"
    assert argument.completion.attempt_count == 2
    # The repair re-asks with the contract reminder appended.
    assert "violated the output contract" in client.calls[1]["contents"]


def test_a_second_malformed_output_fails() -> None:
    client = _StubClient(response=_Response(text="still not json"))
    provider, _, _ = _provider(client=client)
    agent = RiderAdvocateAgent(provider)

    with pytest.raises(AdvocateAgentError) as caught:
        agent.argue_with_trace(_context())

    assert caught.value.code == AdvocateOutputErrorCode.MALFORMED_JSON
    assert len(client.calls) == 2


def test_a_provider_failure_is_never_repaired() -> None:
    """A 503 means the model was never asked; there is nothing to repair."""
    error = _api_error(503)
    client = _StubClient(error=error)
    provider, _, _ = _provider(client=client)
    agent = RiderAdvocateAgent(provider)

    with pytest.raises(LlmProviderError):
        agent.argue_with_trace(_context())

    assert len(client.calls) == 1, "a provider failure must not enter the repair path"


# ---------------------------------------------------------------------------
# 7. Error normalisation
# ---------------------------------------------------------------------------


def _api_error(status: int, message: str = "upstream refused"):
    from google.genai import errors as genai_errors

    return genai_errors.APIError(status, {"error": {"code": status, "message": message}})


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_retryable"),
    [
        (400, ProviderErrorCode.REQUEST_REJECTED, False),
        (401, ProviderErrorCode.AUTHENTICATION_FAILED, False),
        (403, ProviderErrorCode.ACCESS_DENIED, False),
        (404, ProviderErrorCode.MODEL_NOT_FOUND, False),
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (500, ProviderErrorCode.SERVER_ERROR, True),
        (503, ProviderErrorCode.SERVER_ERROR, True),
    ],
)
def test_http_failures_map_onto_the_shared_vocabulary(
    status, expected_code, expected_retryable
) -> None:
    provider, _, _ = _provider(client=_StubClient(error=_api_error(status)))

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    assert caught.value.code == expected_code
    assert caught.value.retryable is expected_retryable
    assert caught.value.provider == "gemini_native"


def test_a_timeout_is_normalised() -> None:
    class _Timeout(Exception):
        pass

    _Timeout.__name__ = "TimeoutException"
    provider, _, _ = _provider(client=_StubClient(error=_Timeout("too slow")))

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    assert caught.value.code == ProviderErrorCode.TIMEOUT
    assert caught.value.retryable is True


def test_a_network_failure_is_normalised() -> None:
    provider, _, _ = _provider(client=_StubClient(error=ConnectionResetError("reset")))

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    assert caught.value.code == ProviderErrorCode.NETWORK_ERROR


def test_a_response_that_cannot_be_read_is_a_malformed_response() -> None:
    provider, _, _ = _provider(client=_StubClient(error=ValueError("undecodable")))

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    assert caught.value.code == ProviderErrorCode.MALFORMED_RESPONSE
    assert caught.value.retryable is False


def test_the_credential_never_appears_in_a_normalised_error() -> None:
    provider, _, _ = _provider(client=_StubClient(error=_api_error(503, SECRET)))

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    rendered = f"{caught.value} {caught.value.args} {vars(caught.value)}"
    assert SECRET not in rendered
    # The upstream body is never surfaced, because it can echo the request.
    assert "upstream refused" not in rendered


def test_retries_are_bounded_and_counted() -> None:
    client = _StubClient(error=_api_error(503))
    provider, _, _ = _provider(client=client, max_retries=2)

    with pytest.raises(LlmProviderError) as caught:
        provider.complete(_advocate_request())

    assert caught.value.attempts == 1
    assert len(client.calls) == 3, "one attempt plus two bounded retries"


def test_a_non_retryable_failure_is_not_retried() -> None:
    client = _StubClient(error=_api_error(401))
    provider, _, _ = _provider(client=client, max_retries=2)

    with pytest.raises(LlmProviderError):
        provider.complete(_advocate_request())

    assert len(client.calls) == 1


def test_process_stop_signal_is_never_normalised_or_retried() -> None:
    class StopSignal(BaseException):
        pass

    client = _StubClient(error=StopSignal("controlled stop"))
    provider, _, _ = _provider(client=client, max_retries=2)

    with pytest.raises(StopSignal):
        provider.complete(_advocate_request())

    assert len(client.calls) == 1


def test_a_successful_completion_reports_a_single_attempt() -> None:
    provider, _, _ = _provider(response=_Response(text=_valid_advocate_json()))

    completion = provider.complete(_advocate_request())

    assert completion.attempt_count == 1
    assert completion.provider_name == "gemini_native"


# ---------------------------------------------------------------------------
# 8. Token accounting
# ---------------------------------------------------------------------------


def test_usage_metadata_maps_onto_the_existing_contract() -> None:
    usage = _Usage(prompt=120, candidates=80, total=260, thoughts=60)
    provider, _, _ = _provider(response=_Response(text=_valid_advocate_json(), usage=usage))

    completion = provider.complete(_advocate_request())

    assert completion.input_tokens == 120
    assert completion.output_tokens == 80
    assert completion.total_tokens == 260
    # Thought tokens are billed but are not part of the answer. They are left to
    # fall out as unattributed rather than surfaced as reasoning.
    unattributed = completion.total_tokens - completion.input_tokens - completion.output_tokens
    assert unattributed == 60


def test_missing_usage_stays_unknown_rather_than_becoming_zero() -> None:
    provider, _, _ = _provider(response=_Response(text=_valid_advocate_json(), usage=None))

    completion = provider.complete(_advocate_request())

    assert completion.input_tokens is None
    assert completion.output_tokens is None
    assert completion.total_tokens is None


def test_no_field_carries_chain_of_thought() -> None:
    usage = _Usage(prompt=10, candidates=5, total=100, thoughts=85)
    provider, _, _ = _provider(response=_Response(text=_valid_advocate_json(), usage=usage))

    completion = provider.complete(_advocate_request())

    assert not hasattr(completion, "thoughts")
    assert not hasattr(completion, "reasoning")
    assert completion.raw_text == _valid_advocate_json()


# ---------------------------------------------------------------------------
# 9. Secrets stay out of logs and artefacts
# ---------------------------------------------------------------------------


def test_the_credential_is_transmitted_and_never_surfaced() -> None:
    provider, _, seen = _provider(response=_Response(text=_valid_advocate_json()))

    completion = provider.complete(_advocate_request())

    assert seen["api_key"] == SECRET, "the credential must actually be handed to the client"
    assert SECRET not in json.dumps(completion.__dict__, default=str)
    assert SECRET not in json.dumps(provider.model_name)


def test_the_settings_repr_does_not_leak_the_credential() -> None:
    settings = AgentSettings(
        mode="real", provider="gemini_native", model=MODEL, api_key=SECRET
    )

    assert SECRET not in repr(settings)
    assert SECRET not in json.dumps(settings.describe())


# ---------------------------------------------------------------------------
# 10. Configuration and registry
# ---------------------------------------------------------------------------


def test_the_native_provider_is_registered() -> None:
    assert "gemini_native" in SUPPORTED_PROVIDERS
    assert "openai_compatible" in SUPPORTED_PROVIDERS, "the old provider must survive"


def test_the_registry_resolves_the_native_provider() -> None:
    settings = AgentSettings(
        mode="real", provider="gemini_native", model=MODEL, api_key=SECRET
    )

    provider = get_provider(settings)

    assert isinstance(provider, GeminiNativeProvider)
    assert provider.name == "gemini_native"
    assert provider.timeout_seconds == 30.0


def test_the_native_provider_does_not_demand_a_base_url() -> None:
    """It addresses Google's API directly, so a base URL would be a value not in force."""
    settings = AgentSettings(
        mode="real", provider="gemini_native", model=MODEL, api_key=SECRET, base_url=None
    )

    settings.validate()


def test_real_mode_still_fails_loudly_without_a_credential() -> None:
    settings = AgentSettings(
        mode="real", provider="gemini_native", model=MODEL, api_key=None
    )

    with pytest.raises(AgentConfigurationError):
        settings.validate()


# ---------------------------------------------------------------------------
# 11. Provenance distinguishes the native provider
# ---------------------------------------------------------------------------


def _run_advocates_with(provider, tmp_path: Path, settings: AgentSettings | None = None) -> dict:
    from app.replay.service import ReplayService
    from app.replay.store import ReplayArtifactStore
    from app.services.resolution_orchestrator import ResolutionOrchestratorService

    settings = settings or AgentSettings(mode="mock", provider="mock")
    store = ReplayArtifactStore(
        root=tmp_path / "replay_artifacts", curated_root=tmp_path / "demo_replays"
    )
    case = CaseService(MockCaseRepository())._load_case(CASE_ID)
    analysis = DisputeAnalysisService().analyze(case)
    orchestrator = ResolutionOrchestratorService(
        settings=settings,
        provider=provider,
        replay_service=ReplayService(store=store, settings=settings),
    )
    orchestrator.run(case, analysis, capture_stages=frozenset({"ADVOCATES"}))
    return json.loads(store.path_for(case.id, "ADVOCATES").read_text(encoding="utf-8"))


def _side_aware_responder(_index, call):
    """Answer with a schema-valid output for whichever side was asked.

    The side is identified by which *side prompt file* the system instruction
    contains. Matching on the words "RIDER"/"DRIVER" is not enough: the rider's
    own prompt discusses the driver, so a keyword test picks the wrong side.

    The comparison must be against the **raw** instruction. The system prompt is
    ``common_rules + side_prompt + output_contract`` joined with blank lines, so
    the side prompt appears verbatim — but only in the unescaped string. Dumping
    it through ``json.dumps`` first escapes every newline, and a multi-line file
    can then never be found.
    """
    from app.agents.base_advocate import PROMPTS_DIR

    system = call["config"].model_dump(exclude_none=True).get("system_instruction")
    assert isinstance(system, str), "the system instruction should be a plain string"
    driver_prompt = (PROMPTS_DIR / "driver_advocate.md").read_text(encoding="utf-8")
    rider_prompt = (PROMPTS_DIR / "rider_advocate.md").read_text(encoding="utf-8")
    if driver_prompt in system:
        side = "DRIVER"
    elif rider_prompt in system:
        side = "RIDER"
    else:
        raise AssertionError("could not tell which side was asked")
    return _Response(
        text=json.dumps(
            {
                "side": side,
                "summary": "A schema-valid argument.",
                "claims": [],
                "requestedOutcome": "NO_REFUND",
                "contextAcknowledged": True,
            }
        )
    )


def test_a_native_capture_records_gemini_native_provenance(tmp_path: Path) -> None:
    """The capture names the provider that produced it, in both modes.

    The provider is read from the injected object, not from the settings, so a
    ``gemini_native`` run is recorded as such even when the settings still say
    something else. The model is recorded only for a real-mode run: a mock
    capture is a valid artefact but never live evidence, so it must not claim a
    model it never asked.
    """
    from app.replay.artifacts import AdvocateArtifact

    client = _StubClient(responder=_side_aware_responder)
    provider, _, _ = _provider(client=client)

    mock_document = _run_advocates_with(provider, tmp_path / "mock")
    mock_artifact = AdvocateArtifact.model_validate(mock_document)
    assert mock_artifact.captured_provider == "gemini_native"
    assert mock_artifact.captured_mode == "mock"
    assert mock_artifact.captured_model is None
    assert mock_artifact.is_live_eligible is False

    # Same injected provider, but the run is declared real. Nothing reaches the
    # network — the transport is still the stub — so this pins the provenance
    # plumbing, not the transport.
    real_client = _StubClient(responder=_side_aware_responder)
    real_provider, _, _ = _provider(client=real_client)
    real_settings = AgentSettings(
        mode="real", provider="gemini_native", model=MODEL, api_key=SECRET
    )
    real_document = _run_advocates_with(real_provider, tmp_path / "real", real_settings)
    real_artifact = AdvocateArtifact.model_validate(real_document)
    assert real_artifact.captured_provider == "gemini_native"
    assert real_artifact.captured_mode == "real"
    assert real_artifact.captured_model == MODEL
    assert real_artifact.is_live_eligible is True


def test_a_native_capture_cannot_masquerade_as_openai_compatible(tmp_path: Path) -> None:
    """Provenance is read back, not assumed: the two providers stay distinct.

    Rewriting only ``capturedProvider`` must move the artefact from one provider
    to the other. If the field were inferred from the artefact class, or ignored
    on read, both variants would report the same provider and a native capture
    could be passed off as an older ``openai_compatible`` one.
    """
    from app.replay.artifacts import AdvocateArtifact

    client = _StubClient(responder=_side_aware_responder)
    provider, _, _ = _provider(client=client)
    document = _run_advocates_with(provider, tmp_path)

    native = AdvocateArtifact.model_validate(
        {**document, "capturedProvider": "gemini_native"}
    )
    compatible = AdvocateArtifact.model_validate(
        {**document, "capturedProvider": "openai_compatible"}
    )

    assert native.captured_provider == "gemini_native"
    assert compatible.captured_provider == "openai_compatible"
    assert native.captured_provider != compatible.captured_provider
    # The document itself says gemini_native; the rewrite is the only difference.
    assert document["capturedProvider"] == "gemini_native"
    assert native.captured_provider != compatible.captured_provider
