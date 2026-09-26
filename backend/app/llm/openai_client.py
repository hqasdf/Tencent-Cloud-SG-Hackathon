from __future__ import annotations

import os

import httpx

from app.llm.hunyuan_client import (
    HunyuanChatMessage,
    HunyuanConfig,
    HunyuanResult,
    HunyuanUnavailableError,
)


def load_openai_config() -> HunyuanConfig:
    """OpenAI configuration reusing the HunyuanConfig dataclass (same fields)."""
    return HunyuanConfig(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    )


class OpenAIClient:
    """Drop-in replacement for HunyuanClient that calls any OpenAI-compatible
    /chat/completions endpoint (OpenAI, Azure, Groq, Together, etc.).
    """

    def __init__(self, config: HunyuanConfig | None = None, timeout: float = 30.0) -> None:
        self._config = config or load_openai_config()
        self._timeout = timeout

    @property
    def is_available(self) -> bool:
        return bool(self._config.api_key)

    def chat(self, messages: list[HunyuanChatMessage]) -> HunyuanResult:
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
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise HunyuanUnavailableError(
                message="AI service response did not contain a message."
            ) from error

        return HunyuanResult(content=content, ok=True)