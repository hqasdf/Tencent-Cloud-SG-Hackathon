"""Safe, minimal provider connectivity verification.

Purpose: before spending tokens on a real advocate run, confirm that

  * the provider is reachable,
  * the configured credential authenticates,
  * the configured AGENT_MODEL is actually available to this account.

Run it as::

    python -m app.agents.providers.diagnostics

or, to also print the available model identifiers::

    python -m app.agents.providers.diagnostics --list

Design constraints, all deliberate:

  * It uses ``GET /models``, which is the cheapest call an OpenAI-compatible
    gateway exposes and costs no completion tokens.
  * It NEVER prints the API key, the Authorization header, or the raw response
    body. A gateway error body can echo the request headers it received, so the
    body is never surfaced.
  * When no credential is configured it reports CREDENTIAL_REQUIRED. It does
    not fabricate a successful live test, and it does not fall back to mock.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from app.agents.config import AgentConfigurationError, AgentSettings, TOKENHUB_API_KEY_ENV
from app.agents.provider import LlmProviderError, ProviderErrorCode
from app.agents.providers.registry import get_provider
from app.agents.providers.tencent_tokenhub import DEFAULT_BASE_URL

STATUS_OK = "OK"
STATUS_MOCK_MODE = "MOCK_MODE"
STATUS_CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"
STATUS_NOT_CONFIGURED = "NOT_CONFIGURED"
STATUS_AUTH_FAILED = "AUTH_FAILED"
STATUS_ACCESS_DENIED = "ACCESS_DENIED"
STATUS_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
STATUS_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
STATUS_UNREACHABLE = "UNREACHABLE"

_CREDENTIAL_REQUIRED_NOTE = (
    "REAL PROVIDER READY — CREDENTIAL REQUIRED FOR LIVE VALIDATION"
)


@dataclass(frozen=True)
class ProviderDiagnostic:
    """A secret-free report of whether a live provider is usable."""

    status: str
    message: str
    mode: str
    provider: str
    base_url: str | None = None
    model_configured: str | None = None
    credential_configured: bool = False
    model_count: int | None = None
    model_available: bool | None = None
    models: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "mode": self.mode,
            "provider": self.provider,
            "base_url": self.base_url,
            "model_configured": self.model_configured,
            "credential_configured": self.credential_configured,
            "model_count": self.model_count,
            "model_available": self.model_available,
        }


def verify_provider_access(
    settings: AgentSettings | None = None,
    *,
    include_models: bool = False,
) -> ProviderDiagnostic:
    """Perform a minimal, cheap liveness and credential check."""
    settings = settings or AgentSettings.from_environment()

    if settings.mode != "real":
        return ProviderDiagnostic(
            status=STATUS_MOCK_MODE,
            message=(
                "AGENT_MODE=mock. No live provider is contacted and no credential is needed. "
                "Set AGENT_MODE=real to validate a real provider."
            ),
            mode=settings.mode,
            provider=settings.provider,
        )

    try:
        settings.validate()
    except AgentConfigurationError as error:
        return ProviderDiagnostic(
            status=STATUS_CREDENTIAL_REQUIRED,
            message=f"{_CREDENTIAL_REQUIRED_NOTE}. {error}",
            mode=settings.mode,
            provider=settings.provider,
            base_url=settings.base_url,
            model_configured=settings.model,
            credential_configured=bool(settings.api_key),
        )

    try:
        provider = get_provider(settings)
    except (AgentConfigurationError, LlmProviderError) as error:
        return ProviderDiagnostic(
            status=STATUS_NOT_CONFIGURED,
            message=str(error),
            mode=settings.mode,
            provider=settings.provider,
            base_url=settings.base_url,
            model_configured=settings.model,
            credential_configured=bool(settings.api_key),
        )

    base_url = getattr(provider, "base_url", settings.base_url) or (
        DEFAULT_BASE_URL if settings.provider == "tencent_tokenhub" else None
    )

    # Only providers that expose the OpenAI-compatible model-listing surface can
    # be verified this way. Anything else is reported rather than guessed at.
    if not hasattr(provider, "list_models"):
        return ProviderDiagnostic(
            status=STATUS_NOT_CONFIGURED,
            message=(
                f"Provider {settings.provider!r} does not support model listing, "
                "so connectivity cannot be verified without spending tokens."
            ),
            mode=settings.mode,
            provider=settings.provider,
            base_url=base_url,
            model_configured=settings.model,
            credential_configured=bool(settings.api_key),
        )

    try:
        models = provider.list_models()  # type: ignore[attr-defined]
    except LlmProviderError as error:
        return ProviderDiagnostic(
            status=_status_for(error.code),
            message=_message_for(error),
            mode=settings.mode,
            provider=settings.provider,
            base_url=base_url,
            model_configured=settings.model,
            credential_configured=True,
        )

    model_configured = settings.model
    available = _model_is_available(model_configured, models)
    if not available:
        return ProviderDiagnostic(
            status=STATUS_MODEL_UNAVAILABLE,
            message=(
                f"Authentication succeeded and the provider is reachable, but "
                f"AGENT_MODEL={model_configured!r} was not listed by the provider "
                f"(matching ignores a leading 'models/'). "
                f"The account can see {len(models)} model(s). Pick one of them."
            ),
            mode=settings.mode,
            provider=settings.provider,
            base_url=base_url,
            model_configured=model_configured,
            credential_configured=True,
            model_count=len(models),
            model_available=False,
            models=models if include_models else [],
        )

    return ProviderDiagnostic(
        status=STATUS_OK,
        message=(
            f"Provider is reachable and AGENT_MODEL={model_configured!r} is available. "
            "Local providers such as Ollama require no credential; remote gateways "
            "also accepted the configured one."
        ),
        mode=settings.mode,
        provider=settings.provider,
        base_url=base_url,
        model_configured=model_configured,
        credential_configured=True,
        model_count=len(models),
        model_available=True,
        models=models if include_models else [],
    )


def _normalise_model_id(model_id: str) -> str:
    """Strip a leading ``models/`` before comparing identifiers.

    Delegates to the shared helper so the diagnostics and the Stage 4C
    benchmark cannot disagree about what "available" means.
    """
    from app.agents.providers.openai_compatible import normalise_model_id

    return normalise_model_id(model_id)


def _model_is_available(model_configured: str | None, models: list[str]) -> bool:
    if not model_configured:
        return False
    wanted = _normalise_model_id(model_configured)
    return any(_normalise_model_id(candidate) == wanted for candidate in models)


def _status_for(code: str) -> str:
    if code == ProviderErrorCode.AUTHENTICATION_FAILED:
        return STATUS_AUTH_FAILED
    if code == ProviderErrorCode.ACCESS_DENIED:
        return STATUS_ACCESS_DENIED
    if code == ProviderErrorCode.MODEL_NOT_FOUND:
        return STATUS_MODEL_NOT_FOUND
    return STATUS_UNREACHABLE


def _message_for(error: LlmProviderError) -> str:
    if error.code == ProviderErrorCode.AUTHENTICATION_FAILED:
        return (
            f"{error.provider} rejected the credential (HTTP 401). "
            f"Check AGENT_API_KEY (or {TOKENHUB_API_KEY_ENV})."
        )
    if error.code == ProviderErrorCode.ACCESS_DENIED:
        return f"{error.provider} denied access (HTTP 403) for this credential."
    if error.code == ProviderErrorCode.MODEL_NOT_FOUND:
        return f"{error.provider} does not expose that endpoint (HTTP 404). Check AGENT_BASE_URL."
    return f"{error.provider} could not be reached ({error.code})."


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    include_models = "--list" in argv

    # Running this as a CLI bypasses app.main, so load backend/.env here too.
    from app.env import describe_env_source, load_local_env

    loaded = load_local_env()

    diagnostic = verify_provider_access(include_models=include_models)

    print("RydeResolve advocate provider check")
    print(f"  env file  : {describe_env_source()}{'' if loaded else ' (not loaded)'}")
    print(f"  mode      : {diagnostic.mode}")
    print(f"  provider  : {diagnostic.provider}")
    print(f"  base url  : {diagnostic.base_url or '(provider default)'}")
    print(f"  model     : {diagnostic.model_configured or '(not set)'}")
    print(f"  credential: {'present' if diagnostic.credential_configured else 'absent'}")
    if diagnostic.model_count is not None:
        print(f"  models    : {diagnostic.model_count} visible to this account")
    if diagnostic.model_available is not None:
        print(f"  available : {diagnostic.model_available}")
    print(f"  status    : {diagnostic.status}")
    print(f"  detail    : {diagnostic.message}")

    if include_models and diagnostic.models:
        print("  model ids :")
        for model_id in diagnostic.models:
            print(f"    - {model_id}")

    return 0 if diagnostic.ok or diagnostic.status == STATUS_MOCK_MODE else 1


if __name__ == "__main__":
    raise SystemExit(main())
