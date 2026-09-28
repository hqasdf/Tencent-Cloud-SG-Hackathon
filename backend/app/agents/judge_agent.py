"""The Judge agent.

Like the advocates, this depends only on the ``LlmProvider`` protocol. There is
no Gemini-specific code here, and there is deliberately no judge variant per
vendor: the Judge works with whatever provider and model the environment is
configured for.

The class is deliberately thin. It assembles a prompt, calls the provider, and
parses the response into ``JudgeOutput``. It does **not** decide whether the
output is trustworthy — that is ``JudgeOutputValidationService``, which runs
afterwards and is allowed to reject anything this class returns.
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
from app.models.judge import JudgeCaseContext, JudgeOutput, JudgeOutputErrorCode

PROMPTS_DIR = Path(__file__).parent / "prompts"

JUDGE_PROMPT_VERSION = "judge_prompts_v1"


class JudgeAgentError(RuntimeError):
    """Raised when the Judge cannot produce a usable, validated output.

    Mirrors ``AdvocateAgentError``, including the ``completion`` carry-over, so a
    Judge call that failed *after* the model responded still contributes its
    latency and token usage to the record.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = JudgeOutputErrorCode.JUDGE_ERROR,
        completion: LlmCompletion | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.completion = completion


@dataclass(frozen=True)
class JudgeDecision:
    """A parsed Judge output plus the execution metrics behind it."""

    output: JudgeOutput
    completion: LlmCompletion


class JudgeAgent:
    """Produces a structured Judge recommendation from verified inputs only."""

    def __init__(self, provider: LlmProvider, settings: AgentSettings | None = None) -> None:
        self._provider = provider
        self._settings = settings or AgentSettings()
        self._rules = (PROMPTS_DIR / "judge_common.md").read_text(encoding="utf-8")
        self._output_schema = json.loads(
            (PROMPTS_DIR / "judge_output_schema.json").read_text(encoding="utf-8")
        )

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def prompt_version(self) -> str:
        return JUDGE_PROMPT_VERSION

    def judge(self, context: JudgeCaseContext) -> JudgeOutput:
        """Produce a validated Judge output. Metrics are discarded."""
        return self.judge_with_trace(context).output

    def judge_with_trace(self, context: JudgeCaseContext) -> JudgeDecision:
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
                    "role": "judge",
                },
            )
        )
        return JudgeDecision(
            output=self._parse(completion.raw_text, completion),
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
    def _user_prompt(context: JudgeCaseContext) -> str:
        return (
            "The following is the trusted case record, containing the deterministic facts, "
            "the applicable policy and its evaluation, and the claims that passed "
            "deterministic verification. Rejected claims are deliberately not included and "
            "must not be inferred. Every deterministic value is authoritative — do not "
            "recompute anything. Produce your JSON response.\n\n"
            "<judge_context>\n"
            + json.dumps(context.model_dump(by_alias=True), indent=2)
            + "\n</judge_context>"
        )

    def _parse(self, raw_text: str, completion: LlmCompletion | None = None) -> JudgeOutput:
        try:
            payload = parse_json_object(raw_text)
        except ValueError as error:
            if completion is not None and completion.truncated:
                raise JudgeAgentError(
                    (
                        "judge output was cut off by the token limit "
                        f"(finish_reason='length', max_tokens={self._settings.max_tokens}). "
                        "Raise AGENT_MAX_TOKENS."
                    ),
                    code=JudgeOutputErrorCode.OUTPUT_TRUNCATED,
                    completion=completion,
                ) from error
            raise JudgeAgentError(
                f"malformed judge JSON: {error}",
                code=JudgeOutputErrorCode.MALFORMED_JSON,
                completion=completion,
            ) from error
        try:
            return JudgeOutput.model_validate(payload)
        except ValidationError as error:
            # ``extra="forbid"`` means an invented field such as ``refundAmount``
            # surfaces here as a schema failure rather than being silently
            # dropped, so the message names the offending keys when there are any.
            extra = _unknown_keys(error)
            detail = f"judge output failed schema validation: {error.error_count()} issue(s)"
            if extra:
                detail += f" (unexpected field(s): {', '.join(extra)})"
            raise JudgeAgentError(
                detail,
                code=JudgeOutputErrorCode.SCHEMA_VALIDATION_FAILED,
                completion=completion,
            ) from error


def _unknown_keys(error: ValidationError) -> list[str]:
    keys: list[str] = []
    for issue in error.errors():
        if issue.get("type") == "extra_forbidden":
            location = issue.get("loc") or ()
            if location:
                keys.append(str(location[-1]))
    return keys


__all__ = ["JUDGE_PROMPT_VERSION", "JudgeAgent", "JudgeAgentError", "JudgeDecision"]
