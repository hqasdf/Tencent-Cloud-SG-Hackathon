from __future__ import annotations

from app.agents.config import AgentConfigurationError, AgentSettings
from app.agents.provider import LlmProvider
from app.agents.providers.mock import MockLlmProvider
from app.agents.providers.openai_compatible import OpenAiCompatibleProvider

SUPPORTED_PROVIDERS = ("mock", "openai_compatible")


def get_provider(settings: AgentSettings) -> LlmProvider:
    """Resolve the configured provider.

    Real mode validates configuration first and fails loudly. It never falls
    back to mock, because a silent fallback would present canned output as
    though a real model produced it.
    """
    settings.validate()

    if settings.provider == "mock":
        return MockLlmProvider()

    if settings.provider == "openai_compatible":
        if settings.mode != "real":
            raise AgentConfigurationError(
                "openai_compatible provider requires AGENT_MODE=real"
            )
        return OpenAiCompatibleProvider(
            base_url=settings.base_url or "",
            model=settings.model or "",
            api_key=settings.api_key or "",
            timeout_seconds=settings.timeout_seconds,
        )

    raise AgentConfigurationError(
        f"Unsupported AGENT_PROVIDER {settings.provider!r}. "
        f"Supported values: {', '.join(SUPPORTED_PROVIDERS)}"
    )
