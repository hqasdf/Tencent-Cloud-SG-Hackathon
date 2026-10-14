"""Synthetic equivalent of the captured rejection code, not reconstructed live output."""
from app.agents.claim_verification import AdvocateClaimVerificationService
from app.agents.rider_advocate import RiderAdvocateAgent
from app.agents.driver_advocate import DriverAdvocateAgent
from app.agents.providers.mock import MockLlmProvider
from app.models.advocate import AdvocateOutput
from tests.test_advocate_schema_alignment import output, fact_proposition, _context


def test_parent_evidence_declaration_is_required_in_both_prompts():
    for agent in (RiderAdvocateAgent, DriverAdvocateAgent):
        prompt=agent(MockLlmProvider())._system_prompt()
        assert "each proposition's `evidenceIds` must be a subset of its parent claim's `evidenceIds`" in prompt


def test_exact_rejection_remains_strict_and_consistent_ids_pass():
    context=_context()
    proposition=fact_proposition()
    payload=output([proposition])
    valid=AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(payload),context)
    assert len(valid.verified_claims)==1 and not valid.rejected_claims
    replacement=next(e.id for e in context.evidence if e.status=='Verified' and e.id not in proposition['evidenceIds'])
    payload['claims'][0]['evidenceIds']=[replacement]
    rejected=AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(payload),context)
    assert not rejected.verified_claims
    assert rejected.rejected_claims[0].reason=='EVIDENCE_NOT_DECLARED_BY_CLAIM'
