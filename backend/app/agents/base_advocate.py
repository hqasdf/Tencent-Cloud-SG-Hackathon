from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.agents.config import PROMPT_VERSION, AgentSettings
from app.agents.provider import AgentCompletionRequest, LlmProvider, LlmProviderError
from app.agents.providers.openai_compatible import parse_json_object
from app.models.advocate import AdvocateOutput
from app.models.agent import AgentCaseContext

PROMPTS_DIR = Path(__file__).parent / "prompts"


class AdvocateAgentError(RuntimeError):
    """Raised when an advocate cannot produce a usable, validated output."""


class BaseAdvocateAgent:
    """Shared prompt assembly and response handling for both advocates.

    The agent depends only on the LlmProvider protocol. It has no knowledge of
    OpenAI, Tencent, or any specific model.
    """

    side: str = ""
    prompt_file: str = ""

    def __init__(self, provider: LlmProvider, settings: AgentSettings | None = None) -> None:
        self._provider = provider
        self._settings = settings or AgentSettings()
        self._common_rules = (PROMPTS_DIR / "advocate_common.md").read_text(encoding="utf-8")
        self._side_prompt = (PROMPTS_DIR / self.prompt_file).read_text(encoding="utf-8")
        self._output_schema = json.loads(
            (PROMPTS_DIR / "output_schema.json").read_text(encoding="utf-8")
        )

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    def argue(self, context: AgentCaseContext) -> AdvocateOutput:
        response = self._provider.complete(
            AgentCompletionRequest(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(context),
                response_schema=self._output_schema,
                temperature=self._settings.temperature,
                max_tokens=2000,
                metadata={
                    "context_json": context.model_dump_json(by_alias=True),
                    "side": self.side,
                },
            )
        )
        return self._parse(response.raw_text)

    def _system_prompt(self) -> str:
        return "\n\n".join(
            [
                self._common_rules,
                self._side_prompt,
                "# Required output schema\n\n"
                "Respond with a single JSON object matching this schema exactly:\n\n"
                "```json\n"
                + json.dumps(self._output_schema, indent=2)
                + "\n```",
            ]
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

    def _parse(self, raw_text: str) -> AdvocateOutput:
        try:
            payload = parse_json_object(raw_text)
        except ValueError as error:
            raise AdvocateAgentError(f"malformed advocate JSON: {error}") from error
        try:
            output = AdvocateOutput.model_validate(payload)
        except ValidationError as error:
            raise AdvocateAgentError(
                f"advocate output failed schema validation: {error.error_count()} issue(s)"
            ) from error
        if output.side != self.side:
            raise AdvocateAgentError(
                f"advocate returned side {output.side!r} but was assigned {self.side!r}"
            )
        return output


__all__ = ["BaseAdvocateAgent", "AdvocateAgentError", "LlmProviderError"]
