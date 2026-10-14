from __future__ import annotations

from app.agents.config import AgentConfigurationError, AgentSettings
from app.agents.provider import LlmProvider
from app.agents.providers.gemini_native import GeminiNativeProvider
from app.agents.providers.mock import MockLlmProvider
from app.agents.providers.openai_compatible import OpenAiCompatibleProvider
from app.agents.providers.tencent_tokenhub import TencentTokenHubProvider

SUPPORTED_PROVIDERS = (
    "mock",
    "openai_compatible",
    "tencent_tokenhub",
    "gemini_native",
)


def get_provider(settings: AgentSettings) -> LlmProvider:
    """Resolve the configured provider.

    Real mode validates configuration first and fails loudly. It never falls
    back to mock, because a silent fallback would present canned output as
    though a real model produced it.

    TokenHub is mapped onto the shared OpenAI-compatible transport rather than
    given a duplicate HTTP client: TokenHub speaks the same wire protocol, so
    only the label and the default base URL differ.

    ``gemini_native`` is a genuinely different transport, not a relabelling: it
    calls ``generateContent`` and expresses the response schema through Gemini's
    native mechanism. It exists because the OpenAI-compatible endpoint could not
    serve a structured-output request for this model.
    """
    settings.validate()
    from app.cloud_storage import storage_backend
    if storage_backend() == 'turso' and settings.mode != 'mock':
        raise AgentConfigurationError('Live Gemini is disabled for the cloud demo release')

    if settings.provider == "mock":
        return MockLlmProvider()

    if settings.provider == "tencent_tokenhub":
        _require_real_mode("tencent_tokenhub", settings)
        return TencentTokenHubProvider(
            base_url=settings.base_url,
            model=settings.model or "",
            api_key=settings.api_key or "",
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            retry_max_backoff_seconds=settings.retry_max_backoff_seconds,
        )

    if settings.provider == "gemini_native":
        _require_real_mode("gemini_native", settings)
        return GeminiNativeProvider(
            model=settings.model or "",
            api_key=settings.api_key or "",
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            retry_max_backoff_seconds=settings.retry_max_backoff_seconds,
        )

    if settings.provider == "openai_compatible":
        _require_real_mode("openai_compatible", settings)
        return OpenAiCompatibleProvider(
            base_url=settings.base_url or "",
            model=settings.model or "",
            api_key=settings.api_key or "",
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            retry_max_backoff_seconds=settings.retry_max_backoff_seconds,
        )

    raise AgentConfigurationError(
        f"Unsupported AGENT_PROVIDER {settings.provider!r}. "
        f"Supported values: {', '.join(SUPPORTED_PROVIDERS)}"
    )


def _require_real_mode(provider: str, settings: AgentSettings) -> None:
    if settings.mode != "real":
        raise AgentConfigurationError(
            f"{provider} provider requires AGENT_MODE=real "
            f"(currently AGENT_MODE={settings.mode})"
        )
