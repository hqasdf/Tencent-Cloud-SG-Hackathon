from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

PROMPT_VERSION = "advocate_prompts_v1"


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
    prompt_version: str = PROMPT_VERSION

    @classmethod
    def from_environment(cls) -> "AgentSettings":
        mode = (os.getenv("AGENT_MODE") or "mock").strip().lower()
        if mode not in ("mock", "real"):
            raise AgentConfigurationError(
                f"AGENT_MODE must be 'mock' or 'real', received {mode!r}"
            )
        provider = (os.getenv("AGENT_PROVIDER") or ("mock" if mode == "mock" else "")).strip()
        return cls(
            mode=mode,
            provider=provider or "mock",
            model=(os.getenv("AGENT_MODEL") or None),
            base_url=(os.getenv("AGENT_BASE_URL") or None),
            api_key=(os.getenv("AGENT_API_KEY") or None),
            timeout_seconds=_float_env("AGENT_TIMEOUT_SECONDS", 20.0),
            temperature=_float_env("AGENT_TEMPERATURE", 0.0),
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
                ("AGENT_BASE_URL", self.base_url),
                ("AGENT_API_KEY", self.api_key),
            )
            if not value
        ]
        if missing:
            raise AgentConfigurationError(
                "AGENT_MODE=real requires the following configuration: "
                + ", ".join(missing)
            )

    def describe(self) -> dict[str, object]:
        """Safe, secret-free description for health endpoints and metadata."""
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model if self.mode == "real" else None,
            "configured": self.mode == "mock" or self._real_mode_configured(),
        }

    def _real_mode_configured(self) -> bool:
        try:
            self.validate()
        except AgentConfigurationError:
            return False
        return True


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be a number, received {raw!r}") from error
