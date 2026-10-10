"""Native Gemini provider: structured output through Google's own API.

Why this exists
---------------
The OpenAI-compatible endpoint was measured, and it cannot serve a
structured-output request for this model. A minimal request without
``response_format`` returns 200; the same request carrying
``response_format: json_schema`` returns 503 — and it still does when the
official OpenAI SDK builds the request, with ``strict``, its own headers and its
own serialization. So the failure is not our payload construction and not
capacity: it is that endpoint's structured-output path.

This provider therefore takes structured output off that endpoint entirely. It
calls ``generateContent`` directly and expresses the schema through Gemini's
native mechanism (``response_mime_type`` + ``response_schema``) instead of
OpenAI's ``response_format``.

What it deliberately does not do
--------------------------------
* **No repair.** Repair is one bounded attempt in ``complete_typed``, downstream
  of every provider. A provider that repaired would give the pipeline two
  different repair policies.
* **No retry of a model-output problem.** Only transport-level failures are
  retried, and only under the existing bounded policy.
* **No chain-of-thought.** Gemini bills thinking tokens and reports their count.
  They are *not* surfaced as reasoning: they land in ``unattributedTokens``,
  which is the accounting term this project already uses for tokens that were
  billed but are not part of the answer.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProviderError,
    ProviderErrorCode,
)

DEFAULT_TIMEOUT_SECONDS = 30.0

# Gemini's stop reasons, mapped onto the OpenAI spellings the rest of the system
# already speaks. ``LlmCompletion.truncated`` is defined as
# ``finish_reason == "length"``, so this mapping is what makes truncation
# detectable across providers instead of only on the OpenAI-compatible one.
_FINISH_REASON_ALIASES = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
}

# Our neutral knob is ``reasoning_effort``; Gemini's native name is
# ``thinking_level``. Only these three are meaningful, and only they are passed
# on — an unrecognised value is left to the provider's own default rather than
# guessed at.
_THINKING_LEVELS = frozenset({"low", "medium", "high"})

_RETRYABLE_CODES = frozenset(
    {
        ProviderErrorCode.RATE_LIMITED,
        ProviderErrorCode.TIMEOUT,
        ProviderErrorCode.NETWORK_ERROR,
        ProviderErrorCode.SERVER_ERROR,
    }
)


def _default_client_factory(*, api_key: str, timeout_seconds: float) -> Any:
    """A GenAI client whose own retry is disabled.

    ``attempts=1`` is deliberate: the SDK's retry would be invisible to us and
    would make the reported ``attempt_count`` a lie. Retries are the provider's
    job here, so they can be counted and bounded.
    """
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=int(timeout_seconds * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


class GeminiNativeProvider:
    """Structured output over Gemini's native ``generateContent``.

    Implements the same ``LlmProvider`` protocol as every other provider, so no
    agent knows which transport answered. ``client_factory`` exists so the
    transport can be replaced in tests: automated tests must never reach a live
    paid API.
    """

    name = "gemini_native"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_max_backoff_seconds: float = 20.0,
        name: str | None = None,
        client_factory: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not model or not api_key:
            raise LlmProviderError(
                "gemini_native provider requires model and api_key",
                provider=name or type(self).name,
                code=ProviderErrorCode.NOT_CONFIGURED,
            )
        self.name = name or type(self).name
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._retry_max_backoff_seconds = max(0.0, retry_max_backoff_seconds)
        self._client_factory = client_factory or _default_client_factory
        self._sleep = sleeper or time.sleep

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def timeout_seconds(self) -> float:
        """Exposed so a test can assert the 30 s ceiling is actually in force."""
        return self._timeout_seconds

    # -- request construction ------------------------------------------------

    def build_config(self, request: AgentCompletionRequest) -> Any:
        """The native request configuration. Public so a test can inspect it.

        This is where the whole provider contract is visible: system
        instruction, temperature, the output budget, the native structured-output
        pair, and the thinking level. Everything comes from the request, so the
        agent remains the single place that decides *what* to ask.
        """
        from google.genai import types

        return types.GenerateContentConfig(
            system_instruction=request.system_prompt or None,
            temperature=request.temperature,
            max_output_tokens=request.max_tokens,
            # The native structured-output pair. This is the mechanism the
            # OpenAI-compatible endpoint could not serve.
            response_mime_type="application/json" if request.response_schema else None,
            response_schema=request.response_schema,
            thinking_config=self._thinking_config(request),
        )

    def _thinking_config(self, request: AgentCompletionRequest) -> Any:
        level = request.extra_body.get("reasoning_effort")
        if not isinstance(level, str):
            return None
        normalised = level.strip().lower()
        if normalised not in _THINKING_LEVELS:
            return None
        from google.genai import types

        return types.ThinkingConfig(thinking_level=normalised)

    # -- the call ------------------------------------------------------------

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                client = self._client_factory(
                    api_key=self._api_key, timeout_seconds=self._timeout_seconds
                )
                response = client.models.generate_content(
                    model=self._model,
                    contents=request.user_prompt,
                    config=self.build_config(request),
                )
            except LlmProviderError:
                raise
            except Exception as error:  # noqa: BLE001 - cancellation/stop must propagate
                failure = _normalise(error, self.name)
                if failure.retryable and attempt <= self._max_retries:
                    self._sleep(self._backoff_seconds(attempt))
                    continue
                raise failure from error

            return self._to_completion(response, started, attempt)

    def _backoff_seconds(self, attempt: int) -> float:
        if self._retry_backoff_seconds <= 0:
            return 0.0
        delay = self._retry_backoff_seconds * (2 ** max(attempt - 1, 0))
        return min(delay, self._retry_max_backoff_seconds)

    def _to_completion(self, response: Any, started: float, attempt: int) -> LlmCompletion:
        finish_reason = _finish_reason(response)
        text = _extract_text(response, self.name)

        usage = getattr(response, "usage_metadata", None)
        input_tokens = _int_or_none(getattr(usage, "prompt_token_count", None))
        output_tokens = _int_or_none(getattr(usage, "candidates_token_count", None))
        total_tokens = _int_or_none(getattr(usage, "total_token_count", None))
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            # Only the two counts we actually have. Thought tokens are unknown
            # here, and inventing them would understate the total.
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

        return LlmCompletion(
            raw_text=text,
            provider_name=self.name,
            model_name=getattr(response, "model_version", None) or self._model,
            duration_ms=int((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            attempt_count=attempt,
        )


# ---------------------------------------------------------------------------
# Response reading
# ---------------------------------------------------------------------------


def _finish_reason(response: Any) -> str | None:
    """The stop reason in the OpenAI spelling the rest of the system uses."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    raw = getattr(candidates[0], "finish_reason", None)
    if raw is None:
        return None
    name = getattr(raw, "name", None) or str(raw)
    return _FINISH_REASON_ALIASES.get(name, name.lower())


def _extract_text(response: Any, provider_name: str) -> str:
    """The candidate's text.

    An empty answer is an ``EMPTY_CONTENT`` failure, not a valid empty string:
    every agent here is asked for a JSON object, so a blank response is a
    provider-side outcome to be named rather than a value to be parsed.

    Text is returned even when the response was cut off by the token limit. That
    is not an oversight: truncation is reported through ``finish_reason`` so the
    existing unrepairable ``OUTPUT_TRUNCATED`` path handles it, rather than the
    provider inventing a second, parallel way to reject it.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise LlmProviderError(
            f"{provider_name} returned no candidates",
            provider=provider_name,
            code=ProviderErrorCode.MALFORMED_RESPONSE,
        )
    text = getattr(response, "text", None)
    if text is None:
        parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
        text = "".join(
            part.text for part in parts if isinstance(getattr(part, "text", None), str)
        )
    if not isinstance(text, str) or not text.strip():
        raise LlmProviderError(
            f"{provider_name} returned an empty candidate",
            provider=provider_name,
            code=ProviderErrorCode.EMPTY_CONTENT,
        )
    return text


# ---------------------------------------------------------------------------
# Error normalisation
# ---------------------------------------------------------------------------


def _normalise(error: BaseException, provider_name: str) -> LlmProviderError:
    """Map a native failure onto the shared provider vocabulary.

    The message is built from the status and the class of failure only. The
    upstream body is never included: a provider body can echo the request it was
    given, and this message is safe to log and to return to a client.
    """
    status = _http_status(error)
    if status is not None:
        code, retryable = _classify(status)
        return LlmProviderError(
            f"{provider_name} returned HTTP {status} ({code})",
            provider=provider_name,
            retryable=retryable,
            code=code,
        )

    if _is_timeout(error):
        return LlmProviderError(
            f"{provider_name} request timed out",
            provider=provider_name,
            retryable=True,
            code=ProviderErrorCode.TIMEOUT,
        )

    if isinstance(error, (TypeError, ValueError, KeyError, AttributeError)):
        # A malformed response that the SDK could not decode into its own model.
        return LlmProviderError(
            f"{provider_name} returned a response that could not be read "
            f"({type(error).__name__})",
            provider=provider_name,
            code=ProviderErrorCode.MALFORMED_RESPONSE,
        )

    return LlmProviderError(
        f"{provider_name} request failed: {type(error).__name__}",
        provider=provider_name,
        retryable=True,
        code=ProviderErrorCode.NETWORK_ERROR,
    )


def _http_status(error: BaseException) -> int | None:
    code = getattr(error, "code", None)
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    # The SDK uses ``code`` for an HTTP status on APIError. A non-HTTP value
    # would not be in a plausible range, so it is not treated as one.
    if 100 <= code <= 599:
        return code
    return None


def _is_timeout(error: BaseException) -> bool:
    if "timeout" in type(error).__name__.lower():
        return True
    try:  # pragma: no cover - depends on which httpx the SDK bundled
        import httpx

        if isinstance(error, httpx.TimeoutException):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:  # pragma: no cover
        import httpx2

        if isinstance(error, httpx2.TimeoutException):
            return True
    except ImportError:  # pragma: no cover
        pass
    return False


def _classify(status: int) -> tuple[str, bool]:
    if status == 400:
        return ProviderErrorCode.REQUEST_REJECTED, False
    if status == 401:
        return ProviderErrorCode.AUTHENTICATION_FAILED, False
    if status == 403:
        return ProviderErrorCode.ACCESS_DENIED, False
    if status == 404:
        return ProviderErrorCode.MODEL_NOT_FOUND, False
    if status == 429:
        return ProviderErrorCode.RATE_LIMITED, True
    if status >= 500:
        return ProviderErrorCode.SERVER_ERROR, True
    return ProviderErrorCode.REQUEST_REJECTED, False


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def retry_after_seconds(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header in either documented form.

    A local copy of the rule the OpenAI-compatible provider uses, because that
    one is private to a module this change must not touch. Kept identical in
    behaviour: an unparseable or absent header means ``None``, which the caller
    treats as "do not retry" rather than guessing a delay.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(float(text), 0.0)
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max((moment - datetime.now(timezone.utc)).total_seconds(), 0.0)


__all__ = ["GeminiNativeProvider", "retry_after_seconds"]
