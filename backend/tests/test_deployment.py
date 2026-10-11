"""Production boundaries and portable demo preparation; no live provider calls."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.deployment import allowed_origins, validate_production, DeploymentConfigurationError


@pytest.fixture
def production(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
    monkeypatch.setenv("INTAKE_DB_PATH", str(tmp_path / "intake" / "intake.db"))
    monkeypatch.setenv("AGENT_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("REPLAY_ARTIFACT_DIR", str(tmp_path / "replay"))
    return tmp_path


def test_development_origins_remain_available():
    assert "http://localhost:5173" in allowed_origins()
    assert "http://127.0.0.1:5173" in allowed_origins()


def test_production_same_origin_needs_no_cors_grant(production):
    assert allowed_origins() == []
    validate_production()


@pytest.mark.parametrize("value", ["*", "https://*.example.com", "http://app.example.com", "https://localhost", "https://127.0.0.1",
    "https://user:PRIVATE_CREDENTIAL@example.com", "https://example.com/path", "https://example.com?secret=PRIVATE_CREDENTIAL"])
def test_production_rejects_unsafe_origins_without_echoing_values(production, monkeypatch, value):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", value)
    with pytest.raises(DeploymentConfigurationError) as caught: allowed_origins()
    assert "PRIVATE_CREDENTIAL" not in str(caught.value)


def test_exact_origin_preflight_permits_only_configured_frontend(production, monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com,https://app.example.com")
    origins = allowed_origins()
    assert origins == ["https://app.example.com"]
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    client = TestClient(app)
    headers = {"Origin":"https://app.example.com", "Access-Control-Request-Method":"POST", "Access-Control-Request-Headers":"Content-Type"}
    allowed = client.options("/api/cases", headers=headers)
    denied = client.options("/api/cases", headers={**headers, "Origin":"https://untrusted.example.com"})
    assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize("key", ["INTAKE_DB_PATH", "AGENT_CHECKPOINT_DIR", "REPLAY_ARTIFACT_DIR"])
def test_production_requires_explicit_absolute_persistence(production, monkeypatch, key):
    monkeypatch.setenv(key, "relative/storage")
    with pytest.raises(DeploymentConfigurationError, match=key): validate_production()


def test_configured_intake_database_persists_after_restart(production, monkeypatch):
    from app.repositories.intake_repository import IntakeCaseRepository
    validate_production()
    first = IntakeCaseRepository()
    created = first.create_case(source_case_id="DISP-005", trip_id="SYNTHETIC_TRIP")
    second = IntakeCaseRepository()
    assert second._db_path == production / "intake" / "intake.db"
    assert second.get_case(created.id).source_case_id == "DISP-005"


def test_production_never_loads_a_project_env_file(monkeypatch, tmp_path):
    from app import env
    import dotenv
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(env, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail("Production must not read local credentials"))
    env.load_local_env.cache_clear()
    try: assert not env.load_local_env()
    finally: env.load_local_env.cache_clear()


def test_portable_demo_initializes_empty_intake_then_replays_without_live_calls(tmp_path, monkeypatch):
    from app.replay.cli import main
    from app.repositories.intake_repository import IntakeCaseRepository
    from app.agents.providers.gemini_native import GeminiNativeProvider
    from app.services.case_service import CaseService
    from app.repositories.case_repository import MockCaseRepository
    from app.agents.config import AgentSettings
    from app.replay.store import ReplayArtifactStore
    from app.replay.service import ReplayService
    from app.services.resolution_orchestrator import ResolutionOrchestratorService
    db = tmp_path / "fresh.db"; root = tmp_path / "demo"
    monkeypatch.setenv("INTAKE_DB_PATH", str(db))
    monkeypatch.setattr(GeminiNativeProvider, "complete", lambda *a, **k: pytest.fail("No live Gemini call permitted"))
    assert main(["mock-demo", "--case", "DISP-005", "--output", str(root), "--initialize-empty-intake"]) == 0
    repo = IntakeCaseRepository()
    intake = repo.get_case_by_source("DISP-005")
    assert intake.messages == [] and not intake.rider_state.facts and not intake.driver_state.facts
    service = CaseService(MockCaseRepository(), intake_repository=repo)
    case = service._load_case("DISP-005"); analysis = service.get_analysis(case.id)
    class Forbidden:
        name = "gemini_native"
        def complete(self, request): pytest.fail("Replay cannot fall back to a provider")
    settings = AgentSettings(mode="real", provider="gemini_native", model="gemini-3.8-flash", max_tokens=4096)
    result = ResolutionOrchestratorService(settings=settings, provider=Forbidden(), replay_service=ReplayService(
        store=ReplayArtifactStore(root), settings=settings)).run(case, analysis, replay_mode="FULL_AI")
    assert result.replay_metadata.source == "MOCK_DEMO"
    assert result.judge.status == "COMPLETE"
    assert result.intake_context.intake_case_id == intake.id and not result.intake_context.comparison_allowed
    assert result.deterministic_resolution.refund_amount == 0


@pytest.mark.parametrize("source_id", ["DISP-005", "DISP-002"])
def test_demo_initialization_refuses_populated_data_without_overwriting(tmp_path, monkeypatch, source_id):
    from app.replay.cli import main
    from app.repositories.intake_repository import IntakeCaseRepository
    db = tmp_path / "populated.db"
    monkeypatch.setenv("INTAKE_DB_PATH", str(db))
    repo = IntakeCaseRepository()
    case = repo.create_case(source_case_id=source_id, trip_id="SYNTHETIC_TRIP")
    repo.add_message(intake_case_id=case.id, party="rider", sender="user", content="PRIVATE_TEST_INTERVIEW")
    before = db.read_bytes()
    assert main(["mock-demo", "--case", "DISP-005", "--output", str(tmp_path / "demo"), "--initialize-empty-intake"]) == 2
    assert db.read_bytes() == before
    assert not (tmp_path / "demo").exists()


def test_demo_initialization_does_not_create_tables_in_an_unrelated_database(tmp_path, monkeypatch):
    import sqlite3
    from app.replay.cli import main
    database = tmp_path / "unrelated.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE private_records (value TEXT)")
        connection.execute("INSERT INTO private_records VALUES ('PRIVATE_TEST_DATA')")
    before = database.read_bytes()
    monkeypatch.setenv("INTAKE_DB_PATH", str(database))
    assert main(["mock-demo", "--case", "DISP-005", "--output", str(tmp_path / "demo"), "--initialize-empty-intake"]) == 2
    assert database.read_bytes() == before


def test_demo_initialization_requires_explicit_database(monkeypatch, tmp_path):
    from app.replay.cli import main
    monkeypatch.setenv("INTAKE_DB_PATH", "")
    assert main(["mock-demo", "--case", "DISP-005", "--output", str(tmp_path / "demo"), "--initialize-empty-intake"]) == 2


def test_live_production_preserves_native_envelope(production, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "gemini_native")
    monkeypatch.setenv("AGENT_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("AGENT_API_KEY", "SYNTHETIC_OFFLINE_TEST_KEY")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "4096")
    monkeypatch.setenv("AGENT_MAX_RETRIES", "1")
    validate_production()  # Configuration only; never constructs a transport client.
    monkeypatch.setenv("AGENT_MAX_RETRIES", "2")
    with pytest.raises(DeploymentConfigurationError): validate_production()


def test_production_refuses_a_live_provider_in_mock_mode(production, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "gemini_native")
    with pytest.raises(DeploymentConfigurationError, match="explicit mock provider"):
        validate_production()
