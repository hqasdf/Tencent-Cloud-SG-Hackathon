"""Phase 2: persisted intake through the production five-role endpoint, offline."""
import json

import pytest
from fastapi.testclient import TestClient

from app.agents.claim_verification import AdvocateClaimVerificationService
from app.agents.context_builder import AdvocateContextBuilder
from app.models.advocate import AdvocateClaim, AdvocateOutput, ClaimProposition
from app.models.intake import PartyFact
from app.repositories.case_repository import MockCaseRepository
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.case_service import CaseService
from app.services.intake_analysis_service import attach_intake_context, IntakeAnalysisError, IntakeAnalysisService
from test_agent_recovery import ControlledProvider, role_of
from test_intake_stabilization import QueueIntakeProvider, _reply
from app.services.conversation_service import ConversationService


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from app.main import app
    import app.api.intake as intake_api
    repo = IntakeCaseRepository(tmp_path / "intake.db")
    monkeypatch.setattr(intake_api, "_repository", repo)
    monkeypatch.setattr(intake_api, "_analysis_service", IntakeAnalysisService(repo))
    monkeypatch.setenv("AGENT_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "controlled")
    monkeypatch.setenv("AGENT_MODEL", "controlled-v1")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "1024")
    provider = ControlledProvider()
    for module in ("advocate_orchestrator", "rebuttal_orchestrator", "resolution_orchestrator"):
        monkeypatch.setattr(f"app.services.{module}.get_provider", lambda _: provider)
    service = CaseService(MockCaseRepository(), intake_repository=repo)
    source = service._load_case("DISP-005")
    with TestClient(app) as client:
        yield repo, service, source, provider, client


def make_intake(repo, source, *, rider=True, driver=True, lifecycle="READY_FOR_ANALYSIS",
                key="account", rider_value="RIDER_PRIVATE", driver_value="DRIVER_PRIVATE"):
    intake = repo.create_case(source_case_id=source.id, trip_id=source.trip.trip_id)
    for party, complete, value in [("rider", rider, rider_value), ("driver", driver, driver_value)]:
        message = repo.add_message(intake_case_id=intake.id, party=party, sender="user", content=value)
        repo.update_state(intake_case_id=intake.id, party=party,
            facts=[PartyFact(id=party+"-fact", key=key, value=value, statedBy=party,
                             sourceMessageId=message.id)],
            missing_details=[] if complete else ["interview incomplete"],
            suggested_dispute_type=source.dispute_type, interview_complete=complete)
    repo.update_lifecycle(intake_case_id=intake.id, lifecycle=lifecycle)
    return repo.get_case(intake.id)


def test_existing_case_without_intake_still_completes_all_five(setup):
    repo, service, source, provider, client = setup
    result = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert result["intakeContext"] is None
    assert result["judge"]["status"] == "COMPLETE"
    assert len(provider.requests) == 5
    assert service.get_analysis(source.id).party_statements == []


def test_completed_intake_reaches_advocates_but_not_judge_and_survives_restart(setup):
    repo, service, source, provider, client = setup
    intake = make_intake(repo, source, key="EXPECTED_FARE", rider_value=str(source.fare.quoted), driver_value=str(source.fare.quoted))
    result = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert result["judge"]["status"] == "COMPLETE"
    assert len(provider.requests) == 5
    for request in provider.requests[:2]:
        context = json.loads(request.metadata["context_json"])
        assert context["intakeContext"]["comparisonAllowed"]
        assert {s["party"] for s in context["intakeContext"]["partyStatements"]} == {"rider", "driver"}
        for s in context["intakeContext"]["partyStatements"]:
            f = s["facts"][0]
            assert f["sourceMessageId"] in {m.id for m in intake.messages}
            assert f["verificationStatus"] == "ALLEGED"
            assert f["evidenceStatus"] == "SUPPORTED_BY_EVIDENCE"
    judge = json.loads(provider.requests[-1].metadata["context_json"])
    assert "intakeContext" not in judge
    assert "partyStatements" not in judge
    restarted = IntakeCaseRepository(repo._db_path)
    assert restarted.get_case(intake.id).rider_state.facts == intake.rider_state.facts
    assert CaseService(MockCaseRepository(), intake_repository=restarted).get_analysis(source.id).intake_context == service.get_analysis(source.id).intake_context


@pytest.mark.parametrize("rider,driver,lifecycle", [
    (True, False, "DRIVER_INTERVIEW"), (False, True, "RIDER_INTERVIEW"),
    (False, False, "RIDER_INTERVIEW"), (True, True, "RIDER_REBUTTAL"),
])
def test_partial_intake_is_explicit_and_opposing_allegations_are_withheld(setup, rider, driver, lifecycle):
    repo, service, source, provider, client = setup
    make_intake(repo, source, rider=rider, driver=driver, lifecycle=lifecycle)
    result = client.post(f"/api/cases/{source.id}/resolution/run").json()
    ctx = result["intakeContext"]
    assert (ctx["riderComplete"], ctx["driverComplete"]) == (rider, driver)
    assert not ctx["comparisonAllowed"]
    assert ctx["partyContradictions"] == []
    assert len(provider.requests) == 5
    for request in provider.requests[:2]:
        payload = json.loads(request.metadata["context_json"])
        side = request.metadata["side"].lower()
        assert [s["party"] for s in payload["intakeContext"]["partyStatements"]] == [side]
        assert ("DRIVER_PRIVATE" if side == "rider" else "RIDER_PRIVATE") not in request.user_prompt


def test_unresolved_contradictions_force_review_without_changing_policy_or_refund(setup):
    repo, service, source, provider, client = setup
    baseline = CaseService(MockCaseRepository()).get_analysis(source.id)
    intake = make_intake(repo, source)
    analysis = service.get_analysis(source.id)
    assert analysis.policy_evaluation == baseline.policy_evaluation
    assert analysis.analysis == baseline.analysis
    assert analysis.resolution_recommendation == baseline.resolution_recommendation
    assert analysis.resolution_mode == "HUMAN_REVIEW"
    c = analysis.party_contradictions[0]
    assert c.status == "DISPUTED"
    assert c.rider_source_message_id and c.driver_source_message_id
    result = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert result["judge"]["status"] == "PENDING_HUMAN_REVIEW"
    assert result["deterministicResolution"]["resolutionMode"] == "HUMAN_REVIEW"
    assert result["deterministicResolution"]["refundAmount"] == baseline.resolution_recommendation.refund_amount
    assert "RIDER_PRIVATE" not in provider.requests[-1].user_prompt
    assert "DRIVER_PRIVATE" not in provider.requests[-1].user_prompt


def test_registered_evidence_resolves_allegation_difference_without_trusting_it(setup):
    repo, service, source, provider, client = setup
    baseline = CaseService(MockCaseRepository()).get_analysis(source.id)
    make_intake(repo, source, key="EXPECTED_FARE", rider_value="999", driver_value=str(source.fare.quoted))
    analysis = service.get_analysis(source.id)
    assert analysis.party_statements[0].facts[0]["evidenceStatus"] == "CONTRADICTED_BY_EVIDENCE"
    c = analysis.party_contradictions[0]
    assert c.status == "RESOLVED_BY_EVIDENCE"
    assert c.supported_value == source.fare.quoted
    assert c.evidence_ids
    assert analysis.resolution_mode == baseline.resolution_mode
    assert analysis.analysis == baseline.analysis
    context = AdvocateContextBuilder().build(source, analysis)
    bad = AdvocateOutput(side="RIDER", summary="Interview proves refund", contractVersion="atomic-propositions-v2",
        requestedOutcome="REFUND", contextAcknowledged=True,
        claims=[AdvocateClaim(claimId="BAD", claim="The quote was 999", reasoningSummary="Interview allegation only", evidenceIds=c.evidence_ids,
            policyRefs=[], importance="HIGH", propositions=[ClaimProposition(
                propositionId="BAD-P", kind="FACT", fact="EXPECTED_FARE", value=999,
                operator="EQUALS", evidenceIds=c.evidence_ids)])])
    verification = AdvocateClaimVerificationService().verify(bad, context)
    assert verification.rejected_claims and not verification.verified_claims
    # Matching the trusted value still cannot turn an interview message into evidence.
    source_id = analysis.party_statements[0].facts[0]["sourceMessageId"]
    claimed = bad.claims[0].model_copy(update={"evidence_ids": [source_id], "propositions": [
        bad.claims[0].propositions[0].model_copy(update={"value": source.fare.quoted, "evidence_ids": [source_id]})
    ]})
    unsupported = AdvocateClaimVerificationService().verify(bad.model_copy(update={"claims": [claimed]}), context)
    assert unsupported.rejected_claims and not unsupported.verified_claims


def test_correction_keeps_history_and_only_active_allegation_flows_downstream(setup):
    repo, service, source, provider, client = setup
    intake = repo.create_case(source_case_id=source.id, trip_id=source.trip.trip_id)
    turns = QueueIntakeProvider([_reply(facts=[{"key": "account", "value": "OLD_ACCOUNT"}])])
    conversation = ConversationService(repo, turns)
    first, _, _ = conversation.send_message(intake_case_id=intake.id, party="rider", content="OLD_ACCOUNT")
    old = first.rider_state.facts[0]
    turns.replies.append(_reply(facts=[{"key":"account","value":"NEW_ACCOUNT","operation":"CORRECT","corrects_fact_id":old.id}]))
    corrected, _, _ = conversation.send_message(intake_case_id=intake.id, party="rider", content="Correction: NEW_ACCOUNT")
    assert len(corrected.rider_state.facts) == 2
    assert not corrected.rider_state.facts[0].active
    result = client.post(f"/api/cases/{source.id}/resolution/run").json()
    facts = result["intakeContext"]["partyStatements"][0]["facts"]
    assert [f["value"] for f in facts] == ["NEW_ACCOUNT"]
    assert facts[0]["supersedesFactId"] == old.id
    assert "OLD_ACCOUNT" not in provider.requests[0].user_prompt


def test_intake_change_invalidates_all_downstream_checkpoints_and_unchanged_retry_reuses(setup):
    repo, service, source, provider, client = setup
    intake = make_intake(repo, source, rider_value="same", driver_value="same")
    first = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert len(provider.requests) == 5
    provider.requests.clear()
    second = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert provider.requests == []
    assert len(second["replayMetadata"]["reusedRoles"]) == 5
    message = repo.add_message(intake_case_id=intake.id, party="rider", sender="user", content="new")
    repo.update_state(intake_case_id=intake.id, party="rider", facts=[PartyFact(
        id="new", key="account", value="same but clarified", statedBy="rider", sourceMessageId=message.id)],
        missing_details=[], suggested_dispute_type=source.dispute_type, interview_complete=True)
    third = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert len(provider.requests) == 5
    assert third["replayMetadata"]["reusedRoles"] == []


def test_partial_provider_failure_reuses_valid_intake_bound_advocate(setup):
    repo, service, source, provider, client = setup
    make_intake(repo, source, rider_value="same", driver_value="same")
    provider.fail = {"DRIVER_ADVOCATE"}
    first = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert first["workflow"]["judge"]["status"] == "BLOCKED"
    provider.fail.clear()
    provider.requests.clear()
    second = client.post(f"/api/cases/{source.id}/resolution/run").json()
    assert second["workflow"]["advocates"]["rider"]["reused"]
    assert {role_of(r) for r in provider.requests} == {"DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL", "JUDGE"}


def test_wrong_case_or_trip_mapping_is_never_attached(setup):
    repo, service, source, provider, client = setup
    intake = make_intake(repo, source)
    baseline = CaseService(MockCaseRepository()).get_analysis(source.id)
    with pytest.raises(IntakeAnalysisError):
        attach_intake_context(baseline, intake.model_copy(update={"source_case_id": "DISP-002"}))
    with pytest.raises(IntakeAnalysisError):
        attach_intake_context(baseline, intake.model_copy(update={"trip_id": "TRIP-2026-09945"}))


def test_interview_endpoints_clarification_refresh_analysis_and_five_agent_run(setup, monkeypatch):
    repo, service, source, provider, client = setup
    import app.api.intake as intake_api
    turns = QueueIntakeProvider([
        _reply(complete=True, facts=[{"key":"account","value":"Rider account"}]),
        _reply(complete=True, facts=[{"key":"account","value":"Driver account"}]),
        _reply(facts=[{"key":"clarification","value":"No more details"}]),
    ])
    monkeypatch.setattr(intake_api, "_conversation_service", ConversationService(repo, turns))
    created = client.post(f"/api/intake/by-source/{source.id}").json()
    path = f"/api/intake/cases/{created['id']}"
    rider = client.post(path+"/messages", json={"party":"rider","content":"Rider account"})
    assert rider.status_code == 200
    assert rider.json()["case"]["lifecycle"] == "DRIVER_INTERVIEW"
    driver = client.post(path+"/messages", json={"party":"driver","content":"Driver account"})
    assert driver.status_code == 200
    assert driver.json()["case"]["lifecycle"] == "RIDER_REBUTTAL"
    assert client.post(path+"/analyse").status_code == 409
    clarification = client.post(path+"/messages", json={"party":"rider","content":"No more details"})
    assert clarification.json()["case"]["lifecycle"] == "READY_FOR_ANALYSIS"
    refreshed = client.get(path).json()
    assert refreshed["riderState"]["interviewComplete"] and refreshed["driverState"]["interviewComplete"]
    analysis = client.post(path+"/analyse")
    assert analysis.status_code == 200
    assert analysis.json()["resolutionMode"] == "HUMAN_REVIEW"
    result = client.post(f"/api/cases/{source.id}/resolution/run")
    assert result.status_code == 200
    assert result.json()["intakeContext"]["comparisonAllowed"]
    assert len(provider.requests) == 5
    assert result.json()["judge"]["status"] == "PENDING_HUMAN_REVIEW"
    assert "Rider account" not in turns.calls[1]["user_prompt"]
    assert "Driver account" not in turns.calls[2]["user_prompt"]
    assert client.get(f"/api/cases/{source.id}").json()["resolution"]["mode"] == "HUMAN_REVIEW"


def test_conflicting_evidence_cannot_resolve_party_disagreement(setup):
    repo, service, source, provider, client = setup
    intake = make_intake(repo, source, key="EXPECTED_FARE", rider_value="999", driver_value=str(source.fare.quoted))
    baseline = CaseService(MockCaseRepository()).get_analysis(source.id)
    conflicting = baseline.model_copy(update={
        "analysis": baseline.analysis.model_copy(update={"contradictory_evidence": True}),
        "resolution_mode": "HUMAN_REVIEW",
    })
    result = attach_intake_context(conflicting, intake)
    assert result.party_contradictions[0].status == "DISPUTED"
    assert result.resolution_mode == "HUMAN_REVIEW"
    assert all(f["evidenceStatus"] == "ALLEGED" for s in result.party_statements for f in s.facts)


def test_cross_party_source_reference_is_excluded_from_allegations_and_contradictions(setup):
    repo, service, source, provider, client = setup
    intake = make_intake(repo, source)
    driver_source = intake.driver_state.facts[0].source_message_id
    repo.update_state(intake_case_id=intake.id, party="rider", facts=[PartyFact(
        id="invalid", key="account", value="WRONG_SOURCE", statedBy="rider", sourceMessageId=driver_source)],
        missing_details=[], suggested_dispute_type=source.dispute_type, interview_complete=True)
    result = service.get_analysis(source.id)
    assert all(f["value"] != "WRONG_SOURCE" for s in result.party_statements for f in s.facts)
    assert result.party_contradictions == []
