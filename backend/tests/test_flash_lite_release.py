"""Approved Flash Lite configuration and cumulative budgets; fully offline."""
from dataclasses import replace
import pytest
from app.agents.config import AgentSettings, AgentConfigurationError
from app.agents.provider import AgentCompletionRequest, LlmProviderError
from app.agents.provider_schema import provider_schema_for
from app.agents.providers.gemini_native import GeminiNativeProvider
from app.cloud_live import validate_live_settings, reserve_attempt
from app.cloud_storage import connection
from app.models.intake import IntakeStructuredReply
from app.models.advocate import AdvocateOutput
from app.models.rebuttal import RebuttalOutput
from app.models.judge import JudgeOutput
from app.services.agent_checkpoints import CheckpointSession
from tests.test_cloud_storage import remote, approved_live

MODEL = 'gemini-3.5-flash-lite'

def configure(monkeypatch):
    approved_live(monkeypatch)
    monkeypatch.setenv('AGENT_MODEL', MODEL)
    monkeypatch.setenv('AGENT_TIMEOUT_SECONDS', '60')
    monkeypatch.setenv('AGENT_MAX_RETRIES', '0')
    monkeypatch.setenv('CLOUD_GEMINI_DAILY_ATTEMPTS', '16')
    monkeypatch.setenv('CLOUD_GEMINI_CUMULATIVE_ATTEMPTS', '16')
    return AgentSettings.from_environment()

def test_exact_model_guard_and_matching_request(remote, monkeypatch):
    settings = configure(monkeypatch)
    assert validate_live_settings(settings) == (16, 1000000)
    request = AgentCompletionRequest(system_prompt='synthetic',user_prompt='synthetic',max_tokens=4096)
    reserve_attempt(request, MODEL)
    for invalid in ('gemini-3.8-flash', 'arbitrary-model'):
        with pytest.raises(LlmProviderError): reserve_attempt(request, invalid)
    with pytest.raises(AgentConfigurationError): validate_live_settings(replace(settings,model='arbitrary-model'))
    with pytest.raises(AgentConfigurationError): validate_live_settings(replace(settings,max_retries=1))

def test_cumulative_allowance_keeps_old_day_records(remote, monkeypatch):
    configure(monkeypatch)
    with connection(autocommit=True) as conn:
        conn.execute('INSERT INTO cloud_ai_usage VALUES(?,?,?,?)',('synthetic-tests','old-day',15,100))
    request = AgentCompletionRequest(system_prompt='',user_prompt='synthetic',max_tokens=4096)
    reserve_attempt(request, MODEL)
    with pytest.raises(LlmProviderError): reserve_attempt(request, MODEL)
    with connection() as conn:
        assert conn.execute('SELECT SUM(attempts) AS n FROM cloud_ai_usage').fetchone()['n'] == 16
        assert conn.execute("SELECT attempts FROM cloud_ai_usage WHERE day='old-day'").fetchone()['attempts'] == 15

@pytest.mark.parametrize('model',[IntakeStructuredReply,AdvocateOutput,RebuttalOutput,JudgeOutput])
def test_flash_lite_strict_schema_and_low_thinking(model):
    schema = provider_schema_for(model)
    request = AgentCompletionRequest(system_prompt='synthetic',user_prompt='synthetic',response_schema=schema,max_tokens=4096,extra_body={'reasoning_effort':'low'})
    config = GeminiNativeProvider(model=MODEL,api_key='SYNTHETIC_OFFLINE_KEY',timeout_seconds=60).build_config(request)
    assert str(config.thinking_config.thinking_level).lower().endswith('low')
    assert config.max_output_tokens == 4096 and config.response_mime_type == 'application/json'
    assert config.response_json_schema['required'] == schema['required']
    assert set(config.response_json_schema['properties']) == set(schema['properties'])

def test_checkpoint_identity_distinguishes_actual_model(tmp_path):
    settings = AgentSettings(mode='real',provider='gemini_native',model=MODEL,max_tokens=4096)
    current = CheckpointSession(tmp_path,b'x'*32,settings,'gemini_native','synthetic')
    old = CheckpointSession(tmp_path,b'x'*32,replace(settings,model='gemini-3.8-flash'),'gemini_native','synthetic')
    assert current.identity != old.identity
    assert current.identity['model'] == MODEL
