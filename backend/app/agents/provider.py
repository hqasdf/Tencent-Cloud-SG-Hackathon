from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class LlmProviderError(RuntimeError):
    """Single error type crossing the provider boundary.

    Agent code must never branch on provider-specific exception types. Every
    transport, authentication, timeout, rate-limit, and malformed-response
    failure is normalised into this error.
    """

    def __init__(self, message: str, *, provider: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


@dataclass(frozen=True)
class AgentCompletionRequest:
    system_prompt: str
    user_prompt: str
    response_schema: dict | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmCompletion:
    raw_text: str
    provider_name: str
    model_name: str | None
    duration_ms: int


@runtime_checkable
class LlmProvider(Protocol):
    """Minimal provider contract.

    Adding a provider means implementing `complete` in a new class. Neither
    RiderAdvocateAgent nor DriverAdvocateAgent needs to change.
    """

    name: str

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion: ...
