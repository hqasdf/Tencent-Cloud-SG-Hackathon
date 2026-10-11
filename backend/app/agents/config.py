from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

PROMPT_VERSION = "advocate_prompts_v3_atomic_propositions"

# Bounded defaults for a typed agent call.
#
# These are not arbitrary. Each one exists to make a call finish inside a
# predictable envelope so a provider hiccup costs one stage and not a session:
#
# * 30 s, because the brief's ceiling is 30 and a request that cannot complete in
#    that time is better failed cleanly than waited on. A longer wait does not
#    make a struggling provider succeed; it makes the run slower to fail.
# * 1024 output tokens, because these are typed decisions, not essays. Gemini
#    bills thinking tokens against this same budget, so the budget has to cover
#    the reasoning *and* the JSON — which is why the prompts ask for short
#    evidence-grounded summaries rather than prose.
# * "low" reasoning, because the equivalent of ReStock's ``think = false`` for a
#    model that cannot disable reasoning entirely. ReStock only needs the final
#    structured decision; so do we.
#
# Changing any of these is a deliberate act with a measurement behind it. They
# are ceilings, not targets: a stage that needs more is a prompt or schema
# problem to diagnose, not a number to raise.
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 30.0
"""Hard ceiling. ``Do not silently extend the timeout`` is enforced, not promised."""

DEFAULT_MAX_TOKENS = 1024
MAX_OUTPUT_TOKENS = 1024
"""Hard ceiling for a typed agent call's generation budget."""

DEFAULT_REASONING_EFFORT = "low"
SUPPORTED_REASONING_EFFORTS = frozenset({"low", "medium", "high"})

# Documented aliases.
#
# AGENT_* is the authoritative advocate configuration. The TENCENT_TOKENHUB_*
# names are accepted ONLY as a fallback, and ONLY for the tencent_tokenhub
# provider, so a developer who already exports a TokenHub key does not have to
# duplicate it. This is an explicit, documented alias rather than a silent
# borrow: no other provider picks these up, and AGENT_MODEL remains authoritative.
TOKENHUB_API_KEY_ENV = "TENCENT_TOKENHUB_API_KEY"
TOKENHUB_BASE_URL_ENV = "TENCENT_TOKENHUB_BASE_URL"

PROVIDERS_WITHOUT_BASE_URL = frozenset({"tencent_tokenhub", "gemini_native"})
"""Providers that reach their own documented endpoint and take no base URL.

``tencent_tokenhub`` has a documented default. ``gemini_native`` addresses
Google's API directly and ignores a base URL entirely, so demanding one would be
demanding a value that is not in force — the same quiet mismatch this project
refuses elsewhere.
"""


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
    # ``repr=False`` is a deliberate leak guard, not tidiness. A dataclass reprs
    # every field, so any ``logger.info("settings=%s", settings)``, any
    # traceback that includes a settings object in a frame's locals, and any
    # test failure message that prints one would have written the credential to
    # a log. ``describe()`` is the safe way to report this object.
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    temperature: float = 0.0
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_retries: int = 0
    retry_backoff_seconds: float = 1.0
    retry_max_backoff_seconds: float = 20.0
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT
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
            timeout_seconds=_bounded_float_env(
                "AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, maximum=MAX_TIMEOUT_SECONDS
            ),
            temperature=_float_env("AGENT_TEMPERATURE", 0.0),
            max_tokens=_bounded_int_env(
                "AGENT_MAX_TOKENS", DEFAULT_MAX_TOKENS, maximum=MAX_OUTPUT_TOKENS
            ),
            max_retries=_int_env("AGENT_MAX_RETRIES", 0, minimum=0),
            retry_backoff_seconds=_float_env("AGENT_RETRY_BACKOFF_SECONDS", 1.0),
            retry_max_backoff_seconds=_float_env("AGENT_RETRY_MAX_BACKOFF_SECONDS", 20.0),
            reasoning_effort=_reasoning_effort_env(),
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
        if self.provider not in PROVIDERS_WITHOUT_BASE_URL and not self.base_url:
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


def _bounded_float_env(name: str, default: float, *, maximum: float) -> float:
    """Read a float that must not exceed a hard ceiling.

    Rejected rather than clamped. Clamping a misconfigured value would leave the
    operator believing the setting they wrote is in force, which is exactly the
    kind of quiet divergence between configuration and behaviour this project
    refuses elsewhere. A value above the ceiling is a mistake, and the error
    names the ceiling so the fix is obvious.
    """
    value = _float_env(name, default)
    if value <= 0:
        raise AgentConfigurationError(f"{name} must be > 0, received {value}")
    if value > maximum:
        raise AgentConfigurationError(
            f"{name} must be <= {maximum:g} (the bounded agent-call ceiling), "
            f"received {value:g}"
        )
    return value


def _bounded_int_env(name: str, default: int, *, maximum: int) -> int:
    """Read an integer that must not exceed a hard ceiling. Rejected, not clamped."""
    value = _int_env(name, default)
    if value > maximum:
        raise AgentConfigurationError(
            f"{name} must be <= {maximum} (the bounded typed-output ceiling), "
            f"received {value}"
        )
    return value


def _reasoning_effort_env() -> str | None:
    """Reasoning effort for a typed call, defaulting to ``low``.

    Defaults to low rather than to unset, because unset means the provider's own
    default — which for this model is a medium effort that spends output budget
    on deliberation a typed decision does not need. An explicit empty value
    (``AGENT_REASONING_EFFORT=``) is honoured as "let the provider decide", so
    the default can be stepped back from without a code change.
    """
    raw = os.getenv("AGENT_REASONING_EFFORT")
    if raw is None:
        return DEFAULT_REASONING_EFFORT
    value = raw.strip().lower()
    if not value:
        return None
    if value not in SUPPORTED_REASONING_EFFORTS:
        raise AgentConfigurationError(
            "AGENT_REASONING_EFFORT must be one of "
            f"{', '.join(sorted(SUPPORTED_REASONING_EFFORTS))} (or empty to defer "
            f"to the provider), received {value!r}"
        )
    return value


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    """Read an integer setting.

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
