"""Deadline propagation and safe worker budget; all tests offline."""
from dataclasses import replace
import httpx
import pytest
from google.genai import errors
from app.agents.config import AgentSettings, AgentConfigurationError
from app.agents.providers.gemini_native import _default_client_factory, _normalise
from app.cloud_live import validate_live_settings

@pytest.mark.parametrize("seconds",[30,60])
def test_pinned_sdk_applies_client_and_google_server_deadline(seconds):
    client=_default_client_factory(api_key="OFFLINE_SYNTHETIC_KEY",timeout_seconds=seconds)
    try:
        request=client._api_client._build_request("post","models/gemini-3.8-flash:generateContent",{})
        assert client._api_client._http_options.timeout==seconds*1000
        assert request.timeout==seconds
        assert request.headers["X-Server-Timeout"]==str(seconds)
        assert client._api_client._http_options.retry_options.attempts==1
    finally:
        client.close()

def test_native_explicit_60_preserves_other_transport_ceiling(monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS","60")
    monkeypatch.setenv("AGENT_PROVIDER","gemini_native")
    assert AgentSettings.from_environment().timeout_seconds==60
    monkeypatch.setenv("AGENT_PROVIDER","openai_compatible")
    with pytest.raises(AgentConfigurationError,match="must be <= 30"):
        AgentSettings.from_environment()
    monkeypatch.setenv("AGENT_PROVIDER","gemini_native")
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS","61")
    with pytest.raises(AgentConfigurationError,match="must be <= 60"):
        AgentSettings.from_environment()

def test_cloud_long_deadline_forbids_multiplying_retry_envelope(monkeypatch):
    for name,value in {"STORAGE_BACKEND":"turso","CLOUD_LIVE_AI_ENABLED":"1","GEMINI_FREE_TIER_CONFIRMED":"1","AGENT_MODE":"real","AGENT_PROVIDER":"gemini_native","CLOUD_GEMINI_DAILY_ATTEMPTS":"12","CLOUD_GEMINI_DAILY_TOKEN_BUDGET":"1000000"}.items():
        monkeypatch.setenv(name,value)
    settings=AgentSettings(mode="real",provider="gemini_native",model="gemini-3.8-flash",max_tokens=4096,timeout_seconds=60,max_retries=0)
    assert validate_live_settings(settings)==(12,1000000)
    assert 2*settings.timeout_seconds==120 and 180-2*settings.timeout_seconds==60
    for invalid in (replace(settings,max_retries=1),replace(settings,timeout_seconds=61)):
        with pytest.raises(AgentConfigurationError):validate_live_settings(invalid)
    validate_live_settings(replace(settings,timeout_seconds=30,max_retries=1))

def test_google_server_deadline_and_client_read_timeout_remain_distinct():
    secret="private upstream body and credential"
    deadline=_normalise(errors.ServerError(504,{"error":{"code":504,"status":"DEADLINE_EXCEEDED","message":secret}}),"gemini_native")
    assert deadline.code=="SERVER_ERROR" and deadline.http_status==504
    assert secret not in str(deadline)
    client=_normalise(httpx.ReadTimeout(secret),"gemini_native")
    assert client.code=="TIMEOUT" and client.http_status is None
    assert secret not in str(client)
