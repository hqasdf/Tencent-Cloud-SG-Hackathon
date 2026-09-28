from __future__ import annotations

from typing import Callable

from app.agents.providers.openai_compatible import OpenAiCompatibleProvider

# Tencent TokenHub (international / Singapore region) exposes an
# OpenAI-compatible Chat Completions surface. Only the base URL and the
# provider label differ, so this provider reuses the shared transport rather
# than duplicating HTTP logic.
#
# The region matters: the mainland endpoint is a different host. This project
# targets the Singapore region, so it is the documented default.
DEFAULT_BASE_URL = "https://tokenhub-intl.tencentmaas.com/v1"


class TencentTokenHubProvider(OpenAiCompatibleProvider):
    """Tencent TokenHub, served through the shared OpenAI-compatible transport.

    Inherits ``complete``, ``list_models``, error normalisation, and usage
    parsing from ``OpenAiCompatibleProvider``. The only TokenHub-specific
    knowledge here is the provider name reported in metadata and the default
    base URL used when none is configured.

    No model is hardcoded: ``AGENT_MODEL`` selects the model, and availability
    is verified against ``GET /v1/models`` rather than assumed.
    """

    name = "tencent_tokenhub"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 20.0,
        max_retries: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_max_backoff_seconds: float = 20.0,
        client_factory: Callable[..., object] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(
            base_url=(base_url or "").strip() or DEFAULT_BASE_URL,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_max_backoff_seconds=retry_max_backoff_seconds,
            client_factory=client_factory,
            sleeper=sleeper,
        )
