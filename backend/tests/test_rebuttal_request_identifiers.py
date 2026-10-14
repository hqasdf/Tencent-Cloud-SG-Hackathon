"""Request-only identifiers for the captured rebuttal failure; offline."""
import json
from copy import deepcopy
from types import SimpleNamespace
from app.agents.rebuttal_agent import RebuttalAgent
from app.agents.claim_verification import fact_values
from app.agents.providers.gemini_native import gemini_response_json_schema
from tests.test_rebuttal import _rebuttal_context, _single_response_payload, _validate

def test_unknown_fact_stays_rejected_and_concession_is_not_trusted():
    context=_rebuttal_context('DISP-005','RIDER')[0]
    payload=_single_response_payload(context,stance='CONCEDE',assertedFacts=[{'fact':'UNKNOWN_INTERVIEW_ASSERTION','value':True}])
    payload['concessions']=[payload['responses'][0]['targetClaimId']]
    result=_validate(context,payload)
    assert not result.verified_rebuttals
    assert {r.reason for r in result.rejected_rebuttals}=={'UNKNOWN_ASSERTED_FACT','CONCESSION_NOT_A_TARGET'}

def test_native_request_identifiers_are_context_bound_without_mutating_contract():
    context=_rebuttal_context('DISP-005','RIDER')[0]
    agent=RebuttalAgent(SimpleNamespace(name='gemini_native'))
    baseline=deepcopy(agent._output_schema)
    schema=gemini_response_json_schema(agent._request_schema(context))
    response=schema['properties']['responses']['items']
    expected=sorted(c.claim_id for c in context.opposing_verified_claims)
    assert response['properties']['targetClaimId']['enum']==expected
    assert schema['properties']['concessions']['items']['enum']==expected
    assert response['properties']['assertedFacts']['items']['properties']['fact']['enum']==sorted(fact_values(context.deterministic_facts))
    assert agent._output_schema==baseline
    prompt=agent._user_prompt(context)
    vocabulary=json.loads(prompt.split('<asserted_fact_vocabulary>\n')[1].split('\n</asserted_fact_vocabulary>')[0])
    assert vocabulary==fact_values(context.deterministic_facts)
