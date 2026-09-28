"""Local .env loading and test-session hermeticity.

The backend loads ``backend/.env`` at startup. Two properties matter enough to
be enforced by tests rather than by convention:

1. **A real environment variable always beats the file.** Otherwise CI or a
   one-off ``AGENT_MODE=real ...`` invocation would be silently overridden by
   whatever a developer happens to have in their local file.
2. **The test suite never picks the file up.** ``app.main`` is imported by the
   API tests, so without the guard in ``conftest.py`` a developer pointing their
   local ``.env`` at a real provider would have the offline suite make live
   model calls: slow, non-deterministic, and potentially billable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.agents.config import AgentSettings
from app.env import ENV_FILE, describe_env_source, load_local_env


def test_env_file_is_resolved_next_to_the_backend_package() -> None:
    """The path must not depend on the current working directory."""
    assert ENV_FILE.name == ".env"
    assert ENV_FILE.parent.name == "backend"
    assert ENV_FILE.parent.parent.name == "Tencent-Cloud-SG-Hackathon"


def test_load_local_env_is_idempotent() -> None:
    first = load_local_env()
    second = load_local_env()
    assert first == second


def test_load_local_env_reports_missing_file_honestly() -> None:
    """No .env must be reported as 'nothing to do', not as a silent success."""
    assert load_local_env() == ENV_FILE.is_file()


def test_describe_env_source_never_raises() -> None:
    assert isinstance(describe_env_source(), str)


def test_a_real_environment_variable_beats_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """override=False is the whole point of the loader.

    Simulated by pointing the loader at a temporary file, because the real
    backend/.env is git-ignored and may legitimately not exist on a fresh clone.
    """
    from dotenv import load_dotenv

    tmp_env = Path(__file__).parent / "_tmp_env_probe.env"
    tmp_env.write_text("AGENT_MODE=real\nAGENT_PROVIDER=openai_compatible\n", encoding="utf-8")
    try:
        monkeypatch.setenv("AGENT_MODE", "mock")
        load_dotenv(tmp_env, override=False)
        assert os.environ["AGENT_MODE"] == "mock"
    finally:
        tmp_env.unlink(missing_ok=True)


def test_the_test_session_is_hermetic_even_if_the_local_env_is_real() -> None:
    """The suite must run in mock mode regardless of what backend/.env says.

    This is the guard that keeps `pytest` offline on a developer machine that is
    configured for a live provider.
    """
    settings = AgentSettings.from_environment()
    assert settings.mode == "mock"
    assert settings.provider == "mock"


def test_no_provider_credential_is_visible_to_the_suite() -> None:
    for name in ("AGENT_API_KEY", "TENCENT_TOKENHUB_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        assert not os.environ.get(name), f"{name} leaked into the test environment"


def test_local_env_is_git_ignored() -> None:
    """A committed .env is how secrets leak. Fail loudly if that changes."""
    repo_root = ENV_FILE.parent.parent
    if not (repo_root / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(ENV_FILE)],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, "backend/.env is NOT git-ignored — do not commit local settings"


def test_env_example_is_committed_and_holds_no_secret() -> None:
    example = ENV_FILE.parent / ".env.example"
    assert example.is_file(), ".env.example must stay committed as the template"
    text = example.read_text(encoding="utf-8")
    # The committed template must default to the safe, offline mode.
    assert "\nAGENT_MODE=mock" in "\n" + text
    # No live-looking key values: every credential line is either blank or commented.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        key, _, value = stripped.partition("=")
        if "API_KEY" in key.upper():
            assert value.strip() == "", f"{key} must be blank in .env.example"
