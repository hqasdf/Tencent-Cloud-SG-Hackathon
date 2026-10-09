"""The bounded, typed request contract for every RydeResolve agent call.

Every test here is offline. The provider's transport is a stub that records the
body and returns a canned response, so the request shape is asserted against what
would actually be transmitted without anything leaving the process. The suite is
run under the egress guard, which makes "zero external calls" enforced rather
than promised.

Two themes run through it, and they are the two things this change is for:

1. **Bounded.** Timeout, output budget, reasoning effort and temperature are
   fixed, and the bounds are ceilings enforced in code — a configuration value
   above the ceiling is rejected, not clamped, so configuration cannot silently
   diverge from behaviour.
2. **Separated.** A provider failure and a model-output failure are different
   facts. Only the second is repairable, and only once. A trust-boundary
   rejection is neither, and is not repairable at all.
"""

from __future__ import annotations

import json

import pytest

from app.agents.base_advocate import AdvocateAgentError
from app.agents.config import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_TOKENS,
    MAX_TIMEOUT_SECONDS,
    AgentConfigurationError,
    AgentSettings,
)
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProviderError,
    ProviderErrorCode,
)
from app.agents.providers.openai_compatible import (
    OpenAiCompatibleProvider,
    build_response_format,
    provider_schema,
)
from app.agents.rider_advocate import RiderAdvocateAgent
from app.agents.typed_call import (
    MAX_REPAIR_ATTEMPTS,
    REPAIRABLE_OUTPUT_CODES,
    REPAIR_INSTRUCTION,
    ModelOutputContractError,
    complete_typed,
)
from app.repositories.case_repository import MockCaseRepository
from app.services.case_service import CaseService

ROUTE_CASE = "DISP-005"
API_KEY = "test-key-not-a-real-credential"


# ---------------------------------------------------------------------------
# Transport stubs. Nothing here opens a socket.
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int = 200, content: str = "{}", finish_reason: str = "stop"):
        self.status_code = status_code
        self.headers: dict = {}
        self._content = content
        self._finish_reason = finish_reason

    def json(self) -> dict:
        return {
            "choices": [
                {"message": {"content": self._content}, "finish_reason": self._finish_reason}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }


class _Client:
    """Records every outbound body; returns queued responses in order."""

    def __init__(self, sink: list, queue: list) -> None:
        self._sink = sink
        self._queue = queue

    def post(self, url: str, **kwargs: object):
        self._sink.append({"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        return self._queue.pop(0) if self._queue else _Response()

    def get(self, url: str, **kwargs: object):  # pragma: no cover - unused
        raise AssertionError("no GET expected")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _provider(queue: list | None = None) -> tuple[OpenAiCompatibleProvider, list]:
    sink: list = []
    provider = OpenAiCompatibleProvider(
        base_url="https://example.invalid/v1",
        model="gemini-3.8-flash",
        api_key=API_KEY,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        client_factory=lambda **_: _Client(sink, list(queue or [])),
    )
    return provider, sink


def _advocate_context():
    service = CaseService(MockCaseRepository())
    case = service._load_case(ROUTE_CASE)
    analysis = service.get_analysis(ROUTE_CASE)
    return AdvocateContextBuilder().build(case, analysis)


def _advocate_request() -> AgentCompletionRequest:
    """The exact request the Rider Advocate builds."""
    from app.agents.providers.mock import MockLlmProvider

    agent = RiderAdvocateAgent(MockLlmProvider(), AgentSettings(mode="mock", provider="mock"))
    return AgentCompletionRequest(
        system_prompt=agent._system_prompt(),
        user_prompt=agent._user_prompt(_advocate_context()),
        response_schema=agent._output_schema,
        temperature=agent._settings.temperature,
        max_tokens=agent._settings.max_tokens,
        extra_body=agent._extra_body(),
        metadata={"side": "RIDER"},
    )


class _RecordingProvider:
    """Returns queued raw texts; counts calls. Never opens a socket."""

    name = "recording"

    def __init__(self, texts: list[str], *, raises: list | None = None) -> None:
        self._texts = list(texts)
        self._raises = list(raises or [])
        self.calls: list[AgentCompletionRequest] = []

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.calls.append(request)
        if self._raises:
            raise self._raises.pop(0)
        text = self._texts.pop(0) if self._texts else "{}"
        return LlmCompletion(
            raw_text=text,
            provider_name=self.name,
            model_name="gemini-3.8-flash",
            duration_ms=1,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
        )


# ---------------------------------------------------------------------------
# 1-4. The bounds themselves
# ---------------------------------------------------------------------------


def test_timeout_is_thirty_seconds(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_TIMEOUT_SECONDS", raising=False)

    settings = AgentSettings.from_environment()

    assert settings.timeout_seconds == 30.0
    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert MAX_TIMEOUT_SECONDS == 30.0


def test_typed_output_cap_is_1024(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)

    settings = AgentSettings.from_environment()

    assert settings.max_tokens == 1024
    assert DEFAULT_MAX_TOKENS == 1024
    assert MAX_OUTPUT_TOKENS == 1024


def test_reasoning_effort_defaults_to_low(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_REASONING_EFFORT", raising=False)

    settings = AgentSettings.from_environment()

    assert settings.reasoning_effort == "low"
    assert DEFAULT_REASONING_EFFORT == "low"


def test_temperature_is_zero(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)

    assert AgentSettings.from_environment().temperature == 0.0


def test_a_timeout_above_the_ceiling_is_rejected_not_clamped(monkeypatch) -> None:
    """Configuration must not be able to silently outlive its ceiling.

    Clamping would leave the operator believing the value they wrote is in
    force, which is the quiet divergence this project refuses elsewhere.
    """
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "60")

    with pytest.raises(AgentConfigurationError, match="must be <= 30"):
        AgentSettings.from_environment()


def test_an_output_cap_above_the_ceiling_is_rejected_not_clamped(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MAX_TOKENS", "8192")

    with pytest.raises(AgentConfigurationError, match="must be <= 1024"):
        AgentSettings.from_environment()


def test_an_unsupported_reasoning_effort_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "medium-high")

    with pytest.raises(AgentConfigurationError, match="AGENT_REASONING_EFFORT"):
        AgentSettings.from_environment()


def test_reasoning_effort_can_be_stepped_back_to_the_provider_default(monkeypatch) -> None:
    """An explicit empty value means "let the provider decide", not "low"."""
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "")

    assert AgentSettings.from_environment().reasoning_effort is None


# ---------------------------------------------------------------------------
# 5-6. What actually reaches the wire
# ---------------------------------------------------------------------------


def test_the_real_schema_reaches_the_provider_payload() -> None:
    """The schema is a request constraint, not a sentence in the prompt.

    The expected schema is *generated* from the Pydantic model rather than
    copied from a literal, so this test cannot drift away from the model it is
    supposed to be checking. ``additionalProperties`` is deliberately not
    asserted: it is a property of the model, and the advocate model does not
    forbid extra keys. Demanding it here would quietly require the schema
    generator to tighten the model, which is the model's decision to make.
    """
    from app.agents.provider_schema import provider_schema_for
    from app.models.advocate import AdvocateOutput

    provider, sink = _provider()

    provider.complete(_advocate_request())

    body = sink[0]["json"]
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert schema == provider_schema_for(AdvocateOutput)
    assert schema["type"] == "object"
    assert "claims" in schema["properties"]
    assert response_format["json_schema"]["name"] == "AdvocateOutput"


def test_the_schema_is_no_longer_duplicated_in_the_prompt() -> None:
    """Sending it twice costs input tokens and can only disagree with itself.

    Asserted by looking for the schema *itself* rather than for a heading or a
    fence, because a fence also appears in legitimate prompt prose.
    """
    from app.agents.provider_schema import provider_schema_for
    from app.models.advocate import AdvocateOutput

    schema = provider_schema_for(AdvocateOutput)
    provider, sink = _provider()

    provider.complete(_advocate_request())

    system = sink[0]["json"]["messages"][0]["content"]
    assert "Required output schema" not in system
    assert '"claimId"' not in system, "a schema property name leaked into the prompt"
    assert json.dumps(schema, indent=2) not in system
    assert json.dumps(schema, separators=(",", ":")) not in system
    assert "```" not in system, "a code fence invites the model to fence its own answer"


def test_the_prompt_carries_only_what_the_schema_cannot_express() -> None:
    """Behavioural rules stay; structural description goes."""
    system = _advocate_request().system_prompt

    assert "Never invent, guess, or extrapolate an evidence ID" in system
    assert "Stop immediately after the closing brace." in system
    assert "properties" not in system


def test_streaming_is_disabled_explicitly() -> None:
    """A streamed response cannot be validated as one object."""
    provider, sink = _provider()

    provider.complete(_advocate_request())

    assert sink[0]["json"]["stream"] is False


def test_the_request_carries_the_bounded_envelope() -> None:
    provider, sink = _provider()

    provider.complete(_advocate_request())

    body = sink[0]["json"]
    assert body["max_tokens"] == 1024
    assert body["temperature"] == 0.0
    assert body["reasoning_effort"] == "low"
    assert body["model"] == "gemini-3.8-flash"


def test_the_provider_is_constructed_with_the_thirty_second_timeout() -> None:
    """The timeout is applied at the transport, not merely recorded."""
    seen: list = []

    def factory(**kwargs: object):
        seen.append(kwargs)
        return _Client([], [])

    provider = OpenAiCompatibleProvider(
        base_url="https://example.invalid/v1",
        model="gemini-3.8-flash",
        api_key=API_KEY,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        client_factory=factory,
    )
    provider.complete(_advocate_request())

    assert seen[0]["timeout"] == 30.0


# ---------------------------------------------------------------------------
# The schema projection
# ---------------------------------------------------------------------------


def test_document_meta_keywords_are_stripped() -> None:
    projected = provider_schema({"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"})

    assert projected == {"type": "object"}


def test_a_bare_multi_type_array_becomes_any_of() -> None:
    """``["number","boolean","string"]`` is not in the documented subset; ``anyOf`` is.

    Semantics-preserving, so the constraint the model is held to is unchanged and
    the schema Pydantic validates against is untouched.
    """
    projected = provider_schema({"type": ["number", "boolean", "string"]})

    assert projected == {
        "anyOf": [{"type": "number"}, {"type": "boolean"}, {"type": "string"}]
    }


def test_a_nullable_type_array_is_left_alone() -> None:
    """``["string","null"]`` *is* documented, so it is not rewritten."""
    assert provider_schema({"type": ["string", "null"]}) == {"type": ["string", "null"]}


def test_the_projection_is_applied_recursively() -> None:
    projected = provider_schema(
        {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"value": {"type": ["number", "string"]}}},
                }
            },
        }
    )

    value = projected["properties"]["facts"]["items"]["properties"]["value"]
    assert value == {"anyOf": [{"type": "number"}, {"type": "string"}]}


def test_the_real_advocate_schema_projects_without_a_bare_union() -> None:
    """The shipped schema is provider-representable after projection."""
    from app.agents.provider_schema import provider_schema_for
    from app.models.advocate import AdvocateOutput

    schema = provider_schema_for(AdvocateOutput)
    projected = provider_schema(schema)

    def walk(node: object) -> None:
        if isinstance(node, dict):
            types = node.get("type")
            if isinstance(types, list) and len(types) > 1 and "null" not in types:
                raise AssertionError(f"bare multi-type array survived projection: {types}")
            assert "$schema" not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(projected)


def test_no_schema_means_the_weakest_correct_constraint() -> None:
    assert build_response_format(None) == {"type": "json_object"}
    assert build_response_format({}) == {"type": "json_object"}


# ---------------------------------------------------------------------------
# 7-8. A valid response still parses and still validates
# ---------------------------------------------------------------------------


def test_a_valid_response_parses_and_validates() -> None:
    good = json.dumps(
        {
            "side": "RIDER",
            "summary": "The deviation is unexplained.",
            "claims": [],
            "requestedOutcome": "PARTIAL_REFUND",
            "contextAcknowledged": True,
        }
    )
    provider = _RecordingProvider([good])

    output, completion = complete_typed(provider, _advocate_request(), lambda t, c: json.loads(t))

    assert output["side"] == "RIDER"
    assert len(provider.calls) == 1, "a valid response must not be repaired"
    assert completion.attempt_count == 1


def test_schema_validation_still_runs_after_the_provider_constraint() -> None:
    """The provider constraint is defence in depth, not a replacement.

    A response that violates the schema is rejected by Pydantic even though the
    provider was asked to constrain it. The schema reaching the request does not
    make the local validator optional.
    """
    from app.agents.providers.mock import MockLlmProvider

    agent = RiderAdvocateAgent(_RecordingProvider(['{"side": "RIDER"}']), AgentSettings(mode="mock", provider="mock"))

    with pytest.raises(AdvocateAgentError) as raised:
        agent.argue(_advocate_context())

    assert raised.value.code == "SCHEMA_VALIDATION_FAILED"
    assert raised.value.completion is not None


# ---------------------------------------------------------------------------
# 9-10. Exactly one repair, only for model-output failures
# ---------------------------------------------------------------------------


class _ContractError(ModelOutputContractError):
    """Stands in for a real agent error, which is what the loop actually sees."""

    def __init__(self, code: str) -> None:
        super().__init__(f"output violated {code}")
        self.code = code


def _strict_parser(text: str, completion=None) -> dict:
    """A parser that behaves like the real ones: coded errors, not raw ones."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise _ContractError("MALFORMED_JSON") from error
    if not isinstance(payload, dict):
        raise _ContractError("MALFORMED_JSON")
    return payload


def test_malformed_output_gets_exactly_one_repair() -> None:
    good = json.dumps({"side": "RIDER", "summary": "ok", "claims": [], "requestedOutcome": "NO_REFUND", "contextAcknowledged": True})
    provider = _RecordingProvider(["not json at all", good])

    output, completion = complete_typed(provider, _advocate_request(), _strict_parser)

    assert output["side"] == "RIDER"
    assert len(provider.calls) == 2, "one initial attempt plus exactly one repair"
    assert REPAIR_INSTRUCTION in provider.calls[1].user_prompt


def test_the_repair_instruction_is_appended_not_resubstituted() -> None:
    """The repair keeps the original context and adds a short correction."""
    provider = _RecordingProvider(["not json", "still not json"])

    with pytest.raises(_ContractError):
        complete_typed(provider, _advocate_request(), _strict_parser)

    first, second = provider.calls
    assert second.system_prompt == first.system_prompt
    assert second.user_prompt.startswith(first.user_prompt)
    assert second.user_prompt.endswith(REPAIR_INSTRUCTION)
    assert len(second.user_prompt) - len(first.user_prompt) < 500, (
        "the repair must be a short correction, not a resent diagnostic transcript"
    )


def test_a_second_malformed_output_fails() -> None:
    provider = _RecordingProvider(["nope", "still nope"])

    with pytest.raises(_ContractError):
        complete_typed(provider, _advocate_request(), _strict_parser)

    assert len(provider.calls) == 2, "there is no third attempt"


def test_a_schema_invalid_output_is_repairable() -> None:
    provider = _RecordingProvider(["{}", '{"side": "RIDER", "summary": "ok"}'])

    def parse(text: str, completion) -> dict:
        payload = json.loads(text)
        if "summary" not in payload:
            raise _ContractError("SCHEMA_VALIDATION_FAILED")
        return payload

    output, _ = complete_typed(provider, _advocate_request(), parse)

    assert output["summary"] == "ok"
    assert len(provider.calls) == 2


def test_an_uncoded_parser_error_is_not_repairable() -> None:
    """A bug in the parser is not something to re-ask a model about.

    Only ``ModelOutputContractError`` with a repairable code earns a second
    request; anything else propagates immediately, so a coding error cannot be
    disguised as a model problem and paid for twice.
    """
    provider = _RecordingProvider(["{}", "{}"])

    with pytest.raises(KeyError):
        complete_typed(provider, _advocate_request(), lambda t, c: {}["missing"])

    assert len(provider.calls) == 1


def test_the_repair_attempts_are_counted_together() -> None:
    """The reported cost is the cost of producing the output, not of the last try."""
    good = '{"ok": true}'
    provider = _RecordingProvider(["bad", good])

    _, completion = complete_typed(provider, _advocate_request(), _strict_parser)

    assert completion.attempt_count == 2
    assert completion.retry_count == 1
    assert completion.input_tokens == 20
    assert completion.output_tokens == 40
    assert completion.total_tokens == 60


def test_unknown_token_counts_stay_unknown_across_a_repair() -> None:
    """``Unknown is not zero`` survives the merge."""
    class _NoUsage(_RecordingProvider):
        def complete(self, request):  # noqa: ANN001, ANN201
            self.calls.append(request)
            text = self._texts.pop(0)
            return LlmCompletion(raw_text=text, provider_name=self.name, model_name=None, duration_ms=1)

    provider = _NoUsage(["bad", '{"ok": true}'])

    _, completion = complete_typed(provider, _advocate_request(), _strict_parser)

    assert completion.input_tokens is None
    assert completion.total_tokens is None
    assert completion.attempt_count == 2


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ProviderErrorCode.RATE_LIMITED, 429),
        (ProviderErrorCode.SERVER_ERROR, 503),
        (ProviderErrorCode.TIMEOUT, None),
        (ProviderErrorCode.NETWORK_ERROR, None),
        (ProviderErrorCode.AUTHENTICATION_FAILED, 401),
    ],
)
def test_provider_failures_are_never_repaired(code, status) -> None:
    """A provider failure is not malformed reasoning.

    Repairing it would spend a second request against a provider that has already
    refused, and would report an outage as a model-output problem.
    """
    error = LlmProviderError("provider failed", provider="recording", code=code)
    provider = _RecordingProvider([], raises=[error])

    with pytest.raises(LlmProviderError) as raised:
        complete_typed(provider, _advocate_request(), lambda t, c: json.loads(t))

    assert raised.value.code == code
    assert len(provider.calls) == 1, "a provider failure must not be re-asked"


def test_only_malformed_and_schema_invalid_codes_are_repairable() -> None:
    assert REPAIRABLE_OUTPUT_CODES == {"MALFORMED_JSON", "SCHEMA_VALIDATION_FAILED"}
    assert "OUTPUT_TRUNCATED" not in REPAIRABLE_OUTPUT_CODES, (
        "re-asking with the same budget truncates the same way"
    )
    assert "WRONG_SIDE" not in REPAIRABLE_OUTPUT_CODES


def test_the_repair_is_bounded_to_one_by_construction() -> None:
    assert MAX_REPAIR_ATTEMPTS == 1


def test_a_truncated_response_is_not_treated_as_valid() -> None:
    """Truncation is its own failure, and it is not repaired."""
    provider, sink = _provider([_Response(content='{"side": "RID', finish_reason="length")])

    completion = provider.complete(_advocate_request())

    assert completion.finish_reason == "length"
    assert completion.truncated is True
    assert len(sink) == 1


def test_a_truncated_response_fails_without_a_repair() -> None:
    """A truncated response is reported as truncation, not as malformed JSON.

    Two things are asserted, and both matter. The code is ``OUTPUT_TRUNCATED`` so
    an operator looks at the budget rather than at the prompt. And the provider is
    asked **once**: re-asking with the same budget truncates the same way, so a
    repair here would spend a request to reproduce the failure.
    """
    from app.agents.providers.mock import MockLlmProvider

    truncated = LlmCompletion(
        raw_text='{"side": "RID',
        provider_name="recording",
        model_name="gemini-3.8-flash",
        duration_ms=1,
        finish_reason="length",
    )

    class _TruncatingProvider:
        name = "recording"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request):  # noqa: ANN001, ANN201
            self.calls += 1
            return truncated

    provider = _TruncatingProvider()
    agent = RiderAdvocateAgent(provider, AgentSettings(mode="mock", provider="mock"))

    with pytest.raises(AdvocateAgentError) as raised:
        agent.argue(_advocate_context())

    assert raised.value.code == "OUTPUT_TRUNCATED"
    assert provider.calls == 1, "a truncation must not be repaired"


def test_every_role_output_fits_the_1024_budget_with_margin() -> None:
    """The budget must hold the schema's realistic output, with room to spare.

    This is the guard against the prompt or the schema quietly growing until the
    budget no longer fits. It measures the deterministic mock output, which is a
    *lower* bound on a real model's length — so passing here is necessary, not
    sufficient, and the live run is what confirms the margin. What it catches is
    the case that is checkable offline: an output shape that cannot fit at all.
    """
    from app.agents.driver_advocate import DriverAdvocateAgent
    from app.agents.judge_agent import JudgeAgent
    from app.agents.providers.mock import MockLlmProvider
    from app.agents.rebuttal_agent import RebuttalAgent
    from app.agents.rebuttal_context_builder import RebuttalContextBuilder
    from app.agents.judge_context_builder import JudgeContextBuilder
    from app.services.advocate_orchestrator import AdvocateOrchestratorService
    from app.services.case_service import CaseService
    from app.services.rebuttal_orchestrator import RebuttalOrchestratorService

    settings = AgentSettings(mode="mock", provider="mock")
    service = CaseService(MockCaseRepository())
    case = service._load_case(ROUTE_CASE)
    analysis = service.get_analysis(ROUTE_CASE)
    context = AdvocateContextBuilder().build(case, analysis)
    mock = MockLlmProvider()

    advocates = AdvocateOrchestratorService(provider=mock, settings=settings).run(case, analysis, context)
    rebuttals = RebuttalOrchestratorService(provider=mock, settings=settings).run(
        case, analysis, context, advocates
    )
    judge_context = JudgeContextBuilder().build(
        case, analysis, context, advocates.rider, advocates.driver, rebuttals
    )

    outputs = {
        "RIDER_ADVOCATE": advocates.rider.model_dump_json(by_alias=True),
        "DRIVER_ADVOCATE": advocates.driver.model_dump_json(by_alias=True),
        "JUDGE": JudgeAgent(mock, settings).judge(judge_context).model_dump_json(by_alias=True),
    }
    for side_label, side in (("RIDER", rebuttals.rider), ("DRIVER", rebuttals.driver)):
        if side.verified_rebuttals:
            outputs[f"{side_label}_REBUTTAL"] = json.dumps(
                [item.model_dump(by_alias=True) for item in side.verified_rebuttals]
            )

    # 4 characters per token is the same crude heuristic the measurement harness
    # uses; the point is the ratio, not the absolute count.
    for role, text in outputs.items():
        estimated = len(text) // 4
        assert estimated < DEFAULT_MAX_TOKENS, (
            f"{role} produced ~{estimated} tokens, which does not fit the "
            f"{DEFAULT_MAX_TOKENS}-token budget"
        )
        assert estimated < DEFAULT_MAX_TOKENS // 2, (
            f"{role} used ~{estimated} of {DEFAULT_MAX_TOKENS} tokens on the mock "
            "output alone; a real model needs the headroom"
        )


# ---------------------------------------------------------------------------
# 14. A trust-boundary rejection is not repairable
# ---------------------------------------------------------------------------


def test_a_trust_boundary_rejection_does_not_trigger_a_repair() -> None:
    """The important RydeResolve difference, asserted at the seam where it could break.

    A schema-valid claim citing evidence that does not exist is rejected by
    ``AdvocateClaimVerificationService``. The model answered correctly and
    untrustworthily, so there is nothing to repair — and asking it to rewrite the
    claim would be asking it to launder a rejection into a pass.

    The structural reason this cannot regress is that verification runs *after*
    ``complete_typed`` has returned, so a rejection has no path back into the
    repair loop. This test pins the observable consequence: one call, one
    rejected claim, no second request.
    """
    from app.agents.claim_verification import AdvocateClaimVerificationService
    from app.agents.providers.mock import MockLlmProvider
    from app.models.advocate import AdvocateOutput

    fabricated = json.dumps(
        {
            "side": "RIDER",
            "summary": "Fabricated support.",
            "claims": [
                {
                    "claimId": "R1",
                    "claim": "The driver took a longer route.",
                    "evidenceIds": ["EVIDENCE-THAT-DOES-NOT-EXIST"],
                    "policyRefs": [],
                    "reasoningSummary": "Because of the fabricated evidence.",
                    "importance": "HIGH",
                }
            ],
            "requestedOutcome": "FULL_REFUND",
            "contextAcknowledged": True,
        }
    )
    provider = _RecordingProvider([fabricated])
    agent = RiderAdvocateAgent(provider, AgentSettings(mode="mock", provider="mock"))
    context = _advocate_context()

    argument = agent.argue_with_trace(context)
    outcome = AdvocateClaimVerificationService().verify(argument.output, context)

    assert len(provider.calls) == 1, "a rejected claim must not be sent back to the model"
    assert argument.output.claims[0].claim_id == "R1"
    assert [claim.claim_id for claim in outcome.verified_claims] == []
    assert [claim.claim_id for claim in outcome.rejected_claims] == ["R1"]
    assert outcome.rejected_claims[0].reason == "EVIDENCE_ID_NOT_FOUND"
    assert AdvocateOutput.model_validate(argument.output.model_dump())


# ---------------------------------------------------------------------------
# 15. The credential never reaches a log, a message, or a repr
# ---------------------------------------------------------------------------


def test_the_credential_never_appears_in_a_provider_error() -> None:
    provider, _ = _provider([_Response(status_code=401)])

    with pytest.raises(LlmProviderError) as raised:
        provider.complete(_advocate_request())

    assert API_KEY not in str(raised.value)


def test_the_credential_is_not_in_the_agent_settings_repr(monkeypatch) -> None:
    """A dataclass reprs every field, so this one is excluded explicitly."""
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AGENT_API_KEY", API_KEY)

    settings = AgentSettings.from_environment()

    assert API_KEY not in repr(settings)
    assert "api_key" not in repr(settings)


def test_the_credential_is_not_in_the_request_body_or_url() -> None:
    provider, sink = _provider()

    provider.complete(_advocate_request())

    body = json.dumps(sink[0]["json"])
    assert API_KEY not in body
    assert API_KEY not in sink[0]["url"]


def test_the_credential_is_only_materialised_in_the_auth_header() -> None:
    provider, sink = _provider()

    provider.complete(_advocate_request())

    assert sink[0]["headers"]["Authorization"] == f"Bearer {API_KEY}"


def test_settings_describe_reports_presence_not_value() -> None:
    settings = AgentSettings(mode="real", provider="openai_compatible", model="m", base_url="https://x", api_key=API_KEY)

    described = settings.describe()

    assert described["credential_configured"] is True
    assert API_KEY not in json.dumps(described)


# ---------------------------------------------------------------------------
# 16-18. Nothing downstream of the request layer moved
# ---------------------------------------------------------------------------


def test_claim_verification_is_unchanged_by_the_request_layer() -> None:
    """The verification service is the same object, with the same rules.

    The request layer changed how a response is *obtained*. What makes a claim
    trustworthy is decided by the same code as before, over the same context.
    """
    from app.agents.claim_verification import AdvocateClaimVerificationService
    from app.agents.providers.mock import MockLlmProvider

    context = _advocate_context()
    agent = RiderAdvocateAgent(MockLlmProvider(), AgentSettings(mode="mock", provider="mock"))

    argument = agent.argue_with_trace(context)
    outcome = AdvocateClaimVerificationService().verify(argument.output, context)

    assert outcome.verified_claims or outcome.rejected_claims
    for claim in outcome.verified_claims:
        assert set(claim.evidence_ids) <= {item.id for item in context.evidence}


def test_the_judge_trust_boundary_is_unchanged() -> None:
    """Rejected material is still unrepresentable in the Judge context.

    Behavioural rather than textual, because a docstring mentioning
    ``rejected_claims`` proves nothing. A real verified claim is moved into the
    rejected list on a real advocate result, the Judge context is built from that
    result, and the claim is then shown to be absent from it — by id, by text and
    by reasoning summary.

    That is the property the whole architecture rests on: the Judge cannot be
    influenced by material the code refused, and it is not filtered out at the
    last moment — it never enters.
    """
    from app.agents.judge_context_builder import JudgeContextBuilder
    from app.agents.providers.mock import MockLlmProvider
    from app.models.advocate import ClaimRejection
    from app.repositories.case_repository import MockCaseRepository
    from app.services.advocate_orchestrator import AdvocateOrchestratorService
    from app.services.case_service import CaseService
    from app.services.rebuttal_orchestrator import RebuttalOrchestratorService

    settings = AgentSettings(mode="mock", provider="mock")
    service = CaseService(MockCaseRepository())
    case = service._load_case(ROUTE_CASE)
    analysis = service.get_analysis(ROUTE_CASE)
    context = AdvocateContextBuilder().build(case, analysis)

    mock = MockLlmProvider()
    run = AdvocateOrchestratorService(provider=mock, settings=settings).run(case, analysis, context)
    assert run.rider.verified_claims, "the fixture must produce a verified claim to move"

    moved = run.rider.verified_claims[0]
    poisoned_rider = run.rider.model_copy(
        update={
            "verified_claims": run.rider.verified_claims[1:],
            "rejected_claims": [
                *run.rider.rejected_claims,
                ClaimRejection(
                    claim_id=moved.claim_id,
                    claim=moved.claim,
                    reason="EVIDENCE_ID_NOT_FOUND",
                    detail="the cited evidence ID is not in the supplied context",
                ),
            ],
        }
    )
    poisoned = run.model_copy(update={"rider": poisoned_rider})
    rebuttals = RebuttalOrchestratorService(provider=mock, settings=settings).run(
        case, analysis, context, poisoned
    )

    judge_context = JudgeContextBuilder().build(
        case, analysis, context, poisoned.rider, poisoned.driver, rebuttals
    )
    serialised = judge_context.model_dump_json(by_alias=True)

    assert moved.claim_id not in serialised, "a rejected claim id reached the Judge context"
    assert moved.claim not in serialised, "rejected claim text reached the Judge context"
    assert "rejectedClaims" not in serialised
    assert "EVIDENCE_ID_NOT_FOUND" not in serialised


def test_the_deterministic_engines_are_untouched() -> None:
    """Deterministic analysis must be byte-identical after the request change."""
    from app.replay.hashing import deterministic_analysis_hash

    service = CaseService(MockCaseRepository())
    case = service._load_case(ROUTE_CASE)
    analysis = service.get_analysis(ROUTE_CASE)

    assert deterministic_analysis_hash(case, analysis) == (
        "33899cc478724950b44333590819eaa69245e58ba9572783a9511464dc39e407"
    )


def test_every_agent_role_receives_the_same_bounded_envelope() -> None:
    """All five roles, not just the advocates.

    A role that quietly kept the old envelope would be the one that runs out of
    budget in production.
    """
    from app.agents.judge_agent import JudgeAgent
    from app.agents.providers.mock import MockLlmProvider
    from app.agents.rebuttal_agent import RebuttalAgent
    from app.agents.driver_advocate import DriverAdvocateAgent

    settings = AgentSettings(mode="mock", provider="mock")
    provider = MockLlmProvider()

    for agent in (
        RiderAdvocateAgent(provider, settings),
        DriverAdvocateAgent(provider, settings),
        RebuttalAgent(provider, settings),
        JudgeAgent(provider, settings),
    ):
        assert agent._settings.timeout_seconds == 30.0
        assert agent._settings.max_tokens == 1024
        assert agent._settings.temperature == 0.0
        assert agent._settings.reasoning_effort == "low"
        assert agent._extra_body() == {"reasoning_effort": "low"}
        assert "output_contract" not in agent._system_prompt()  # the file name is not leaked
        assert "Required output schema" not in agent._system_prompt()
        assert "Stop immediately after the closing brace." in agent._system_prompt()


def test_the_output_contract_is_in_every_system_prompt() -> None:
    from app.agents.judge_agent import JudgeAgent
    from app.agents.providers.mock import MockLlmProvider
    from app.agents.rebuttal_agent import RebuttalAgent

    settings = AgentSettings(mode="mock", provider="mock")
    provider = MockLlmProvider()

    for agent in (RiderAdvocateAgent(provider, settings), RebuttalAgent(provider, settings), JudgeAgent(provider, settings)):
        prompt = agent._system_prompt()
        assert "exactly **one** JSON object" in prompt
        assert "Do not output internal reasoning" in prompt
        assert "The first character must be `{`" in prompt
