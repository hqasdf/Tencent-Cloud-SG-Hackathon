"""Safe upstream status crosses the intake API without raw content."""
import pytest
from fastapi import HTTPException
from app.agents.config import AgentSettings
from app.agents.provider import LlmProviderError
from app.models.intake import SendMessageRequest
from app.services.intake_provider import RegistryIntakeProvider, IntakeProviderError

@pytest.mark.parametrize("status,code,delay", [(500,"SERVER_ERROR",None),(503,"SERVER_ERROR",2.0),(429,"RATE_LIMITED",45.0)])
def test_intake_preserves_only_safe_upstream_diagnostics(monkeypatch,status,code,delay):
    from app.api import intake
    secret = "private-credential-and-interview-content"
    class Provider:
        def complete(self,request):
            raise LlmProviderError(secret,provider="gemini_native",code=code,http_status=status,attempts=1,retry_after_seconds=delay)
    adapter=RegistryIntakeProvider(settings=AgentSettings(),provider=Provider())
    with pytest.raises(IntakeProviderError) as caught:
        adapter.complete_turn(system_prompt=secret,user_prompt=secret,party="rider",lifecycle="RIDER_INTERVIEW")
    class Conversation:
        def send_message(self,**kwargs):
            raise caught.value
    monkeypatch.setattr(intake,"_conversation_service",Conversation())
    with pytest.raises(HTTPException) as routed:
        intake.send_message("SYNTHETIC",SendMessageRequest(party="rider",content="Synthetic account"))
    detail=routed.value.detail
    assert detail["code"]==code and detail["upstreamHttpStatus"]==status
    assert detail["attemptCount"]==1 and secret not in str(detail)
    assert (detail.get("retryAfterSeconds"))==delay
    assert routed.value.status_code==503

def test_unknown_status_and_invalid_cooldown_are_not_invented():
    error=IntakeProviderError("Safe failure",code="NETWORK_ERROR",retry_after_seconds=float("nan"))
    assert "upstreamHttpStatus" not in error.safe_details()
    assert "retryAfterSeconds" not in error.safe_details()
