from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

PROMPT_VERSION = "advocate_prompts_v1"

# Documented aliases.
#
# AGENT_* is the authoritative advocate configuration. The TENCENT_TOKENHUB_*
# names are accepted ONLY as a fallback, and ONLY for the tencent_tokenhub
# provider, so a developer who already exports a TokenHub key does not have to
# duplicate it. This is an explicit, documented alias rather than a silent
# borrow: no other provider picks these up, and the model is deliberately NOT
# aliased because the intake pipeline uses TENCENT_MODEL for a different job.
TOKENHUB_API_KEY_ENV = "TENCENT_TOKENHUB_API_KEY"
TOKENHUB_BASE_URL_ENV = "TENCENT_TOKENHUB_BASE_URL"


class AgentConfigurationError(RuntimeError):
    """Raised when real agent mode is requested without usable configuration.

    This must never be swallowed. Silently falling back to mock would make the
    system appear to be running a real model when it is serving canned output.
    """


@dataclass(frozen=True)
class AgentSettings:
    mode: Literal["mock", "real"] = "mock"
    provider: str = "mock"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 20.0
    temperature: float = 0.0
    max_tokens: int = 2000
    max_retries: int = 0
    retry_backoff_seconds: float = 1.0
    retry_max_backoff_seconds: float = 20.0
    reasoning_effort: str | None = None
    prompt_version: str = PROMPT_VERSION

    @classmethod
    def from_environment(cls) -> "AgentSettings":
        mode = (os.getenv("AGENT_MODE") or "mock").strip().lower()
        if mode not in ("mock", "real"):
            raise AgentConfigurationError(
                f"AGENT_MODE must be 'mock' or 'real', received {mode!r}"
            )
        provider = (os.getenv("AGENT_PROVIDER") or ("mock" if mode == "mock" else "")).strip()
        provider = provider or "mock"
        return cls(
            mode=mode,
            provider=provider,
            model=_clean(os.getenv("AGENT_MODEL")),
            base_url=_resolve_base_url(provider),
            api_key=_resolve_api_key(provider),
            timeout_seconds=_float_env("AGENT_TIMEOUT_SECONDS", 20.0),
            temperature=_float_env("AGENT_TEMPERATURE", 0.0),
            max_tokens=_int_env("AGENT_MAX_TOKENS", 2000),
            max_retries=_int_env("AGENT_MAX_RETRIES", 0, minimum=0),
            retry_backoff_seconds=_float_env("AGENT_RETRY_BACKOFF_SECONDS", 1.0),
            retry_max_backoff_seconds=_float_env("AGENT_RETRY_MAX_BACKOFF_SECONDS", 20.0),
            reasoning_effort=_clean(os.getenv("AGENT_REASONING_EFFORT")),
        )

    def validate(self) -> None:
        """Fail loudly and specifically when real mode is not usable."""
        if self.mode != "real":
            return
        missing = [
            name
            for name, value in (
                ("AGENT_PROVIDER", self.provider if self.provider != "mock" else None),
                ("AGENT_MODEL", self.model),
                ("AGENT_API_KEY", self.api_key),
            )
            if not value
        ]
        # TokenHub has a documented default base URL, so AGENT_BASE_URL is only
        # mandatory for a provider that has no default.
        if self.provider != "tencent_tokenhub" and not self.base_url:
            missing.append("AGENT_BASE_URL")
        if missing:
            hint = ""
            if "AGENT_API_KEY" in missing and self.provider == "tencent_tokenhub":
                hint = f" (or set {TOKENHUB_API_KEY_ENV})"
            raise AgentConfigurationError(
                "AGENT_MODE=real requires the following configuration: "
                + ", ".join(missing)
                + hint
            )

    def describe(self) -> dict[str, object]:
        """Safe, secret-free description for health endpoints and metadata.

        Reports *whether* a credential is present, never its value or length.
        """
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model if self.mode == "real" else None,
            "configured": self.mode == "mock" or self._real_mode_configured(),
            "credential_configured": bool(self.api_key),
        }

    def _real_mode_configured(self) -> bool:
        try:
            self.validate()
        except AgentConfigurationError:
            return False
        return True


def _clean(raw: str | None) -> str | None:
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _resolve_api_key(provider: str) -> str | None:
    """AGENT_API_KEY wins; TokenHub falls back to its documented alias."""
    explicit = _clean(os.getenv("AGENT_API_KEY"))
    if explicit:
        return explicit
    if provider == "tencent_tokenhub":
        return _clean(os.getenv(TOKENHUB_API_KEY_ENV))
    return None


def _resolve_base_url(provider: str) -> str | None:
    """AGENT_BASE_URL wins; TokenHub falls back to its documented alias.

    ``None`` means "use the provider's own default", which is how the TokenHub
    Singapore endpoint is reached without hardcoding it at the call site.
    """
    explicit = _clean(os.getenv("AGENT_BASE_URL"))
    if explicit:
        return explicit
    if provider == "tencent_tokenhub":
        return _clean(os.getenv(TOKENHUB_BASE_URL_ENV))
    return None


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be a number, received {raw!r}") from error


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    """Read an integer setting.

    ``AGENT_MAX_TOKENS`` exists because a thinking model spends part of the
    completion budget on its reasoning, so a limit that suits a cloud model can
    truncate a local one. A truncated response is not a smaller response — it is
    an unusable one.

    ``minimum`` is 1 for a token budget but 0 for ``AGENT_MAX_RETRIES``, where
    zero ("do not retry") is a meaningful and default value.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be an integer, received {raw!r}") from error
    if value < minimum:
        raise AgentConfigurationError(f"{name} must be >= {minimum}, received {value}")
    return value
