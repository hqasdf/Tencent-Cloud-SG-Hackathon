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
native mechanism (``response_mime_type`` + ``response_json_schema``) instead of
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
import math
import random
import re
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

# Gemini accepts a documented subset of JSON Schema for native structured
# output. Pydantic remains the authoritative local validator; this allow-list is
# only a transport adapter that prevents unsupported annotations and constraints
# (for example ``default`` and ``minLength``) from making the provider reject an
# otherwise valid request.
_GEMINI_JSON_SCHEMA_KEYWORDS = frozenset(
    {
        "$id",
        "$defs",
        "$ref",
        "$anchor",
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "oneOf",
        "properties",
        "additionalProperties",
        "required",
        "propertyOrdering",
    }
)

_GEMINI_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "$defs"})
_GEMINI_SCHEMA_LIST_KEYWORDS = frozenset({"prefixItems", "anyOf", "oneOf"})

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


def gemini_response_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the documented Gemini JSON Schema subset for structured output.

    This transformation intentionally affects only the schema sent to Gemini.
    The original Pydantic model and the full provider-neutral schema remain
    unchanged and still validate the response after generation.
    """
    if not isinstance(schema, dict):
        raise TypeError("Gemini response schema must be an object")
    return _gemini_schema_node(schema)


def _gemini_schema_node(node: Any) -> Any:
    if isinstance(node, list):
        return [_gemini_schema_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    compatible: dict[str, Any] = {}
    for keyword, value in node.items():
        if keyword == "const":
            # Gemini supports enum, not const. Dropping a singleton Literal
            # silently removes constraints such as operator=EQUALS.
            compatible["enum"] = [value]
            continue
        if keyword not in _GEMINI_JSON_SCHEMA_KEYWORDS:
            continue
        if keyword in _GEMINI_SCHEMA_MAP_KEYWORDS:
            if isinstance(value, dict):
                compatible[keyword] = {
                    name: _gemini_schema_node(child)
                    for name, child in value.items()
                    if isinstance(child, (dict, bool))
                }
            continue
        if keyword in _GEMINI_SCHEMA_LIST_KEYWORDS:
            if isinstance(value, list):
                compatible[keyword] = [_gemini_schema_node(child) for child in value]
            continue
        if keyword == "items":
            if isinstance(value, (dict, bool)):
                compatible[keyword] = _gemini_schema_node(value)
            continue
        if keyword == "additionalProperties" and isinstance(value, dict):
            compatible[keyword] = _gemini_schema_node(value)
            continue
        compatible[keyword] = value
    return compatible


def _uses_gemini_38_generation_config(model: str) -> bool:
    """Whether ``model`` follows Gemini 3.8's reduced generation config."""
    normalised = model.strip().lower()
    if normalised.startswith("models/"):
        normalised = normalised.removeprefix("models/")
    return normalised == "gemini-3.8-flash" or normalised.startswith("gemini-3.8-flash-")


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
        jitter: Callable[[], float] | None = None,
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
        self._jitter = jitter or random.random

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
        instruction, model-compatible sampling fields, the output budget, the
        native structured-output pair, and the thinking level. Everything comes
        from the request, so the agent remains the single place that decides
        *what* to ask.
        """
        from google.genai import types

        response_schema = (
            gemini_response_json_schema(request.response_schema)
            if request.response_schema
            else None
        )
        return types.GenerateContentConfig(
            system_instruction=request.system_prompt or None,
            # Gemini 3.8 rejects the legacy sampling controls. Keep them for
            # other Gemini models, where callers still own the sampling policy.
            temperature=(
                None if _uses_gemini_38_generation_config(self._model) else request.temperature
            ),
            max_output_tokens=request.max_tokens,
            # The native structured-output pair. This is the mechanism the
            # OpenAI-compatible endpoint could not serve.
            response_mime_type="application/json" if response_schema else None,
            response_json_schema=response_schema,
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
        waited = 0.0
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
                failure.attempts = attempt
                failure.quota_details = [item for item in failure.quota_details
                    if not any(self._api_key in value for value in item.values())]
                if failure.retryable and attempt <= self._max_retries:
                    delay = self._retry_delay(failure, attempt)
                    if delay is not None and waited + delay <= self._retry_max_backoff_seconds:
                        self._sleep(delay)
                        waited += delay
                        continue
                raise failure from None  # Never render the SDK's full upstream body in a traceback.

            return self._to_completion(response, started, attempt)

    def _backoff_seconds(self, attempt: int) -> float:
        if self._retry_backoff_seconds <= 0:
            return 0.0
        delay = self._retry_backoff_seconds * (2 ** max(attempt - 1, 0))
        # Add bounded jitter; never shorten a provider's minimum retry delay.
        return min(delay * (1.0 + 0.25 * self._jitter()), self._retry_max_backoff_seconds)

    def _retry_delay(self, failure: LlmProviderError, attempt: int) -> float | None:
        recommended = failure.retry_after_seconds
        if failure.code == ProviderErrorCode.RATE_LIMITED and recommended is None:
            return None  # A quota window cannot be guessed from a 429.
        if recommended is not None:
            return max(recommended, self._backoff_seconds(attempt))
        return self._backoff_seconds(attempt)

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
            http_status=status,
            retry_after_seconds=_retry_guidance(error),
            quota_details=_quota_details(error) if status == 429 else [],
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


def _structured_details(error: BaseException) -> list[dict]:
    payload = getattr(error, "details", None)
    if isinstance(payload, dict):
        payload = payload.get("error", payload)
        payload = payload.get("details", []) if isinstance(payload, dict) else []
    return [item for item in payload[:16] if isinstance(item, dict)] if isinstance(payload, list) else []


def _duration_seconds(value: object) -> float | None:
    try:
        if isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?s", value):
            seconds = float(value[:-1])
        elif isinstance(value, dict) and ("seconds" in value or "nanos" in value):
            seconds = float(value.get("seconds", 0)) + float(value.get("nanos", 0)) / 1e9
        else:
            return None
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _retry_guidance(error: BaseException) -> float | None:
    delays = []
    for item in _structured_details(error):
        if item.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
            delay = _duration_seconds(item.get("retryDelay"))
            if delay is not None:
                delays.append(delay)
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is not None and hasattr(headers, "get"):
        delay = retry_after_seconds(headers.get("Retry-After"))
        if delay is not None:
            delays.append(delay)
    return max(delays) if delays else None


def _quota_details(error: BaseException) -> list[dict[str, str]]:
    """Keep bounded quota identifiers only; omit descriptions and dimensions."""
    result = []
    for item in _structured_details(error):
        if item.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure":
            continue
        violations = item.get("violations", [])
        if not isinstance(violations, list):
            continue
        for violation in violations[:8]:
            if not isinstance(violation, dict):
                continue
            safe = {}
            patterns = {
                "quotaMetric": r"generativelanguage\.googleapis\.com/generate_content[a-z0-9_]{0,100}",
                "quotaId": r"GenerateContent[A-Za-z0-9_-]{1,120}",
            }
            for key, pattern in patterns.items():
                value = violation.get(key)
                if isinstance(value, str) and re.fullmatch(pattern, value) and not re.search(r"AIza|sk-|Bearer", value):
                    safe[key] = value
            if safe:
                result.append(safe)
    return result[:8]


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
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        delay = float(text)
        return delay if math.isfinite(delay) and delay >= 0 else None
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
