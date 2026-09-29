"""The rebuttal agent.

Like the advocates and the Judge, this depends only on the ``LlmProvider``
protocol. There is no provider-specific or Gemini-specific code here, and there
is no per-side subclass: one class serves both sides, parameterised by the
``ownSide`` carried in the context.

The class is deliberately thin. It assembles a prompt, calls the provider, and
parses the response into ``RebuttalOutput``. It does **not** decide whether the
rebuttal is trustworthy — that is ``RebuttalValidationService``, which runs
afterwards and is allowed to reject any response this class returns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.agents.config import AgentSettings
from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProvider,
    LlmProviderError,
)
from app.agents.providers.openai_compatible import parse_json_object
from app.models.rebuttal import (
    RebuttalCaseContext,
    RebuttalOutput,
    RebuttalOutputErrorCode,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"

REBUTTAL_PROMPT_VERSION = "rebuttal_prompts_v1"


class RebuttalAgentError(RuntimeError):
    """Raised when a rebuttal cannot produce a usable, validated output.

    Mirrors ``AdvocateAgentError`` and ``JudgeAgentError``, including the
    ``completion`` carry-over, so a rebuttal that failed *after* the model
    responded still contributes its latency and token usage to the record.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = RebuttalOutputErrorCode.REBUTTAL_ERROR,
        completion: LlmCompletion | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.completion = completion


@dataclass(frozen=True)
class RebuttalArgument:
    """A parsed rebuttal plus the execution metrics behind it."""

    output: RebuttalOutput
    completion: LlmCompletion


class RebuttalAgent:
    """Produces a structured rebuttal from verified claims only."""

    def __init__(self, provider: LlmProvider, settings: AgentSettings | None = None) -> None:
        self._provider = provider
        self._settings = settings or AgentSettings()
        self._rules = (PROMPTS_DIR / "rebuttal_common.md").read_text(encoding="utf-8")
        self._output_schema = json.loads(
            (PROMPTS_DIR / "rebuttal_output_schema.json").read_text(encoding="utf-8")
        )

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def prompt_version(self) -> str:
        return REBUTTAL_PROMPT_VERSION

    def rebut(self, context: RebuttalCaseContext) -> RebuttalOutput:
        """Produce a validated rebuttal. Metrics are discarded."""
        return self.rebut_with_trace(context).output

    def rebut_with_trace(self, context: RebuttalCaseContext) -> RebuttalArgument:
        completion = self._provider.complete(
            AgentCompletionRequest(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(context),
                response_schema=self._output_schema,
                temperature=self._settings.temperature,
                max_tokens=self._settings.max_tokens,
                extra_body=self._extra_body(),
                metadata={
                    "context_json": context.model_dump_json(by_alias=True),
                    "role": "rebuttal",
                    "side": context.own_side,
                },
            )
        )
        return RebuttalArgument(
            output=self._parse(completion.raw_text, context.own_side, completion),
            completion=completion,
        )

    def _extra_body(self) -> dict[str, object]:
        if self._settings.reasoning_effort:
            return {"reasoning_effort": self._settings.reasoning_effort}
        return {}

    def _system_prompt(self) -> str:
        return "\n\n".join(
            [
                self._rules,
                "# Required output schema\n\n"
                "Respond with a single JSON object matching this schema exactly:\n\n"
                "```json\n"
                + json.dumps(self._output_schema, indent=2)
                + "\n```",
            ]
        )

    @staticmethod
    def _user_prompt(context: RebuttalCaseContext) -> str:
        return (
            "The following is the trusted case record. It contains the deterministic facts, "
            "the applicable policy and its evaluation, your own verified claims, and the "
            "opposing side's verified claims. Only the opposing claims are valid rebuttal "
            "targets. Rejected claims are deliberately not included and must not be inferred. "
            "This is the only rebuttal round. Every deterministic value is authoritative — do "
            "not recompute anything. Produce your JSON response.\n\n"
            "<rebuttal_context>\n"
            + json.dumps(context.model_dump(by_alias=True), indent=2)
            + "\n</rebuttal_context>"
        )

    def _parse(
        self,
        raw_text: str,
        expected_side: str,
        completion: LlmCompletion | None = None,
    ) -> RebuttalOutput:
        try:
            payload = parse_json_object(raw_text)
        except ValueError as error:
            if completion is not None and completion.truncated:
                raise RebuttalAgentError(
                    (
                        "rebuttal output was cut off by the token limit "
                        f"(finish_reason='length', max_tokens={self._settings.max_tokens}). "
                        "Raise AGENT_MAX_TOKENS."
                    ),
                    code=RebuttalOutputErrorCode.OUTPUT_TRUNCATED,
                    completion=completion,
                ) from error
            raise RebuttalAgentError(
                f"malformed rebuttal JSON: {error}",
                code=RebuttalOutputErrorCode.MALFORMED_JSON,
                completion=completion,
            ) from error
        try:
            output = RebuttalOutput.model_validate(payload)
        except ValidationError as error:
            extra = _unknown_keys(error)
            detail = (
                f"rebuttal output failed schema validation: {error.error_count()} issue(s)"
            )
            if extra:
                detail += f" (unexpected field(s): {', '.join(extra)})"
            raise RebuttalAgentError(
                detail,
                code=RebuttalOutputErrorCode.SCHEMA_VALIDATION_FAILED,
                completion=completion,
            ) from error
        if output.side != expected_side:
            raise RebuttalAgentError(
                (
                    f"rebuttal returned side {output.side!r} but was assigned "
                    f"{expected_side!r}"
                ),
                code=RebuttalOutputErrorCode.WRONG_SIDE,
                completion=completion,
            )
        return output


def _unknown_keys(error: ValidationError) -> list[str]:
    keys: list[str] = []
    for issue in error.errors():
        if issue.get("type") == "extra_forbidden":
            location = issue.get("loc") or ()
            if location:
                keys.append(str(location[-1]))
    return keys


__all__ = [
    "REBUTTAL_PROMPT_VERSION",
    "RebuttalAgent",
    "RebuttalAgentError",
    "RebuttalArgument",
]
