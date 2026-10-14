"""Request-only citation restrictions, unchanged persistence and strict verification."""
import json
from copy import deepcopy
from types import SimpleNamespace
import httpx
import pytest
from app.agents.driver_advocate import DriverAdvocateAgent
from app.agents.rider_advocate import RiderAdvocateAgent
from app.agents.provider import AgentCompletionRequest
from app.agents.provider_schema import provider_schema_for
from app.agents.providers.gemini_native import GeminiNativeProvider,_default_client_factory
from app.agents.claim_verification import AdvocateClaimVerificationService
from app.models.advocate import AdvocateOutput
from tests.test_advocate_schema_alignment import _context,output,fact_proposition
from tests.test_agent_recovery import ControlledProvider,execute
from tests.test_cloud_storage import remote,approved_live

def agent():
    return DriverAdvocateAgent(SimpleNamespace(name='gemini_native'))

def test_old_missing_citation_shape_is_auditable_but_generation_disallows_empty_array():
    context=_context();payload=output([fact_proposition()]);payload['side']='DRIVER';payload['claims'][0]['evidenceIds']=[]
    result=AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(payload),context)
    assert result.rejected_claims[0].reason=='NO_EVIDENCE_REFERENCE'
    base=provider_schema_for(AdvocateOutput)['properties']['claims']['items']
    assert 'evidenceIds' in base['required'] and 'minItems' not in base['properties']['evidenceIds']
    schema=agent()._request_schema(context);claim=schema['properties']['claims']['items']
    assert 'evidenceIds' in claim['required'] and claim['properties']['evidenceIds']['minItems']==1

def test_enums_exclude_alleged_and_preserve_mandatory_proposition_branches():
    context=_context()
    context=context.model_copy(update={'evidence':[e.model_copy(update={'status':'ALLEGED'}) if i==0 else e for i,e in enumerate(context.evidence)]})
    schema=agent()._request_schema(context);claim=schema['properties']['claims']['items'];props=claim['properties']['propositions']['items']
    ids=sorted(e.id for e in context.evidence if e.status=='Verified')
    assert claim['properties']['evidenceIds']['items']['enum']==ids
    assert props['properties']['evidenceIds']['items']['enum']==ids
    for branch in props['anyOf']:
        if branch['properties']['kind']['const']=='ADVOCACY_POSITION':
            assert 'evidenceIds' not in branch['required']
        else:
            assert 'evidenceIds' in branch['required'] and branch['properties']['evidenceIds']['minItems']==1
            assert branch['properties']['evidenceIds']['items']['enum']==ids

def test_abstention_and_persisted_schema_remain_unchanged():
    a=agent();baseline=deepcopy(a._output_schema);context=_context()
    schema=a._request_schema(context)
    assert 'minItems' not in schema['properties']['claims']
    empty=context.model_copy(update={'evidence':[e.model_copy(update={'status':'Missing'}) for e in context.evidence]})
    assert a._request_schema(empty)['properties']['claims']['maxItems']==0
    assert a._output_schema==baseline==provider_schema_for(AdvocateOutput)

def test_actual_sdk_wire_keeps_minimum_required_and_enums():
    context=_context();a=agent();schema=a._request_schema(context)
    provider=GeminiNativeProvider(model='gemini-3.5-flash-lite',api_key='OFFLINE_FAKE_KEY',timeout_seconds=60,max_retries=0)
    request=AgentCompletionRequest(system_prompt='Synthetic.',user_prompt=a._user_prompt(context),response_schema=schema,max_tokens=4096,extra_body={'reasoning_effort':'low'})
    client=_default_client_factory(api_key='OFFLINE_FAKE_KEY',timeout_seconds=60);original=client._api_client._httpx_client;seen=[]
    def capture(req):
        config=json.loads(req.content)['generationConfig'];claim=config['responseJsonSchema']['properties']['claims']['items']
        assert 'evidenceIds' in claim['required'] and claim['properties']['evidenceIds']['minItems']==1
        assert claim['properties']['evidenceIds']['items']['enum']==sorted(e.id for e in context.evidence if e.status=='Verified')
        for branch in claim['properties']['propositions']['items']['anyOf']:
            if branch['properties']['kind']['enum']!=['ADVOCACY_POSITION']:
                assert branch['properties']['evidenceIds']['minItems']==1 and 'evidenceIds' in branch['required']
        assert config['maxOutputTokens']==4096 and client._api_client._http_options.retry_options.attempts==1
        seen.append(True)
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{}'}]},'finishReason':'STOP'}]})
    client._api_client._httpx_client=httpx.Client(transport=httpx.MockTransport(capture))
    try:client.models.generate_content(model=provider.model_name,contents=request.user_prompt,config=provider.build_config(request));assert len(seen)==1
    finally:client.close();original.close()

def test_valid_driver_checkpoint_and_rider_reuse_have_no_duplicate_calls(tmp_path):
    first=ControlledProvider(fail={'DRIVER_ADVOCATE'})
    execute(tmp_path,first)
    rider=next(tmp_path.rglob('rider_advocate.json')).read_bytes()
    resumed=ControlledProvider();result=execute(tmp_path,resumed)
    assert resumed.counts['RIDER_ADVOCATE']==0 and resumed.counts['DRIVER_ADVOCATE']==1
    assert result.rider.execution.reused and result.driver.status=='COMPLETE'
    assert next(tmp_path.rglob('rider_advocate.json')).read_bytes()==rider

def test_twenty_attempt_cumulative_cap_keeps_previous_records(remote,monkeypatch):
    from app.cloud_storage import connection
    from app.cloud_live import reserve_attempt
    from app.agents.provider import LlmProviderError
    approved_live(monkeypatch)
    monkeypatch.setenv('CLOUD_GEMINI_DAILY_ATTEMPTS','20');monkeypatch.setenv('CLOUD_GEMINI_CUMULATIVE_ATTEMPTS','20')
    with connection(autocommit=True) as conn:conn.execute('INSERT INTO cloud_ai_usage VALUES(?,?,?,?)',('synthetic-tests','previous-day',19,100))
    request=AgentCompletionRequest(system_prompt='',user_prompt='synthetic',max_tokens=4096)
    reserve_attempt(request,'gemini-3.8-flash')
    with pytest.raises(LlmProviderError):reserve_attempt(request,'gemini-3.8-flash')
    with connection() as conn:assert conn.execute('SELECT SUM(attempts) AS n FROM cloud_ai_usage').fetchone()['n']==20
