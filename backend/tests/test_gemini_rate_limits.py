"""Native quota handling: injected transport only; no live requests or sleeps."""
from types import SimpleNamespace
import json
import traceback

import pytest
from google.genai.errors import APIError

from app.agents.provider import LlmProviderError
from test_gemini_native import _provider, _advocate_request, _Response, SECRET


def quota_error(delay=None, header=None, status=429, details=None):
    items = list(details or [])
    if delay is not None:
        items.append({"@type":"type.googleapis.com/google.rpc.RetryInfo", "retryDelay":delay})
    error = APIError(status, {"error":{"code":status,"message":SECRET+" private interview",
                                      "details":items}})
    if header is not None:
        error.response = SimpleNamespace(headers={"Retry-After":header})
    return error


@pytest.mark.parametrize("delay,seconds", [("5.25s",5.25), ({"seconds":"4","nanos":500000000},4.5)])
def test_structured_retry_delay_is_respected_and_success_counts_two_attempts(delay, seconds):
    def respond(index, _call):
        if index == 1: raise quota_error(delay)
        return _Response()
    provider, client, _ = _provider(responder=respond, max_retries=1)
    waits=[];provider._sleep=waits.append
    completion=provider.complete(_advocate_request())
    assert waits == [seconds]
    assert completion.attempt_count == 2 and len(client.calls) == 2


def test_header_and_structured_retry_guidance_use_later_minimum():
    provider, client, _ = _provider(error=quota_error("4s",header="7"),max_retries=1)
    waits=[];provider._sleep=waits.append
    with pytest.raises(LlmProviderError) as caught: provider.complete(_advocate_request())
    assert waits == [7.0] and len(client.calls) == 2
    assert caught.value.retry_after_seconds == 7.0
    assert caught.value.attempts == 2 and caught.value.http_status == 429


def test_excessive_delay_returns_cooldown_without_shortening_it_or_retrying():
    provider, client, _ = _provider(error=quota_error("60s"),max_retries=1)
    waits=[];provider._sleep=waits.append
    with pytest.raises(LlmProviderError) as caught: provider.complete(_advocate_request())
    assert caught.value.code == "RATE_LIMITED" and caught.value.retryable
    assert caught.value.retry_after_seconds == 60.0
    assert caught.value.attempts == 1 and len(client.calls) == 1 and waits == []


@pytest.mark.parametrize("delay,header", [(None,None),("garbage",None),({},None),(None,"NaN"),(None,"-4")])
def test_no_usable_429_guidance_does_not_blindly_retry(delay, header):
    provider, client, _ = _provider(error=quota_error(delay,header),max_retries=1)
    waits=[];provider._sleep=waits.append
    with pytest.raises(LlmProviderError) as caught: provider.complete(_advocate_request())
    assert caught.value.code == "RATE_LIMITED" and caught.value.retry_after_seconds is None
    assert waits == [] and len(client.calls) == 1 and caught.value.attempts == 1


def test_temporary_503_uses_bounded_jitter_and_one_retry():
    provider, client, _ = _provider(error=quota_error(status=503),max_retries=1,jitter=lambda:0.5)
    waits=[];provider._sleep=waits.append
    with pytest.raises(LlmProviderError) as caught: provider.complete(_advocate_request())
    assert waits == [1.125]
    assert len(client.calls) == 2 and caught.value.attempts == 2
    assert caught.value.code == "SERVER_ERROR" and caught.value.http_status == 503


def test_even_large_retry_count_cannot_exceed_total_wait_budget():
    provider, client, _ = _provider(error=quota_error("12s"),max_retries=100)
    waits=[];provider._sleep=waits.append
    with pytest.raises(LlmProviderError) as caught: provider.complete(_advocate_request())
    assert waits == [12.0] and len(client.calls) == 2
    assert caught.value.attempts == 2 and sum(waits) <= 20


def test_safe_quota_fields_and_traceback_never_echo_body_or_credentials(caplog, capsys):
    quota={"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":[{
        "quotaMetric":"generativelanguage.googleapis.com/generate_content_free_tier_requests",
        "quotaId":"GenerateContentRequestsPerMinutePerProjectPerModel-FreeTier",
        "description":SECRET+" private interview","quotaDimensions":{"project":"PRIVATE_PROJECT","model":SECRET}},
        {"quotaId":"GenerateContent"+SECRET}, {"quotaMetric":SECRET}]}
    provider, client, _ = _provider(error=quota_error("60s",details=[quota]),max_retries=1)
    with pytest.raises(LlmProviderError) as caught: provider.complete(_advocate_request())
    failure=caught.value
    assert failure.quota_details == [{
        "quotaMetric":"generativelanguage.googleapis.com/generate_content_free_tier_requests",
        "quotaId":"GenerateContentRequestsPerMinutePerProjectPerModel-FreeTier"}]
    captured = capsys.readouterr()
    rendered=json.dumps(vars(failure))+str(failure)+''.join(traceback.format_exception(failure)) + caplog.text + captured.out + captured.err
    for forbidden in [SECRET,"private interview","PRIVATE_PROJECT","quotaDimensions","description"]:
        assert forbidden not in rendered


def test_google_sdk_automatic_retries_remain_disabled(monkeypatch):
    from app.agents.providers.gemini_native import _default_client_factory
    from google import genai
    seen={}
    monkeypatch.setattr(genai,"Client",lambda **kwargs:seen.update(kwargs))
    _default_client_factory(api_key=SECRET,timeout_seconds=30)
    assert seen["http_options"].retry_options.attempts == 1


def test_rate_limited_driver_preserves_signed_rider_checkpoint(tmp_path):
    from test_agent_recovery import ControlledProvider, execute, role_of, assert_complete

    class LimitedProvider(ControlledProvider):
        def complete(self, request):
            if role_of(request) == "DRIVER_ADVOCATE":
                self.requests.append(request)
                raise LlmProviderError("Safe quota failure", provider=self.name,
                    code="RATE_LIMITED", http_status=429, retry_after_seconds=60, attempts=1)
            return super().complete(request)

    first = execute(tmp_path, LimitedProvider())
    assert first.rider.status == "COMPLETE"
    assert first.driver.execution.failure_code == "RATE_LIMITED"
    assert first.driver.execution.retry_after_seconds == 60
    assert first.driver.execution.http_status == 429
    assert first.workflow.judge.status == "BLOCKED"
    assert len(list(tmp_path.rglob("*.json"))) == 1
    recovered = ControlledProvider()
    second = execute(tmp_path, recovered)
    assert_complete(second)
    assert recovered.counts.get("RIDER_ADVOCATE", 0) == 0
    assert recovered.counts["DRIVER_ADVOCATE"] == 1
    assert second.deterministic_resolution == first.deterministic_resolution
