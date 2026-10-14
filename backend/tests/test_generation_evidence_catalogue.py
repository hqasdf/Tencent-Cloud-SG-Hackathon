"""Generation-only citation guidance; all provider traffic mocked offline."""
import json
from copy import deepcopy
from dataclasses import replace
import httpx
import pytest
from app.agents.base_advocate import BaseAdvocateAgent
from app.agents.claim_verification import AdvocateClaimVerificationService, fact_values
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.provider import AgentCompletionRequest
from app.agents.provider_schema import provider_schema_for
from app.agents.providers.gemini_native import GeminiNativeProvider, _default_client_factory
from app.agents.semantic_registry import supporting_evidence_ids
from app.benchmarks.cases import case_by_id
from app.models.advocate import AdvocateOutput
from app.services.dispute_analysis import DisputeAnalysisService
from tests.test_advocate_schema_alignment import _context, output, fact_proposition
from tests.test_agent_recovery import ControlledProvider, execute

def contract(context):
    prompt=BaseAdvocateAgent._user_prompt(context)
    return json.loads(prompt.split('<atomic_proposition_contract>\n')[1].split('\n</atomic_proposition_contract>')[0]),prompt

@pytest.mark.parametrize('case_id',[f'DISP-{n:03}' for n in range(2,10)])
def test_catalogue_and_example_match_current_verifier(case_id):
    case=case_by_id(case_id)
    context=AdvocateContextBuilder().build(case,DisputeAnalysisService().analyze(case))
    data,prompt=contract(context)
    assert data['verifiedEvidenceIds']==sorted(e.id for e in context.evidence if e.status=='Verified')
    for fact in fact_values(context.facts):
        assert data['factEvidenceIds'][fact]==supporting_evidence_ids(context,fact)
    assert 'trusted case context' not in prompt
    if '<citation_format_example>' in prompt:
        claim=json.loads(prompt.split('<citation_format_example>\n')[1].split('\n</citation_format_example>')[0])
        response={'contractVersion':'atomic-propositions-v2','side':'RIDER','summary':'Format test.','claims':[claim],'requestedOutcome':'NO_REFUND','contextAcknowledged':True}
        outcome=AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(response),context)
        assert len(outcome.verified_claims)==1 and not outcome.rejected_claims

@pytest.mark.parametrize('mutation,reason',[
    ('parent_missing','EVIDENCE_NOT_DECLARED_BY_CLAIM'),
    ('proposition_empty','NO_EVIDENCE_REFERENCE'),
    ('invented','EVIDENCE_ID_NOT_FOUND'),
    ('alleged','EVIDENCE_NOT_VERIFIED'),
])
def test_invalid_citation_fixtures_remain_rejected(mutation,reason):
    context=_context();proposition=fact_proposition();payload=output([proposition])
    if mutation=='parent_missing':
        payload['claims'][0]['evidenceIds']=[next(e.id for e in context.evidence if e.status=='Verified' and e.id not in proposition['evidenceIds'])]
    elif mutation=='proposition_empty': payload['claims'][0]['propositions'][0]['evidenceIds']=[]
    elif mutation=='invented':
        payload['claims'][0]['evidenceIds']=['INVENTED'];payload['claims'][0]['propositions'][0]['evidenceIds']=['INVENTED']
    else:
        cited=set(proposition['evidenceIds'])
        context=context.model_copy(update={'evidence':[e.model_copy(update={'status':'ALLEGED'}) if e.id in cited else e for e in context.evidence]})
        data,_=contract(context)
        assert cited.isdisjoint(data['verifiedEvidenceIds'])
        assert not data['factEvidenceIds']['EXPECTED_FARE']
    outcome=AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(payload),context)
    assert not outcome.verified_claims and outcome.rejected_claims[0].reason==reason

def test_multiple_claims_and_nested_citations_have_separate_declared_sets():
    context=_context()
    props=[]
    for index,fact in enumerate(('EXPECTED_FARE','ACTUAL_DISTANCE_KM')):
        props.append({'propositionId':f'P{index}','kind':'FACT','fact':fact,'value':fact_values(context.facts)[fact],'operator':'EQUALS','evidenceIds':supporting_evidence_ids(context,fact)})
    payload=output([props[0]])
    second=deepcopy(output([props[1]])['claims'][0]);second['claimId']='C2';payload['claims'].append(second)
    assert payload['claims'][0]['evidenceIds']!=payload['claims'][1]['evidenceIds']
    outcome=AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(payload),context)
    assert len(outcome.verified_claims)==2 and not outcome.rejected_claims
    nested=AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(output(props)),context)
    assert len(nested.verified_claims)==1 and not nested.rejected_claims

def test_supported_abstention_is_not_an_uncited_claim_and_is_checkpointed(tmp_path):
    class Abstain(ControlledProvider):
        def complete(self,request):
            result=super().complete(request)
            if request.metadata.get('side')=='RIDER' and request.metadata.get('role','advocate')=='advocate':
                payload=json.loads(result.raw_text);payload['claims']=[];payload['summary']='Insufficient verified evidence for additional material assertions.'
                return replace(result,raw_text=json.dumps(payload))
            return result
    result=execute(tmp_path,Abstain())
    assert result.rider.status=='COMPLETE' and not result.rider.rejected_claims
    assert list(tmp_path.rglob('rider_advocate.json'))

def test_pinned_sdk_wire_uses_low_4096_and_unchanged_schema():
    _,prompt=contract(_context())
    schema=provider_schema_for(AdvocateOutput)
    request=AgentCompletionRequest(system_prompt='Synthetic offline system.',user_prompt=prompt,response_schema=schema,max_tokens=4096,extra_body={'reasoning_effort':'low'})
    client=_default_client_factory(api_key='OFFLINE_FAKE_KEY',timeout_seconds=60)
    original=client._api_client._httpx_client;seen=[]
    def capture(req):
        body=json.loads(req.content);config=body['generationConfig']
        thinking=config['thinkingConfig'];assert str(thinking.get('thinkingLevel',thinking.get('thinking_level'))).upper()=='LOW'
        assert config['maxOutputTokens']==4096 and config['responseMimeType']=='application/json'
        assert config['responseJsonSchema']['required']==schema['required']
        assert req.headers['x-server-timeout']=='60' and client._api_client._http_options.retry_options.attempts==1
        assert body['contents'][0]['parts'][0]['text']==prompt
        assert len(req.content)<65536
        seen.append(True)
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{}'}]},'finishReason':'STOP'}]})
    client._api_client._httpx_client=httpx.Client(transport=httpx.MockTransport(capture))
    try:
        provider=GeminiNativeProvider(model='gemini-3.5-flash-lite',api_key='OFFLINE_FAKE_KEY',timeout_seconds=60,max_retries=0)
        client.models.generate_content(model=provider.model_name,contents=prompt,config=provider.build_config(request))
        assert len(seen)==1
    finally:client.close();original.close()
