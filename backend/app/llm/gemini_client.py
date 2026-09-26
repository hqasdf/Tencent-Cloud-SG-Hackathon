"""Google Gemini client using the google-genai SDK — free tier, no credit card.

Drop-in replacement for HunyuanClient: accepts HunyuanChatMessage lists and
returns HunyuanResult, so ConversationService needs no changes.
"""
from __future__ import annotations

import os
import warnings

# Silence the google-genai SDK's AFC recommendation warning — we use
# generate_content directly which is fine for our single-turn use case.
warnings.filterwarnings("ignore", message=".*automatic function calling.*")

from app.llm.hunyuan_client import (
    HunyuanChatMessage,
    HunyuanConfig,
    HunyuanResult,
    HunyuanUnavailableError,
)


def load_gemini_config() -> HunyuanConfig:
    return HunyuanConfig(
        api_key=os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")),
        base_url="",  # unused — SDK manages the endpoint
        model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
    )


# Fallback models to try if the primary model is overloaded (503).
_FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]


class GeminiClient:
    """Client for Google Gemini via the google-genai SDK."""

    def __init__(self, config: HunyuanConfig | None = None, timeout: float = 30.0) -> None:
        self._config = config or load_gemini_config()
        self._timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as error:
            raise HunyuanUnavailableError(
                message="google-genai package not installed. Run: pip install google-genai"
            ) from error

        self._client = genai.Client(api_key=self._config.api_key)
        return self._client

    @property
    def is_available(self) -> bool:
        return self._config.is_configured

    def chat(self, messages: list[HunyuanChatMessage]) -> HunyuanResult:
        if not self._config.is_configured:
            raise HunyuanUnavailableError()

        client = self._get_client()

        # Gemini separates system instruction from the contents list.
        system_parts = [m for m in messages if m.role == "system"]
        conversation = [m for m in messages if m.role != "system"]

        # Gemini requires multi-turn conversations to start with user and end
        # with user, alternating between user and model. Strip any leading
        # assistant turns and any trailing assistant turns so we never send a
        # conversation that Gemini considers to start or end with "model".
        while conversation and conversation[0].role in ("assistant", "model"):
            conversation.pop(0)
        while conversation and conversation[-1].role in ("assistant", "model"):
            conversation.pop()

        role_map = {"user": "user", "assistant": "model", "model": "model"}
        contents = [
            {"role": role_map.get(m.role, m.role), "parts": [{"text": m.content}]}
            for m in conversation
        ]

        system_instruction = " ".join(m.content for m in system_parts) if system_parts else None

        try:
            from google.genai import types as gtypes

            config = gtypes.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=1024,
                system_instruction=system_instruction,
            )
        except ImportError:
            config = None

        # Try primary model, then fall back to alternatives on 503/overloaded.
        models_to_try = [self._config.model] + [
            m for m in _FALLBACK_MODELS if m != self._config.model
        ]

        last_error: Exception | None = None
        for model_name in models_to_try:
            try:
                kwargs: dict = {"model": model_name, "contents": contents}
                if config is not None:
                    kwargs["config"] = config
                response = client.models.generate_content(**kwargs)
                last_error = None
                break
            except Exception as error:
                last_error = error
                err_str = str(error)
                # Only retry on 503 (overloaded) or 429 (rate limit).
                if "503" not in err_str and "429" not in err_str and "UNAVAILABLE" not in err_str:
                    break

        if last_error is not None:
            raise HunyuanUnavailableError(
                message=f"Gemini call failed: {last_error}"
            ) from last_error

        text = getattr(response, "text", None)
        if not text:
            try:
                text = response.candidates[0].content.parts[0].text
            except (AttributeError, IndexError, TypeError) as error:
                raise HunyuanUnavailableError(
                    message="Gemini response did not contain a message."
                ) from error

        return HunyuanResult(content=text, ok=True)
