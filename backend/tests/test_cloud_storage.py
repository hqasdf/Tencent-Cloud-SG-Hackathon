from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path

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


def test_cloud_environment_example_respects_mock_provider_ceiling(monkeypatch):
    from dotenv import dotenv_values
    values = dotenv_values(Path(__file__).resolve().parents[1] / 'cloud.env.example')
    for name in ('AGENT_MODE', 'AGENT_PROVIDER', 'AGENT_MAX_TOKENS', 'AGENT_MAX_RETRIES'):
        monkeypatch.setenv(name, values[name])
    settings = AgentSettings.from_environment()
    settings.validate()
    assert settings.mode == 'mock' and settings.max_tokens == 1024


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


def test_vercel_production_target_fails_closed_before_local_fallback(remote, monkeypatch):
    from app.deployment import validate_production, DeploymentConfigurationError
    monkeypatch.setenv('VERCEL_ENV', 'production')
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('STORAGE_BACKEND', 'sqlite')
    with pytest.raises(DeploymentConfigurationError, match='production is not approved'):
        validate_production()


def test_vercel_preview_requires_explicit_cloud_configuration(remote, monkeypatch):
    from app.deployment import validate_production, DeploymentConfigurationError
    monkeypatch.setenv('VERCEL_ENV', 'preview')
    monkeypatch.setenv('APP_ENV', 'development')
    with pytest.raises(DeploymentConfigurationError, match='authenticated Turso'):
        validate_production()


def test_vercel_preview_accepts_valid_authenticated_configuration(remote, monkeypatch):
    from app.deployment import validate_production
    monkeypatch.setenv('VERCEL_ENV', 'preview')
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://synthetic-demo.vercel.app')
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


def approved_live(monkeypatch):
    for key, value in {'CLOUD_LIVE_AI_ENABLED': '1', 'GEMINI_FREE_TIER_CONFIRMED': '1',
            'AGENT_MODE': 'real', 'AGENT_PROVIDER': 'gemini_native', 'AGENT_MODEL': 'gemini-3.8-flash',
            'AGENT_API_KEY': 'synthetic-cloud-test-only', 'AGENT_MAX_TOKENS': '4096',
            'AGENT_MAX_RETRIES': '1', 'CLOUD_GEMINI_DAILY_ATTEMPTS': '12',
            'CLOUD_GEMINI_DAILY_TOKEN_BUDGET': '1000000'}.items():
        monkeypatch.setenv(key, value)
    return AgentSettings.from_environment()


def test_live_gate_requires_free_tier_and_explicit_budget(remote, monkeypatch):
    from app.cloud_live import validate_live_settings, live_enabled
    settings = approved_live(monkeypatch)
    validate_live_settings(settings)
    assert live_enabled()
    monkeypatch.setenv('GEMINI_FREE_TIER_CONFIRMED', '0')
    with pytest.raises(AgentConfigurationError):
        validate_live_settings(settings)
    monkeypatch.setenv('GEMINI_FREE_TIER_CONFIRMED', '1')
    monkeypatch.delenv('CLOUD_GEMINI_DAILY_ATTEMPTS')
    with pytest.raises(AgentConfigurationError):
        validate_live_settings(settings)


def test_atomic_live_attempt_cap_and_provider_cooldown(remote, monkeypatch):
    from app.cloud_live import reserve_attempt, remember_cooldown
    from app.agents.provider import AgentCompletionRequest, LlmProviderError
    approved_live(monkeypatch)
    monkeypatch.setenv('CLOUD_GEMINI_DAILY_ATTEMPTS', '2')
    request = AgentCompletionRequest(system_prompt='synthetic', user_prompt='synthetic', max_tokens=4096)
    def attempt(_):
        try:
            reserve_attempt(request, 'gemini-3.8-flash')
            return True
        except LlmProviderError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 2
    monkeypatch.setenv('CLOUD_GEMINI_DAILY_ATTEMPTS', '12')
    remember_cooldown(LlmProviderError('safe', provider='gemini_native', code='RATE_LIMITED', retry_after_seconds=30))
    with pytest.raises(LlmProviderError) as error:
        reserve_attempt(request, 'gemini-3.8-flash')
    assert error.value.attempts == 0 and error.value.retry_after_seconds > 0


def test_cloud_live_token_cap_blocks_before_transport(remote, monkeypatch):
    from app.cloud_live import reserve_attempt
    from app.agents.provider import AgentCompletionRequest, LlmProviderError
    approved_live(monkeypatch)
    monkeypatch.setenv('CLOUD_GEMINI_DAILY_TOKEN_BUDGET', '4096')
    with pytest.raises(LlmProviderError) as error:
        reserve_attempt(AgentCompletionRequest(system_prompt='synthetic', user_prompt='synthetic', max_tokens=4096), 'gemini-3.8-flash')
    assert error.value.attempts == 0


def test_five_roles_resume_one_per_request_without_repeat_calls(remote, monkeypatch):
    from dataclasses import replace
    from app.cloud_live import StageDeferred
    settings = approved_live(monkeypatch)
    class SyntheticNative:
        name = 'gemini_native'
        def __init__(self):
            self.roles = []
        def complete(self, request):
            role = request.metadata.get('role', 'advocate')
            self.roles.append('JUDGE' if role == 'judge' else request.metadata['side'] + '_' + role.upper())
            response = MockLlmProvider().complete(request)
            return replace(response, provider_name=self.name, model_name='gemini-3.8-flash')
    provider = SyntheticNative()
    service = CaseService(MockCaseRepository())
    case = service._load_case('DISP-005')
    analysis = service.get_analysis('DISP-005')
    for index in range(5):
        runner = ResolutionOrchestratorService(settings=settings, provider=provider)
        before = len(provider.roles)
        if index < 4:
            with pytest.raises(StageDeferred) as pending:
                runner.run(case, analysis, resume=True)
            assert len(pending.value.saved_roles) == index + 1
        else:
            result = runner.run(case, analysis, resume=True)
            assert result.judge.status == 'COMPLETE'
            assert result.deterministic_resolution.recommended_action == 'NO_REFUND'
        assert len(provider.roles) == before + 1
    assert len(set(provider.roles)) == 5
    result = ResolutionOrchestratorService(settings=settings, provider=provider).run(case, analysis, resume=True)
    assert len(provider.roles) == 5
    assert len(result.replay_metadata.reused_roles) == 5


def test_stage_budget_never_continues_unverified_output(remote):
    from types import SimpleNamespace
    from app.cloud_live import StageBudget
    session = SimpleNamespace(events={'RIDER_ADVOCATE': 'REUSED'}, provider='gemini_native')
    budget = StageBudget(session)
    budget.before_invoke('DRIVER_ADVOCATE')
    session.events['DRIVER_ADVOCATE'] = 'REJECTED_OUTPUT'
    from app.agents.provider import LlmProviderError
    with pytest.raises(LlmProviderError) as blocked:
        budget.before_invoke('RIDER_REBUTTAL')
    assert blocked.value.attempts == 0



def test_native_transport_budget_counts_the_single_retry(remote, monkeypatch):
    from dataclasses import replace
    from test_gemini_native import _provider, _advocate_request, _Response
    from test_gemini_rate_limits import quota_error
    approved_live(monkeypatch)
    def respond(index, _call):
        if index == 1:
            raise quota_error(status=503)
        return _Response()
    provider, client, _ = _provider(responder=respond, max_retries=1)
    provider._sleep = lambda _delay: None
    result = provider.complete(replace(_advocate_request(), max_tokens=4096))
    assert result.attempt_count == 2 and len(client.calls) == 2
    with cloud.connection() as conn:
        row = conn.execute('SELECT attempts FROM cloud_ai_usage').fetchone()
    assert row['attempts'] == 2


def test_quota_cooldown_cannot_be_shortened_and_blocks_next_transport(remote, monkeypatch):
    from dataclasses import replace
    from app.cloud_live import remember_cooldown
    from app.agents.provider import LlmProviderError
    from test_gemini_native import _provider, _advocate_request
    from test_gemini_rate_limits import quota_error
    approved_live(monkeypatch)
    provider, client, _ = _provider(error=quota_error('60s'), max_retries=1)
    with pytest.raises(LlmProviderError) as failed:
        provider.complete(replace(_advocate_request(), max_tokens=4096))
    assert failed.value.retry_after_seconds == 60 and len(client.calls) == 1
    before = cloud.SqlState().read('cloud-gemini', 'cooldown')['until']
    remember_cooldown(LlmProviderError('safe', provider='gemini_native', code='RATE_LIMITED', retry_after_seconds=1))
    assert cloud.SqlState().read('cloud-gemini', 'cooldown')['until'] == before
    with pytest.raises(LlmProviderError) as blocked:
        provider.complete(replace(_advocate_request(), max_tokens=4096))
    assert blocked.value.attempts == 0 and len(client.calls) == 1


def test_live_configuration_preserves_honest_mock_replay_fallback(remote, monkeypatch):
    monkeypatch.setenv('REPLAY_API_ENABLED', '1')
    seed_demo('DISP-005')
    approved_live(monkeypatch)
    result = CaseService(MockCaseRepository()).get_resolution_run('DISP-005', replay_mode='FULL_AI')
    assert result.replay_metadata.source == 'MOCK_DEMO'
    assert result.deterministic_resolution.recommended_action == 'NO_REFUND'
    with cloud.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM cloud_ai_usage').fetchone()['n'] == 0


def test_authenticated_resolution_endpoint_returns_bounded_continuation(remote, monkeypatch):
    from app.main import app
    from app.api.cases import get_case_service
    from app.cloud_live import StageDeferred
    class Service:
        def get_resolution_run(self, *_args, **_kwargs):
            raise StageDeferred('DRIVER_ADVOCATE', ['RIDER_ADVOCATE'])
    app.dependency_overrides[get_case_service] = lambda: Service()
    try:
        with TestClient(app) as client:
            assert client.post('/api/cases/DISP-005/resolution/run').status_code == 401
            result = client.post('/api/cases/DISP-005/resolution/run', headers={'Authorization': 'Bearer ' + issue_session(), 'Idempotency-Key': 'synthetic-continuation-123456'})
        assert result.status_code == 202
        assert result.json()['savedRoles'] == ['RIDER_ADVOCATE']
        assert '202' in app.openapi()['paths']['/api/cases/{case_id}/resolution/run']['post']['responses']
    finally:
        app.dependency_overrides.clear()


def test_uncertain_budget_write_never_enters_provider_retry_loop(remote, monkeypatch):
    from dataclasses import replace
    from app import cloud_live
    from test_gemini_native import _provider, _advocate_request
    approved_live(monkeypatch)
    reservations = []
    reserve = cloud_live.reserve_attempt
    def uncertain(*args):
        reservations.append(True)
        reserve(*args)  # Simulate loss of acknowledgement after the commit.
        raise cloud.CloudStorageError('REMOTE_STORAGE_UNAVAILABLE')
    monkeypatch.setattr(cloud_live, 'reserve_attempt', uncertain)
    provider, client, _ = _provider(max_retries=1)
    waits = []
    provider._sleep = waits.append
    with pytest.raises(cloud.CloudStorageError):
        provider.complete(replace(_advocate_request(), max_tokens=4096))
    assert len(reservations) == 1
    assert client.calls == [] and waits == []
    with cloud.connection() as conn:
        assert conn.execute('SELECT attempts FROM cloud_ai_usage').fetchone()['attempts'] == 1


def test_cloud_mock_standalone_advocates_remain_supported(remote):
    result = CaseService(MockCaseRepository()).get_advocate_run('DISP-005')
    assert result.rider.status == result.driver.status == 'COMPLETE'


def test_cloud_live_standalone_advocates_cannot_bypass_stage_budget(remote, monkeypatch):
    approved_live(monkeypatch)
    with pytest.raises(AgentConfigurationError, match='checkpointed'):
        CaseService(MockCaseRepository()).get_advocate_run('DISP-005')


def test_actual_native_transport_contract_fits_all_five_resumable_roles(remote, monkeypatch):
    from types import SimpleNamespace
    from app.agents.providers.gemini_native import GeminiNativeProvider
    from app.cloud_live import StageDeferred
    from test_gemini_native import _Response
    settings = approved_live(monkeypatch)
    class OfflineNative(GeminiNativeProvider):
        def __init__(self):
            self.calls = []
            super().__init__(model=settings.model, api_key='synthetic-native-audit-only', max_retries=1,
                             client_factory=lambda **_kwargs: SimpleNamespace(models=SimpleNamespace(generate_content=self.generate)))
        def complete(self, request):
            self.current = request
            return super().complete(request)
        def generate(self, *, model, contents, config):
            assert model == settings.model and contents == self.current.user_prompt
            assert config.max_output_tokens == 4096 and config.response_json_schema
            self.calls.append(self.current)
            return _Response(MockLlmProvider().complete(self.current).raw_text)
    provider = OfflineNative()
    service = CaseService(MockCaseRepository())
    case, analysis = service._load_case('DISP-005'), service.get_analysis('DISP-005')
    for index in range(5):
        runner = ResolutionOrchestratorService(settings=settings, provider=provider)
        if index < 4:
            with pytest.raises(StageDeferred):
                runner.run(case, analysis, resume=True)
        else:
            result = runner.run(case, analysis, resume=True)
            assert result.judge.status == 'COMPLETE'
            assert result.deterministic_resolution.recommended_action == 'NO_REFUND'
    assert len(provider.calls) == 5
    with cloud.connection() as conn:
        assert conn.execute('SELECT attempts FROM cloud_ai_usage').fetchone()['attempts'] == 5


def test_production_activation_requires_explicit_approval(remote, monkeypatch):
    from app.deployment import validate_production, DeploymentConfigurationError
    monkeypatch.setenv('VERCEL_ENV', 'production')
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://synthetic-demo.vercel.app')
    with pytest.raises(DeploymentConfigurationError, match='production is not approved'):
        validate_production()
    monkeypatch.setenv('CLOUD_PRODUCTION_APPROVED', '1')
    validate_production()
    approved_live(monkeypatch)
    with pytest.raises(DeploymentConfigurationError, match='shared usage namespace'):
        validate_production()
    monkeypatch.setenv('CLOUD_GEMINI_USAGE_NAMESPACE', 'synthetic-tests')
    validate_production()


def test_separate_production_data_preserves_shared_attempt_cap(remote, monkeypatch):
    from app.cloud_live import reserve_attempt, remember_cooldown
    from app.agents.provider import AgentCompletionRequest, LlmProviderError
    approved_live(monkeypatch)
    monkeypatch.setenv('CLOUD_GEMINI_DAILY_ATTEMPTS', '20')
    monkeypatch.setenv('CLOUD_GEMINI_CUMULATIVE_ATTEMPTS', '20')
    with cloud.connection() as conn:
        conn.execute('INSERT INTO cloud_ai_usage VALUES(?,?,?,?)', ('synthetic-tests','old-day',18,0))
    monkeypatch.setenv('STORAGE_NAMESPACE', 'production-data')
    monkeypatch.setenv('CLOUD_GEMINI_USAGE_NAMESPACE', 'synthetic-tests')
    request = AgentCompletionRequest(system_prompt='synthetic', user_prompt='synthetic', max_tokens=4096)
    reserve_attempt(request, 'gemini-3.8-flash')
    reserve_attempt(request, 'gemini-3.8-flash')
    with pytest.raises(LlmProviderError):
        reserve_attempt(request, 'gemini-3.8-flash')
    with cloud.connection() as conn:
        assert conn.execute('SELECT SUM(attempts) AS n FROM cloud_ai_usage WHERE namespace=?', ('synthetic-tests',)).fetchone()['n'] == 20
        assert conn.execute('SELECT COUNT(*) AS n FROM cloud_ai_usage WHERE namespace=?', ('production-data',)).fetchone()['n'] == 0
    remember_cooldown(LlmProviderError('safe',provider='gemini_native',code='RATE_LIMITED',retry_after_seconds=30))
    assert cloud.SqlState().read('cloud-gemini','cooldown') is None
    monkeypatch.setenv('STORAGE_NAMESPACE', 'synthetic-tests')
    assert cloud.SqlState().read('cloud-gemini','cooldown')['until'] > 0


@pytest.mark.parametrize('namespace', ['', '../unsafe', 'x'*65])
def test_usage_namespace_rejects_invalid_configuration(remote, monkeypatch, namespace):
    from app.cloud_live import validate_live_settings
    settings = approved_live(monkeypatch)
    monkeypatch.setenv('CLOUD_GEMINI_USAGE_NAMESPACE', namespace)
    with pytest.raises(AgentConfigurationError, match='usage namespace'):
        validate_live_settings(settings)
