from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import httpx

from app.agents.provider import (
    AgentCompletionRequest,
    LlmCompletion,
    LlmProviderError,
    ProviderErrorCode,
)


def _default_client_factory(**kwargs: object) -> httpx.Client:
    return httpx.Client(**kwargs)  # type: ignore[arg-type]


# Fields the agent owns. `extra_body` may add gateway knobs but must never be
# able to redefine these, otherwise a configuration value could silently change
# which model answers or what it was asked.
_RESERVED_PAYLOAD_KEYS = frozenset(
    {"model", "messages", "temperature", "response_format", "max_tokens", "stream"}
)

# JSON Schema keywords that describe the *document* rather than the value it
# constrains. A provider that maps this schema onto its own native response
# schema has no use for them, and the native subset does not list them.
_SCHEMA_META_KEYWORDS = frozenset({"$schema", "$id", "$comment"})

# The schema name the provider requires alongside a structured-output schema.
# Preferred from the schema's own title, because that is the name a human wrote.
_DEFAULT_SCHEMA_NAME = "ryderesolve_output"


def provider_schema(schema: object) -> object:
    """Project a JSON Schema onto the subset a native response schema supports.

    Two transforms, both semantics-preserving — the constraint the model is held
    to is the same one Pydantic will enforce afterwards, so this cannot loosen
    the contract:

    * **Document meta keywords are dropped.** ``$schema``/``$id``/``$comment``
      describe the schema document, not the value. They are not in the supported
      subset and some providers reject a schema that carries them.
    * **A bare multi-type array becomes ``anyOf``.** ``{"type": ["number",
      "boolean"]}`` is valid JSON Schema but is *not* in the documented subset,
      which only shows the type-array form combined with ``null``. ``anyOf`` is
      documented and means exactly the same thing. The ``["<type>", "null"]``
      form is left alone, because that one *is* documented.

    This is a projection, not a redefinition: it is applied to the outbound
    request only. The schema the response is validated against is untouched, and
    it remains the authority.
    """
    if isinstance(schema, list):
        return [provider_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    types = schema.get("type")
    widened = (
        isinstance(types, list) and len(types) > 1 and "null" not in types
    )

    projected: dict = {}
    if widened:
        projected["anyOf"] = [{"type": kind} for kind in types]
    for key, value in schema.items():
        if key in _SCHEMA_META_KEYWORDS:
            continue
        if widened and key == "type":
            # Already expressed as anyOf above; keeping both would be ambiguous.
            continue
        projected[key] = provider_schema(value)
    return projected


def build_response_format(response_schema: dict | None) -> dict:
    """The structured-output constraint for one request.

    When the agent supplies a schema, the real schema is sent as the provider's
    constraint — not merely described in the prompt. A prompt that says "respond
    with this shape" is a request; a schema attached to the request is a
    constraint the provider enforces while generating, which is why a model
    cannot quietly drift out of shape on the first token.

    With no schema, the weakest correct constraint is used: any JSON object.
    """
    if not response_schema:
        return {"type": "json_object"}
    name = response_schema.get("title")
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name if isinstance(name, str) and name else _DEFAULT_SCHEMA_NAME,
            "schema": provider_schema(response_schema),
        },
    }


class OpenAiCompatibleProvider:
    """Generic OpenAI-compatible chat-completions provider.

    Deliberately provider-agnostic: base URL, model, and key come from
    configuration. This covers OpenAI-compatible gateways without committing
    the project to one vendor, and without guessing any vendor-specific
    endpoint or auth scheme.

    A provider with a genuinely different request shape belongs in its own
    module behind the same LlmProvider protocol.

    `client_factory` exists so the transport can be replaced with an offline
    stub in tests. Automated tests must never reach a live paid API.
    """

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 20.0,
        max_retries: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_max_backoff_seconds: float = 20.0,
        name: str | None = None,
        client_factory: Callable[..., object] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not base_url or not model or not api_key:
            raise LlmProviderError(
                "openai_compatible provider requires base_url, model, and api_key",
                provider=name or type(self).name,
                code=ProviderErrorCode.NOT_CONFIGURED,
            )
        # Subclasses declare their own `name`, so metadata reports the provider
        # the operator actually configured rather than the transport it reuses.
        self.name = name or type(self).name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, min(max_retries, 2))
        # Backoff exists so a retry is not simply a second wasted request. A
        # demand spike needs time to clear; hammering it immediately burns
        # quota and still fails.
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._retry_max_backoff_seconds = max(0.0, retry_max_backoff_seconds)
        self._client_factory = client_factory or _default_client_factory
        # Injectable so tests can assert the backoff schedule without waiting.
        self._sleep = sleeper or time.sleep

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        """Safe to expose: it is configuration, not a secret."""
        return self._base_url

    def _auth_headers(self) -> dict[str, str]:
        """The ONLY place the key is materialised. Never logged, never returned."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        started = time.monotonic()
        # Provider-specific extras go in first so the core fields below always
        # win. A gateway knob must never be able to replace the model, the
        # messages, or the temperature the agent chose.
        payload: dict[str, object] = {}
        for key, value in request.extra_body.items():
            if key in _RESERVED_PAYLOAD_KEYS:
                raise LlmProviderError(
                    f"{self.name} request tried to override the reserved field {key!r}",
                    provider=self.name,
                    code=ProviderErrorCode.REQUEST_REJECTED,
                )
            payload[key] = value

        payload["model"] = self._model
        payload["messages"] = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
        payload["temperature"] = request.temperature
        # The agent's own schema, where it supplied one. This is the difference
        # between asking for a shape and enforcing it.
        payload["response_format"] = build_response_format(request.response_schema)
        # Stated rather than implied. A streamed response cannot be validated as
        # one object, and a gateway that defaulted to streaming would turn every
        # typed call into a partial-JSON failure.
        payload["stream"] = False
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        attempt = 0
        while True:
            attempt += 1
            try:
                with self._client_factory(timeout=self._timeout_seconds) as client:
                    response = client.post(  # type: ignore[attr-defined]
                        f"{self._base_url}/chat/completions",
                        headers=self._auth_headers(),
                        json=payload,
                    )
            except httpx.TimeoutException as error:
                if attempt <= self._max_retries:
                    self._sleep(self._backoff_seconds(attempt))
                    continue
                raise LlmProviderError(
                    f"{self.name} request timed out after {self._timeout_seconds:g}s",
                    provider=self.name,
                    retryable=True,
                    code=ProviderErrorCode.TIMEOUT,
                    attempts=attempt,
                ) from error
            except Exception as error:  # noqa: BLE001 - normalised below
                if attempt <= self._max_retries:
                    self._sleep(self._backoff_seconds(attempt))
                    continue
                raise LlmProviderError(
                    f"{self.name} request failed: {type(error).__name__}",
                    provider=self.name,
                    retryable=True,
                    code=ProviderErrorCode.NETWORK_ERROR,
                    attempts=attempt,
                ) from error

            if response.status_code >= 400:
                code, retryable = _classify_http_status(response.status_code)
                delay = self._retry_delay(response, attempt) if retryable else None
                if delay is not None and attempt <= self._max_retries:
                    self._sleep(delay)
                    continue
                # Never surface the response body: it may echo request headers.
                raise LlmProviderError(
                    f"{self.name} returned HTTP {response.status_code} ({code})",
                    provider=self.name,
                    retryable=retryable,
                    code=code,
                    attempts=attempt,
                )

            body = self._read_json_body(response)
            content = _extract_content(body, self.name)
            input_tokens, output_tokens, total_tokens = _extract_usage(body)

            return LlmCompletion(
                raw_text=content,
                provider_name=self.name,
                model_name=self._model,
                duration_ms=int((time.monotonic() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                finish_reason=_extract_finish_reason(body),
                attempt_count=attempt,
            )

    def _backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff for attempt N, capped.

        A demand spike needs time to clear. Retrying immediately spends a second
        request against the same overloaded endpoint and usually fails the same
        way, which is the worst of both worlds: cost without progress.
        """
        if self._retry_backoff_seconds <= 0:
            return 0.0
        delay = self._retry_backoff_seconds * (2 ** max(attempt - 1, 0))
        return min(delay, self._retry_max_backoff_seconds)

    def _retry_delay(self, response: object, attempt: int) -> float | None:
        """Seconds to wait before retrying, or ``None`` when retrying is unsafe.

        A rate limit is only worth retrying when the server states when to come
        back. Without ``Retry-After`` there is no basis for choosing a delay, so
        the request fails safely instead of spending more of an already
        exhausted quota.
        """
        if getattr(response, "status_code", None) == 429:
            retry_after = _parse_retry_after(_header(response, "Retry-After"))
            if retry_after is None:
                return None
            return min(retry_after, self._retry_max_backoff_seconds)
        return self._backoff_seconds(attempt)

    def list_models(self) -> list[str]:
        """GET /models — a minimal, cheap connectivity and credential check.

        Used to verify the provider is reachable and that the configured
        AGENT_MODEL is actually available to this account before spending
        tokens on a real advocate run.

        Only model identifiers are returned. The raw body is never logged,
        because a gateway error body can echo the request headers.
        """
        try:
            with self._client_factory(timeout=self._timeout_seconds) as client:
                response = client.get(  # type: ignore[attr-defined]
                    f"{self._base_url}/models",
                    headers=self._auth_headers(),
                )
        except httpx.TimeoutException as error:
            raise LlmProviderError(
                f"{self.name} model listing timed out after {self._timeout_seconds:g}s",
                provider=self.name,
                retryable=True,
                code=ProviderErrorCode.TIMEOUT,
            ) from error
        except Exception as error:  # noqa: BLE001 - normalised below
            raise LlmProviderError(
                f"{self.name} model listing failed: {type(error).__name__}",
                provider=self.name,
                retryable=True,
                code=ProviderErrorCode.NETWORK_ERROR,
            ) from error

        if response.status_code >= 400:
            code, retryable = _classify_http_status(response.status_code)
            raise LlmProviderError(
                f"{self.name} model listing returned HTTP {response.status_code} ({code})",
                provider=self.name,
                retryable=retryable,
                code=code,
            )

        body = self._read_json_body(response)
        data = body.get("data")
        if not isinstance(data, list):
            raise LlmProviderError(
                f"{self.name} model listing had no model array",
                provider=self.name,
                code=ProviderErrorCode.MALFORMED_RESPONSE,
            )
        return [
            entry["id"]
            for entry in data
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        ]

    def _read_json_body(self, response: object) -> dict:
        try:
            body = response.json()  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - normalised below
            raise LlmProviderError(
                f"{self.name} returned a body that was not JSON",
                provider=self.name,
                code=ProviderErrorCode.MALFORMED_RESPONSE,
            ) from error
        if not isinstance(body, dict):
            raise LlmProviderError(
                f"{self.name} returned a JSON body that was not an object",
                provider=self.name,
                code=ProviderErrorCode.MALFORMED_RESPONSE,
            )
        return body


def normalise_model_id(model_id: str) -> str:
    """Strip a leading ``models/`` so the two naming conventions compare equal.

    Gemini lists identifiers in its native form (``models/gemini-3.8-flash``)
    even through the OpenAI-compatible ``/models`` route, while a configured
    model is conventionally written bare (``gemini-3.8-flash``). Comparing the
    raw strings reports a model as unavailable when it is right there.

    Shared by the provider diagnostics and the Stage 4C benchmark so the two
    cannot drift apart.
    """
    return model_id[7:] if model_id.startswith("models/") else model_id


def _classify_http_status(status_code: int) -> tuple[str, bool]:
    """Map an HTTP status onto a provider error code and a retry decision."""
    if status_code == 401:
        return ProviderErrorCode.AUTHENTICATION_FAILED, False
    if status_code == 403:
        return ProviderErrorCode.ACCESS_DENIED, False
    if status_code == 404:
        return ProviderErrorCode.MODEL_NOT_FOUND, False
    if status_code == 429:
        return ProviderErrorCode.RATE_LIMITED, True
    if status_code >= 500:
        return ProviderErrorCode.SERVER_ERROR, True
    return ProviderErrorCode.REQUEST_REJECTED, False


def _header(response: object, name: str) -> str | None:
    """Read a response header case-insensitively.

    httpx.Headers is already case-insensitive, but the test stubs use a plain
    dict, and a plain dict lookup would silently miss ``retry-after``.
    """
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    if value is None:
        for key, candidate in getattr(headers, "items", lambda: [])():
            if isinstance(key, str) and key.lower() == name.lower():
                value = candidate
                break
    return value if isinstance(value, str) else None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header in either documented form.

    RFC 9110 allows both delay-seconds and an HTTP-date. Returns ``None`` when
    the header is absent or unparseable, which the caller treats as "do not
    retry" rather than guessing a delay.
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


def _extract_content(body: dict, provider_name: str) -> str:
    """Pull the assistant message out of a chat-completions body."""
    try:
        choices = body["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LlmProviderError(
            f"{provider_name} returned a response with no assistant message",
            provider=provider_name,
            code=ProviderErrorCode.MALFORMED_RESPONSE,
        ) from error
    if not isinstance(content, str):
        raise LlmProviderError(
            f"{provider_name} returned a non-text assistant message",
            provider=provider_name,
            code=ProviderErrorCode.MALFORMED_RESPONSE,
        )
    if not content.strip():
        raise LlmProviderError(
            f"{provider_name} returned an empty assistant message",
            provider=provider_name,
            code=ProviderErrorCode.EMPTY_CONTENT,
        )
    return content


def _extract_finish_reason(body: dict) -> str | None:
    """Read the stop reason, which is how truncation is detected.

    ``"length"`` means the model was cut off by max_tokens. Without this, a
    truncated response is indistinguishable from a model that simply cannot
    produce valid JSON — a very different problem with a very different fix.
    """
    try:
        reason = body["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return reason if isinstance(reason, str) else None


def _extract_usage(body: dict) -> tuple[int | None, int | None, int | None]:
    """Read token usage, tolerating both the OpenAI and the newer naming.

    A missing usage block yields ``(None, None, None)`` rather than zeros, so a
    gateway that does not report usage is never mistaken for one that reported
    zero tokens.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    input_tokens = _int_or_none(usage.get("prompt_tokens", usage.get("input_tokens")))
    output_tokens = _int_or_none(usage.get("completion_tokens", usage.get("output_tokens")))
    total_tokens = _int_or_none(usage.get("total_tokens"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return input_tokens, output_tokens, total_tokens


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_json_object(raw_text: str) -> dict:
    """Parse a JSON object from a model response, tolerating code fences.

    Real models frequently wrap JSON in ```json fences even when asked for a
    bare object, so a fence is stripped rather than treated as a failure. The
    low-level helper raises ``ValueError``; the provider boundary normalises
    it into ``LlmProviderError``.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Model response was not valid JSON: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise ValueError("Model response must be a JSON object")
    return parsed
