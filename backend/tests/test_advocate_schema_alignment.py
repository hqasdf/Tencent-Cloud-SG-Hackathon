"""Offline schema regressions. Samples reproduce reported shapes, not lost live JSON."""
import json
from copy import deepcopy

import pytest

from app.agents.base_advocate import AdvocateAgentError
from app.agents.claim_verification import AdvocateClaimVerificationService, fact_values
from app.agents.provider_schema import provider_schema_for
from app.agents.providers.gemini_native import gemini_response_json_schema
from app.agents.rider_advocate import RiderAdvocateAgent
from app.agents.semantic_registry import CAUSAL_RELATIONS, supporting_evidence_ids
from app.models.advocate import AdvocateOutput
from test_gemini_native import _context, _provider, _Response, _advocate_request


def output(propositions, side="RIDER"):
    evidence = sorted({e for p in propositions for e in p.get("evidenceIds", [])})
    return {
        "contractVersion": "atomic-propositions-v2", "side": side,
        "summary": "Evidence-based argument.", "contextAcknowledged": True,
        "requestedOutcome": "NO_REFUND", "claims": [{
            "claimId": "C1", "claim": "A test assertion", "evidenceIds": evidence,
            "policyRefs": [_context().policy.rules[0].rule_id], "reasoningSummary": "Check the case evidence.",
            "importance": "HIGH", "propositions": propositions,
        }],
    }


def fact_proposition():
    context = _context()
    return {"propositionId": "P1", "kind": "FACT", "fact": "EXPECTED_FARE",
            "value": context.facts.expected_fare, "operator": "EQUALS",
            "evidenceIds": supporting_evidence_ids(context, "EXPECTED_FARE")}


def test_gemini_preserves_singleton_literals_recursively_without_mutating_schema():
    schema = provider_schema_for(AdvocateOutput)
    original = deepcopy(schema)
    native = gemini_response_json_schema(schema)
    assert schema == original
    version = native["properties"]["contractVersion"]
    assert version["anyOf"] == [{"type": "string", "enum": ["atomic-propositions-v2"]}]
    assert "contractVersion" in native["required"]
    props = native["properties"]["claims"]["items"]["properties"]["propositions"]["items"]
    operator = props["properties"]["operator"]
    assert operator["anyOf"][0]["enum"] == ["EQUALS"]
    assert '"const"' not in json.dumps(native)


@pytest.mark.parametrize("kind,required", [
    ("FACT", {"kind", "fact", "value", "operator", "evidenceIds"}),
    ("CAUSAL", {"kind", "causeFacts", "effectFact", "relationId", "evidenceIds"}),
    ("POLICY_APPLICATION", {"kind", "policyRuleId", "assertedRulePassed", "evidenceIds"}),
    ("ADVOCACY_POSITION", {"kind", "position"}),
])
def test_native_schema_requires_each_kind_specific_shape(kind, required):
    native = gemini_response_json_schema(provider_schema_for(AdvocateOutput))
    item = native["properties"]["claims"]["items"]["properties"]["propositions"]["items"]
    branch = next(b for b in item["anyOf"] if b["properties"]["kind"]["enum"] == [kind])
    assert set(branch["required"]) == required
    for name in required - {"kind"}:
        field = branch["properties"][name]
        assert field.get("type") != "null"
        assert all(b.get("type") != "null" for b in field.get("anyOf", []))
    if "evidenceIds" in required:
        assert branch["properties"]["evidenceIds"]["minItems"] == 1
    if kind == "FACT":
        assert branch["properties"]["operator"]["anyOf"][0]["enum"] == ["EQUALS"]
        assert {b["type"] for b in branch["properties"]["value"]["anyOf"]} == {"number", "integer", "boolean", "string"}


def test_correct_fact_still_passes_local_parser_and_deterministic_verification():
    provider, _, _ = _provider()
    parsed = RiderAdvocateAgent(provider)._parse(json.dumps(output([fact_proposition()])))
    checked = AdvocateClaimVerificationService().verify(parsed, _context())
    assert len(checked.verified_claims) == 1
    assert checked.rejected_claims == []


@pytest.mark.parametrize("omit", ["fact", "value", "operator"])
def test_reported_driver_fact_missing_fields_remains_rejected(omit):
    proposition = fact_proposition()
    proposition.pop(omit)
    # Historical/audit record parsing remains possible; malformed facts never become trusted.
    parsed = AdvocateOutput.model_validate(output([proposition], side="DRIVER"))
    checked = AdvocateClaimVerificationService().verify(parsed, _context())
    assert checked.verified_claims == []
    assert checked.rejected_claims[0].reason == "MALFORMED_PROPOSITION"
    assert "FACT requires fact, value, and operator=EQUALS" in checked.rejected_claims[0].detail


def test_causal_explanation_mislabeled_fact_is_rejected_not_reclassified():
    proposition = {"propositionId": "P1", "kind": "FACT", "relationId": "TRAFFIC_CAUSED_DETOUR",
                   "causeFacts": ["TRAFFIC"], "effectFact": "ACTUAL_DISTANCE_KM",
                   "evidenceIds": supporting_evidence_ids(_context(), "ACTUAL_DISTANCE_KM")}
    checked = AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(output([proposition], "DRIVER")), _context())
    assert not checked.verified_claims
    assert checked.rejected_claims[0].reason == "MALFORMED_PROPOSITION"
    proposition["kind"] = "CAUSAL"
    checked = AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(output([proposition], "DRIVER")), _context())
    assert checked.rejected_claims[0].reason == "UNREGISTERED_CAUSAL_RELATION"


def test_representative_four_union_errors_report_paths_and_codes_without_input():
    # The live Rider JSON was not saved. This fixture demonstrates a four-error
    # scalar-union failure; it is not asserted to be that lost live response.
    proposition = fact_proposition()
    sensitive_input = "PRIVATE_ALLEGATION_DO_NOT_ECHO"
    proposition["value"] = {"cause": sensitive_input}
    agent = RiderAdvocateAgent(_provider()[0])
    with pytest.raises(AdvocateAgentError) as caught:
        agent._parse(json.dumps(output([proposition])))
    message = str(caught.value)
    assert caught.value.code == "SCHEMA_VALIDATION_FAILED"
    assert "4 issue(s)" in message
    for variant in ["float", "int", "bool", "str"]:
        assert f"claims.0.propositions.0.value.{variant}" in message
    assert sensitive_input not in message
    assert '"cause"' not in message


def test_two_invalid_answers_exhaust_one_repair_without_accepting_malformed_output():
    proposition = fact_proposition()
    proposition["operator"] = "CAUSES"
    provider, client, _ = _provider(response=_Response(text=json.dumps(output([proposition]))))
    with pytest.raises(AdvocateAgentError) as caught:
        RiderAdvocateAgent(provider).argue_with_trace(_context())
    assert caught.value.code == "SCHEMA_VALIDATION_FAILED"
    assert len(client.calls) == 2


def test_prompt_supplies_current_fact_and_registered_causal_vocabulary():
    context = _context()
    agent = RiderAdvocateAgent(_provider()[0])
    prompt = agent._user_prompt(context)
    raw = prompt.split("<atomic_proposition_contract>\n")[1].split("\n</atomic_proposition_contract>")[0]
    vocabulary = json.loads(raw)
    assert vocabulary["factNames"] == sorted(fact_values(context.facts))
    assert {r["relationId"] for r in vocabulary["causalRelations"]} == {
        r.relation_id for r in CAUSAL_RELATIONS.values() if r.dispute_type == context.dispute_type
    }
    assert "operator" in agent._system_prompt() and "EQUALS" in agent._system_prompt()
    assert "scalar" in agent._system_prompt()
    assert "interview allegation" in agent._system_prompt()


def test_all_repaired_constraints_reach_native_sdk_config_offline():
    provider, client, _ = _provider()
    provider.complete(_advocate_request())
    schema = client.calls[0]["config"].response_json_schema
    assert schema == gemini_response_json_schema(provider_schema_for(AdvocateOutput))
    assert "contractVersion" in schema["required"]
    assert len(schema["properties"]["claims"]["items"]["properties"]["propositions"]["items"]["anyOf"]) == 4


def test_registered_causal_calculation_remains_verified():
    context = _context()
    relation = CAUSAL_RELATIONS["ROUTE_DISTANCES_DETERMINE_DISTANCE_DIFFERENCE"]
    evidence = sorted({e for fact in [*relation.cause_facts, relation.effect_fact]
                       for e in supporting_evidence_ids(context, fact)})
    proposition = {"propositionId":"P1", "kind":"CAUSAL", "relationId":relation.relation_id,
                   "causeFacts":sorted(relation.cause_facts), "effectFact":relation.effect_fact,
                   "evidenceIds":evidence}
    checked = AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(output([proposition])), context)
    assert len(checked.verified_claims) == 1 and not checked.rejected_claims


def test_policy_application_and_argument_only_position_keep_distinct_authority():
    context = _context()
    rule = context.policy_evaluation.evaluated_rules[0]
    policy = {"propositionId":"P1", "kind":"POLICY_APPLICATION", "policyRuleId":rule.rule_id,
              "assertedRulePassed":rule.passed, "assertedPolicyOutcome":context.policy_evaluation.overall_outcome,
              "evidenceIds":rule.evidence_ids}
    position = {"propositionId":"P2", "kind":"ADVOCACY_POSITION", "position":"The rider alleges an unrequested detour."}
    payload = output([policy, position])
    payload["claims"][0]["policyRefs"] = [rule.rule_id]
    checked = AdvocateClaimVerificationService().verify(AdvocateOutput.model_validate(payload), context)
    assert len(checked.verified_claims) == 1
    results = checked.verified_claims[0].propositions
    assert [(p.kind,p.status) for p in results] == [("POLICY_APPLICATION","VERIFIED"),("ADVOCACY_POSITION","ARGUMENT_ONLY")]
    assert "unrequested detour" not in checked.verified_claims[0].trusted_summary


def test_generation_schema_never_changes_legacy_audit_loading():
    old = output([fact_proposition()])
    old.pop("contractVersion")
    record = AdvocateOutput.model_validate(old)
    assert record.contract_version is None
    checked = AdvocateClaimVerificationService().verify(record, _context())
    assert not checked.verified_claims
    assert checked.rejected_claims[0].verification_level == "LEGACY_STRUCTURAL_ONLY"
