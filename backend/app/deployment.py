"""Small deployment boundary; calculation and AI contracts stay unchanged."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

DEV_ORIGINS = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in range(5173, 5181)]


class DeploymentConfigurationError(ValueError):
    pass


def production_mode() -> bool:
    value = os.getenv("APP_ENV", "development").strip().lower()
    if value not in {"development", "test", "production"}:
        raise DeploymentConfigurationError("APP_ENV must be development, test or production")
    return value == "production"


def allowed_origins() -> list[str]:
    production = production_mode()
    configured = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if not configured:
        return [] if production else list(DEV_ORIGINS)
    origins = list(dict.fromkeys(item.strip() for item in configured.split(",") if item.strip()))
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not parsed.path and not parsed.query and not parsed.fragment)
            parsed.port  # Validate malformed ports, without echoing the value.
        except ValueError:
            valid = False
        if not valid or "*" in origin:
            raise DeploymentConfigurationError("CORS_ALLOWED_ORIGINS must contain exact HTTP(S) origins")
        if production and (parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}):
            raise DeploymentConfigurationError("Production CORS origins must use HTTPS and a non-loopback host")
    return origins


def validate_production() -> None:
    if not production_mode():
        return
    # New SQLite/replay/lock files are private to the container service owner.
    # Do not change permissions or contents of any existing developer data.
    os.umask(0o077)
    allowed_origins()
    # Explicit paths make the persistent-volume requirement reviewable. This
    # checks availability; the deployment must actually mount durable storage.
    for key in ("INTAKE_DB_PATH", "AGENT_CHECKPOINT_DIR", "REPLAY_ARTIFACT_DIR"):
        value = os.getenv(key, "")
        if not value or not Path(value).is_absolute():
            raise DeploymentConfigurationError(f"Production requires an absolute {key}")
        directory = Path(value).parent if key == "INTAKE_DB_PATH" else Path(value)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory) as probe:
                probe.write(b"storage-check")
                probe.flush()
        except OSError:
            raise DeploymentConfigurationError(f"Production storage is unavailable for {key}") from None
    from app.agents.config import AgentSettings
    settings = AgentSettings.from_environment()
    settings.validate()
    if settings.mode == "mock" and settings.provider != "mock":
        raise DeploymentConfigurationError("Production mock mode requires the explicit mock provider")
    if settings.mode == "real" and settings.provider not in ("gemini_native", "tencent_tokenhub", "openai_compatible"):
        raise DeploymentConfigurationError("Production live mode requires a supported provider")
