from __future__ import annotations

import json
import time

from app.agents.provider import AgentCompletionRequest, LlmCompletion, LlmProviderError


class OpenAiCompatibleProvider:
    """Generic OpenAI-compatible chat-completions provider.

    Deliberately provider-agnostic: base URL, model, and key come from
    configuration. This covers OpenAI-compatible gateways without committing
    the project to one vendor, and without guessing any vendor-specific
    endpoint or auth scheme.

    A provider with a genuinely different request shape belongs in its own
    module behind the same LlmProvider protocol.
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
    ) -> None:
        if not base_url or not model or not api_key:
            raise LlmProviderError(
                "openai_compatible provider requires base_url, model, and api_key",
                provider=self.name,
            )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, min(max_retries, 2))

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        import httpx

        started = time.monotonic()
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        attempt = 0
        while True:
            attempt += 1
            try:
                with httpx.Client(timeout=self._timeout_seconds) as client:
                    response = client.post(
                        f"{self._base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
            except Exception as error:  # noqa: BLE001 - normalised below
                if attempt > self._max_retries:
                    raise LlmProviderError(
                        f"{self.name} request failed: {type(error).__name__}",
                        provider=self.name,
                        retryable=True,
                    ) from error
                continue

            if response.status_code >= 400:
                retryable = response.status_code >= 500 or response.status_code == 429
                if retryable and attempt <= self._max_retries:
                    continue
                # Never surface the response body: it may echo request headers.
                raise LlmProviderError(
                    f"{self.name} returned HTTP {response.status_code}",
                    provider=self.name,
                    retryable=retryable,
                )

            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise TypeError("message content was not a string")
            except Exception as error:  # noqa: BLE001 - normalised below
                raise LlmProviderError(
                    f"{self.name} returned an unreadable response",
                    provider=self.name,
                ) from error

            return LlmCompletion(
                raw_text=content,
                provider_name=self.name,
                model_name=self._model,
                duration_ms=int((time.monotonic() - started) * 1000),
            )


def parse_json_object(raw_text: str) -> dict:
    """Parse a JSON object from a model response, tolerating code fences."""
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
