from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from app.agents.config import AgentConfigurationError, AgentSettings
from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProvider,
    LlmProviderError,
    ProviderErrorCode,
)
from app.agents.provider_schema import provider_schema_for
from app.agents.providers.registry import get_provider
from app.agents.typed_call import ModelOutputContractError, complete_typed
from app.models.intake import IntakeExecutionMetadata, IntakeStructuredReply, Party


class IntakeProviderError(RuntimeError):
    """Safe error crossing the intake/provider boundary."""

    def __init__(self, message: str, *, code: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class IntakeOutputContractError(ModelOutputContractError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IntakeTurnResult:
    reply: IntakeStructuredReply
    execution: IntakeExecutionMetadata


class IntakeTurnProvider(Protocol):
    def complete_turn(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        party: Party,
        lifecycle: str,
    ) -> IntakeTurnResult: ...


class RegistryIntakeProvider:
    """Narrow intake adapter over the established agent provider contract.

    Configuration and provider construction are deliberately lazy so a missing
    intake configuration never prevents FastAPI from starting. Mock mode is not
    treated as an intake implementation; tests inject a controlled provider.
    """

    def __init__(
        self,
        *,
        settings: AgentSettings | None = None,
        provider: LlmProvider | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider

    def complete_turn(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        party: Party,
        lifecycle: str,
    ) -> IntakeTurnResult:
        try:
            settings = self._settings or AgentSettings.from_environment()
        except AgentConfigurationError as error:
            raise IntakeProviderError(
                "AI intake provider configuration is incomplete or invalid.",
                code=ProviderErrorCode.NOT_CONFIGURED,
            ) from error
        if self._provider is None and settings.mode == "mock":
            raise IntakeProviderError(
                "AI intake is not configured for the current server mode.",
                code=ProviderErrorCode.NOT_CONFIGURED,
            )
        try:
            provider = self._provider or get_provider(settings)
        except AgentConfigurationError as error:
            raise IntakeProviderError(
                "AI intake provider configuration is incomplete or invalid.",
                code=ProviderErrorCode.NOT_CONFIGURED,
            ) from error

        request = AgentCompletionRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=provider_schema_for(IntakeStructuredReply),
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            metadata={"role": f"INTAKE_{party.upper()}", "lifecycle": lifecycle},
            extra_body=(
                {"reasoning_effort": settings.reasoning_effort}
                if settings.reasoning_effort
                else {}
            ),
        )
        try:
            reply, completion = complete_typed(provider, request, _parse_reply)
        except LlmProviderError as error:
            raise IntakeProviderError(
                _safe_provider_message(error.code), code=error.code
            ) from error
        except IntakeOutputContractError as error:
            raise IntakeProviderError(
                "The intake provider returned an invalid structured response.",
                code=error.code,
                status_code=502,
            ) from error
        return IntakeTurnResult(reply=reply, execution=_execution_metadata(completion))


def _parse_reply(raw_text: str, _completion: LlmCompletion | None) -> IntakeStructuredReply:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as error:
        raise IntakeOutputContractError(
            "The intake provider returned malformed JSON.", code="MALFORMED_JSON"
        ) from error
    try:
        return IntakeStructuredReply.model_validate(payload)
    except ValidationError as error:
        raise IntakeOutputContractError(
            "The intake provider response did not match the required schema.",
            code="SCHEMA_VALIDATION_FAILED",
        ) from error


def _execution_metadata(completion: LlmCompletion) -> IntakeExecutionMetadata:
    return IntakeExecutionMetadata(
        provider=completion.provider_name,
        model=completion.model_name,
        durationMs=completion.duration_ms,
        inputTokens=completion.input_tokens,
        outputTokens=completion.output_tokens,
        totalTokens=completion.total_tokens,
        attemptCount=completion.attempt_count,
    )


def _safe_provider_message(code: str) -> str:
    messages = {
        ProviderErrorCode.AUTHENTICATION_FAILED: "AI intake authentication failed.",
        ProviderErrorCode.ACCESS_DENIED: "AI intake access was denied.",
        ProviderErrorCode.MODEL_NOT_FOUND: "The configured AI intake model was not found.",
        ProviderErrorCode.RATE_LIMITED: "AI intake is temporarily rate limited.",
        ProviderErrorCode.TIMEOUT: "AI intake timed out.",
        ProviderErrorCode.NETWORK_ERROR: "AI intake could not reach the provider.",
        ProviderErrorCode.SERVER_ERROR: "The AI intake provider is temporarily unavailable.",
        ProviderErrorCode.REQUEST_REJECTED: "The AI intake request was rejected.",
        ProviderErrorCode.MALFORMED_RESPONSE: "The AI intake provider returned an invalid response.",
        ProviderErrorCode.EMPTY_CONTENT: "The AI intake provider returned no content.",
    }
    return messages.get(code, "AI intake is temporarily unavailable.")


__all__ = [
    "IntakeProviderError",
    "IntakeTurnProvider",
    "IntakeTurnResult",
    "RegistryIntakeProvider",
]
