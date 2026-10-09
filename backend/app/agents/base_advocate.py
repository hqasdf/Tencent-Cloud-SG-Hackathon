from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.agents.config import PROMPT_VERSION, AgentSettings
from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProvider,
    LlmProviderError,
)
from app.agents.providers.openai_compatible import parse_json_object
from app.agents.provider_schema import provider_schema_for
from app.agents.typed_call import ModelOutputContractError, complete_typed
from app.models.advocate import AdvocateOutput
from app.models.agent import AgentCaseContext

PROMPTS_DIR = Path(__file__).parent / "prompts"


class AdvocateOutputErrorCode:
    """Why an advocate's response could not be used.

    Separate from provider failures: the model *was* reached, but what came
    back was unusable. Stage 4C needs to tell these apart, because a model that
    is consistently malformed is a different problem from a provider outage.
    """

    MALFORMED_JSON = "MALFORMED_JSON"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    WRONG_SIDE = "WRONG_SIDE"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    ADVOCATE_ERROR = "ADVOCATE_ERROR"


class AdvocateAgentError(ModelOutputContractError):
    """Raised when an advocate cannot produce a usable, validated output.

    ``completion`` carries the provider response when there was one. It is kept
    so that a *failed* call still contributes its latency and token usage to the
    execution metadata: a model that reliably runs out of budget is exactly the
    kind of thing Stage 4C needs to see, and discarding the metrics would hide
    it.

    Deriving from ``ModelOutputContractError`` is what makes the code below
    eligible for the single bounded repair in ``complete_typed`` — but only for
    the codes listed in ``REPAIRABLE_OUTPUT_CODES``.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = AdvocateOutputErrorCode.ADVOCATE_ERROR,
        completion: LlmCompletion | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.completion = completion


@dataclass(frozen=True)
class AdvocateArgument:
    """A validated argument plus the raw execution metrics behind it."""

    output: AdvocateOutput
    completion: LlmCompletion


class BaseAdvocateAgent:
    """Shared prompt assembly and response handling for both advocates.

    The agent depends only on the LlmProvider protocol. It has no knowledge of
    OpenAI, Tencent, or any specific model, so switching from mock to a real
    model is purely a configuration change.
    """

    side: str = ""
    prompt_file: str = ""

    def __init__(self, provider: LlmProvider, settings: AgentSettings | None = None) -> None:
        self._provider = provider
        self._settings = settings or AgentSettings()
        self._common_rules = (PROMPTS_DIR / "advocate_common.md").read_text(encoding="utf-8")
        self._side_prompt = (PROMPTS_DIR / self.prompt_file).read_text(encoding="utf-8")
        self._output_contract = (PROMPTS_DIR / "output_contract.md").read_text(encoding="utf-8")
        # Derived from the model that validates the answer, so the schema the
        # provider is held to cannot drift from the schema the parser enforces.
        self._output_schema = provider_schema_for(AdvocateOutput)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    def argue(self, context: AgentCaseContext) -> AdvocateOutput:
        """Produce a validated argument. The metrics are discarded."""
        return self.argue_with_trace(context).output

    def argue_with_trace(self, context: AgentCaseContext) -> AdvocateArgument:
        """Produce a validated argument together with its execution metrics.

        The provider call and the parse are kept in one place so latency and
        token usage stay attached to the argument they produced. ``complete_typed``
        owns the single bounded repair for malformed output; this method supplies
        the parser, so the definition of "valid" stays in exactly one place.
        """
        request = AgentCompletionRequest(
            system_prompt=self._system_prompt(),
            user_prompt=self._user_prompt(context),
            response_schema=self._output_schema,
            temperature=self._settings.temperature,
            max_tokens=self._settings.max_tokens,
            # A gateway-only knob, kept out of the generic provider contract.
            extra_body=self._extra_body(),
            metadata={
                "context_json": context.model_dump_json(by_alias=True),
                "side": self.side,
            },
        )
        output, completion = complete_typed(self._provider, request, self._parse)
        return AdvocateArgument(output=output, completion=completion)

    def _extra_body(self) -> dict[str, object]:
        """Provider-specific optional request fields for this call.

        Unset by default so the request body is unchanged unless an operator
        explicitly asks for lighter reasoning. A local thinking model is the
        reason this is not hardcoded: the knob is meaningful for one transport
        and unknown to the others.
        """
        if self._settings.reasoning_effort:
            return {"reasoning_effort": self._settings.reasoning_effort}
        return {}

    def _system_prompt(self) -> str:
        """Behavioural rules only.

        The response schema is **not** reproduced here. It is attached to the
        request as a structured-output constraint, which the provider enforces
        while generating, so pasting it into the prompt as well would send the
        same information twice and pay input tokens for the duplicate. The
        prompt carries only what the schema cannot express: the authority
        hierarchy, the evidence and policy discipline, and the output contract.
        """
        return "\n\n".join(
            [self._common_rules, self._side_prompt, self._output_contract]
        )

    @staticmethod
    def _user_prompt(context: AgentCaseContext) -> str:
        return (
            "The following is the trusted case context. Every deterministic value in it is "
            "authoritative. Do not recompute anything. Produce your JSON response.\n\n"
            "<case_context>\n"
            + json.dumps(context.model_dump(by_alias=True), indent=2)
            + "\n</case_context>"
        )

    def _parse(self, raw_text: str, completion: LlmCompletion | None = None) -> AdvocateOutput:
        try:
            payload = parse_json_object(raw_text)
        except ValueError as error:
            # A response cut off by the token limit is not a model that cannot
            # write JSON. Reporting it as MALFORMED_JSON sends the operator
            # looking for a prompt bug instead of raising the token budget.
            if completion is not None and completion.truncated:
                raise AdvocateAgentError(
                    (
                        f"advocate output was cut off by the token limit "
                        f"(finish_reason='length', max_tokens={self._settings.max_tokens}). "
                        "Raise AGENT_MAX_TOKENS; a thinking model spends part of the "
                        "completion budget on reasoning before it emits any JSON."
                    ),
                    code=AdvocateOutputErrorCode.OUTPUT_TRUNCATED,
                    completion=completion,
                ) from error
            raise AdvocateAgentError(
                f"malformed advocate JSON: {error}",
                code=AdvocateOutputErrorCode.MALFORMED_JSON,
                completion=completion,
            ) from error
        try:
            output = AdvocateOutput.model_validate(payload)
        except ValidationError as error:
            raise AdvocateAgentError(
                f"advocate output failed schema validation: {error.error_count()} issue(s)",
                code=AdvocateOutputErrorCode.SCHEMA_VALIDATION_FAILED,
                completion=completion,
            ) from error
        if output.side != self.side:
            raise AdvocateAgentError(
                f"advocate returned side {output.side!r} but was assigned {self.side!r}",
                code=AdvocateOutputErrorCode.WRONG_SIDE,
                completion=completion,
            )
        return output


__all__ = [
    "AdvocateAgentError",
    "AdvocateArgument",
    "AdvocateOutputErrorCode",
    "BaseAdvocateAgent",
    "LlmProviderError",
]
