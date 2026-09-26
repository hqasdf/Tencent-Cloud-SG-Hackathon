from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class HunyuanConfig:
    api_key: str
    base_url: str
    model: str

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


def load_hunyuan_config() -> HunyuanConfig:
    return HunyuanConfig(
        api_key=os.environ.get("TENCENT_TOKENHUB_API_KEY", ""),
        base_url=os.environ.get(
            "TENCENT_TOKENHUB_BASE_URL", "https://tokenhub-intl.tencentcloudmaas.com/plan/v3"
        ),
        model=os.environ.get("TENCENT_MODEL", "hunyuan-turbos-latest"),
    )


@dataclass
class HunyuanChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class HunyuanResult:
    content: str
    ok: bool
    error: str | None = None


class HunyuanUnavailableError(RuntimeError):
    """Raised when the LLM provider is not configured or reachable."""

    def __init__(
        self,
        code: str = "LLM_UNAVAILABLE",
        message: str = "The AI interview service is not available.",
    ) -> None:
        self.code = code
        super().__init__(message)


class HunyuanClient:
    """Thin provider boundary around Tencent's Singapore TokenHub OpenAI-compatible endpoint.

    The base URL, API key, and model ID come from environment variables and are
    never exposed to the browser. When the key is missing the client raises
    ``HunyuanUnavailableError`` so the caller can return a safe fallback.
    """

    def __init__(self, config: HunyuanConfig | None = None, timeout: float = 30.0) -> None:
        self._config = config or load_hunyuan_config()
        self._timeout = timeout

    @property
    def is_available(self) -> bool:
        return self._config.is_configured

    def chat(self, messages: list[HunyuanChatMessage]) -> HunyuanResult:
        """Call the Hunyuan chat-completions endpoint and return raw text content."""
        if not self._config.is_configured:
            raise HunyuanUnavailableError()

        url = f"{self._config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self._config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0.3,
            "max_tokens": 1024,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.RequestError as error:
            raise HunyuanUnavailableError(
                message=f"Could not reach the AI service: {error}"
            ) from error

        if response.status_code != 200:
            raise HunyuanUnavailableError(
                message=f"AI service returned status {response.status_code}: {response.text[:300]}",
            )

        try:
            body = response.json()
        except ValueError as error:
            raise HunyuanUnavailableError(
                message="AI service returned non-JSON response."
            ) from error

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise HunyuanUnavailableError(
                message="AI service response did not contain a message."
            ) from error

        return HunyuanResult(content=content, ok=True)
