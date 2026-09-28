from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ProviderErrorCode:
    """Machine-readable failure classes crossing the provider boundary.

    These exist so the orchestrator can report an honest, specific failure
    ("authentication failed") instead of a generic outage, and so Stage 4C can
    group model failures by cause without parsing prose. They are deliberately
    coarse: the point is to distinguish *what the operator must fix*, not to
    mirror every upstream status code.
    """

    NOT_CONFIGURED = "NOT_CONFIGURED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    ACCESS_DENIED = "ACCESS_DENIED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class LlmProviderError(RuntimeError):
    """Single error type crossing the provider boundary.

    Agent code must never branch on provider-specific exception types. Every
    transport, authentication, timeout, rate-limit, and malformed-response
    failure is normalised into this error.

    The message must stay safe to log and to return to a client: it never
    contains the API key, the Authorization header, or the raw upstream body,
    because an upstream body can echo the request it was given.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
        code: str = ProviderErrorCode.PROVIDER_ERROR,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.code = code
        # How many HTTP attempts were made before giving up. Stage 4C records
        # this because retrying burns quota, so the cost of a failure is part of
        # the comparison between models.
        self.attempts = attempts


@dataclass(frozen=True)
class AgentCompletionRequest:
    system_prompt: str
    user_prompt: str
    response_schema: dict | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, object] = field(default_factory=dict)
    """Optional provider-specific request fields, merged into the JSON body.

    This exists so a gateway-only knob (for example a reasoning-effort hint)
    does not have to become a first-class concept on the generic provider
    contract. The core fields -- model, messages, temperature -- are always
    written after this mapping, so it cannot be used to smuggle a different
    model or prompt past the agent.
    """


@dataclass(frozen=True)
class LlmCompletion:
    """A model response plus the execution metrics Stage 4C will need.

    Token counts are optional because not every gateway returns a usage block,
    and a missing count must not be silently reported as zero.

    ``finish_reason`` is carried through so a response cut short by the token
    limit can be diagnosed as truncation rather than surfacing as a confusing
    JSON syntax error.
    """

    raw_text: str
    provider_name: str
    model_name: str | None
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    attempt_count: int = 1
    """HTTP attempts made for this completion. 1 means it succeeded first try.

    Recorded so the benchmark can report retry cost separately from model
    quality: a model that needs three attempts is more expensive to run than one
    that needs one, even when both eventually succeed.
    """

    @property
    def retry_count(self) -> int:
        """Attempts beyond the first. Never negative."""
        return max(self.attempt_count - 1, 0)

    @property
    def truncated(self) -> bool:
        """True when the provider stopped because the token limit was reached."""
        return self.finish_reason == "length"


@runtime_checkable
class LlmProvider(Protocol):
    """Minimal provider contract.

    Adding a provider means implementing `complete` in a new class. Neither
    RiderAdvocateAgent nor DriverAdvocateAgent needs to change.
    """

    name: str

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion: ...
