from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import cloud_storage as cloud
from app.demo_auth import make_password_hash, issue_session, valid_session
from app.services.agent_checkpoints import AgentCheckpointStore, CheckpointStorageError, CheckpointBusy
from app.agents.config import AgentSettings, AgentConfigurationError
from app.agents.providers.registry import get_provider
from app.agents.providers.mock import MockLlmProvider
from app.services.resolution_orchestrator import ResolutionOrchestratorService
from app.repositories.case_repository import MockCaseRepository
from app.services.case_service import CaseService
from app.cloud_bootstrap import seed_demo, smoke


@pytest.fixture(params=['sqlite_contract', 'native_libsql'])
def remote(request, monkeypatch, tmp_path):
    monkeypatch.setenv('STORAGE_BACKEND', 'turso')
    monkeypatch.setenv('_RYDE_CLOUD_WORKER', '1')
    monkeypatch.setenv('STORAGE_NAMESPACE', 'synthetic-tests')
    monkeypatch.setenv('CHECKPOINT_SIGNING_SECRET', 'synthetic-checkpoint-key-' + 'x' * 32)
    monkeypatch.setenv('DEMO_SESSION_SECRET', 'synthetic-session-key-' + 'y' * 32)
    monkeypatch.setenv('DEMO_PASSWORD_HASH', make_password_hash('synthetic-password'))
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://synthetic.turso.io')
    monkeypatch.setenv('TURSO_AUTH_TOKEN', 'synthetic-not-a-real-token')
    path = tmp_path / 'remote-contract.sqlite'
    def connect(autocommit=False):
        if request.param == 'native_libsql':
            import libsql
            return cloud.NamedConnection(libsql.connect(str(path), timeout=2, isolation_level=None if autocommit else 'DEFERRED'))
        conn = sqlite3.connect(path, timeout=2, isolation_level=None if autocommit else 'DEFERRED')
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(cloud, 'connect', connect)
    cloud.SqlState().initialize()
    cloud.intake_repository().initialize()
    yield path


def test_intake_smoke_without_importing_records(remote):
    assert smoke()
    repo = cloud.intake_repository()
    assert repo.get_case_by_source('DISP-005') is None


def test_intake_namespaces_do_not_share_cases_or_interviews(remote, monkeypatch):
    first = cloud.intake_repository()
    saved = first.create_case(source_case_id='DISP-005', trip_id='SYNTHETIC-PREVIEW')
    first.add_message(intake_case_id=saved.id, party='rider', sender='user', content='Synthetic preview account')
    monkeypatch.setenv('STORAGE_NAMESPACE', 'separate-production-test')
    second = cloud.intake_repository()
    second.initialize()
    assert second.get_case(saved.id) is None
    assert second.get_case_by_source('DISP-005') is None
    assert first.get_case(saved.id).source_case_id == 'DISP-005'


def test_competing_leases_and_fenced_stale_write(remote):
    state = cloud.SqlState()
    with state.lease('case') as old:
        with pytest.raises(CheckpointBusy):
            with cloud.SqlState().lease('case'):
                pytest.fail('Duplicate lease')
        with cloud.connection() as conn:
            conn.execute('UPDATE cloud_leases SET expires=0 WHERE case_id=?', ('case',))
        with state.lease('case') as new:
            with pytest.raises(cloud.CloudStorageError):
                state.write('case', 'slot', {'synthetic': True}, old)
            state.write('case', 'slot', {'synthetic': True}, new)
    assert state.read('case', 'slot') == {'synthetic': True}


def test_usage_limit_atomic_across_threads(remote):
    state = cloud.SqlState()
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: state.consume('bucket', 2), range(8)))
    assert sum(outcomes) == 2


def test_cloud_checkpoints_recover_and_keep_signatures(remote):
    service = CaseService(MockCaseRepository())
    case, analysis = service._load_case('DISP-005'), service.get_analysis('DISP-005')
    settings = AgentSettings(mode='mock', provider='mock')
    first = ResolutionOrchestratorService(settings=settings, provider=MockLlmProvider()).run(case, analysis, resume=True)
    second = ResolutionOrchestratorService(settings=settings, provider=MockLlmProvider()).run(case, analysis, resume=True)
    assert len(second.replay_metadata.reused_roles) == 5
    assert first.deterministic_resolution == second.deterministic_resolution
    state = cloud.SqlState()
    doc = state.read('DISP-005', 'rider_advocate.json')
    doc['signature'] = '0' * 64
    state.write('DISP-005', 'rider_advocate.json', doc)
    third = ResolutionOrchestratorService(settings=settings, provider=MockLlmProvider()).run(case, analysis, resume=True)
    assert 'RIDER_ADVOCATE' not in third.replay_metadata.reused_roles


def test_checkpoint_storage_failure_does_not_trigger_replacement(remote, monkeypatch):
    with AgentCheckpointStore().session('DISP-005', AgentSettings(), 'mock', 'hash') as session:
        monkeypatch.setattr(session.directory.state, 'read', lambda *args: (_ for _ in ()).throw(cloud.CloudStorageError('synthetic failure')))
        from app.agents.context_builder import AdvocateContextBuilder
        service = CaseService(MockCaseRepository())
        context = AdvocateContextBuilder().build(service._load_case('DISP-005'), service.get_analysis('DISP-005'))
        with pytest.raises(CheckpointStorageError):
            session.run('RIDER_ADVOCATE', context, lambda: pytest.fail('Must not invoke provider'), lambda *a: None, lambda *a: True)


@pytest.mark.parametrize('case_id', [case.id for case in MockCaseRepository().list_cases()])
def test_seed_and_revalidate_all_synthetic_cases(remote, case_id):
    result = seed_demo(case_id)
    assert 'MOCK_DEMO' in result.replay_metadata.model_dump_json()
    assert result.workflow.final_resolution.status == 'COMPLETE'


def test_cloud_auth_and_protected_full_replay(remote, monkeypatch):
    from app.main import app
    from app.api import intake
    monkeypatch.setattr(intake, '_repository', cloud.intake_repository())
    monkeypatch.setenv('REPLAY_API_ENABLED', '1')
    seed_demo('DISP-005')
    with TestClient(app) as client:
        assert client.get('/api/cases').status_code == 401
        assert client.get('/demo').status_code == 404
        assert client.post('/api/auth/login', json={'password': 'wrong'}).status_code == 401
        response = client.post('/api/auth/login', json={'password': 'synthetic-password'})
        assert response.status_code == 200
        token = response.json()['accessToken']
        headers = {'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'synthetic-action-123456'}
        assert client.get('/api/cases', headers=headers).status_code == 200
        result = client.post('/api/cases/DISP-005/resolution/run', json={'replayMode': 'FULL_AI'}, headers=headers)
        assert result.status_code == 200
        assert 'MOCK_DEMO' in result.text
        assert client.post('/api/cases/DISP-005/resolution/run', json={'replayMode': 'FULL_AI'}, headers=headers).status_code == 409
        assert client.get('/api/cases', headers={'Authorization': 'Bearer ' + token + 'tamper'}).status_code == 401
        preflight = client.options('/api/cases', headers={'Origin': 'http://127.0.0.1:5173', 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'Authorization'})
        assert preflight.status_code == 200


def test_cloud_never_enables_live_provider(remote):
    with pytest.raises(AgentConfigurationError, match='disabled'):
        get_provider(AgentSettings(mode='real', provider='gemini_native', model='gemini-3.8-flash', api_key='synthetic-only', max_tokens=4096))


def test_production_cloud_config_requires_auth_and_signing(remote, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://synthetic-demo.vercel.app')
    from app.deployment import validate_production, DeploymentConfigurationError
    validate_production()
    monkeypatch.delenv('CHECKPOINT_SIGNING_SECRET')
    with pytest.raises(DeploymentConfigurationError, match='CHECKPOINT_SIGNING_SECRET'):
        validate_production()


def test_cloud_production_requires_explicit_cors(remote, monkeypatch):
    from app.deployment import validate_production, DeploymentConfigurationError
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('CORS_ALLOWED_ORIGINS', raising=False)
    with pytest.raises(DeploymentConfigurationError, match='CORS origin'):
        validate_production()


def test_unsafe_documents_and_namespace_isolation(remote, monkeypatch):
    state = cloud.SqlState()
    with pytest.raises(cloud.CloudStorageError):
        state.write('case', 'slot', {'password': 'synthetic'})
    state.write('case', 'slot', {'synthetic': True})
    monkeypatch.setenv('STORAGE_NAMESPACE', 'other-test')
    assert cloud.SqlState().read('case', 'slot') is None


def test_login_rate_limit_and_expired_session(remote, monkeypatch):
    from app.demo_auth import login, Login, _sign
    from fastapi import HTTPException
    import base64
    state = cloud.SqlState()
    import time
    bucket = 'login:' + str(int(time.time()) // 3600)
    for _ in range(60):
        state.consume(bucket, 60)
    with pytest.raises(HTTPException) as error:
        login(Login(password='synthetic-password'))
    assert error.value.status_code == 429
    payload = base64.urlsafe_b64encode(json.dumps({'sub': 'demo-operator', 'exp': 0}).encode()).decode()
    assert not valid_session(payload + '.' + _sign(payload))
