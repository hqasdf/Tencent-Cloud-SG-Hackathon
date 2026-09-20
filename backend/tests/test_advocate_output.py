"""Tests for the shared advocate output schema and the Stage 4 response contract.

The important properties here are:

  * Rider and Driver use the EXACT same schema, discriminated only by ``side``.
  * There is deliberately no ``position`` field in Milestone 4.
  * camelCase payloads round-trip through ``model_validate_json``.
"""

from app.models.advocate import (
    AdvocateClaim,
    AdvocateOutput,
    AdvocateSideResult,
    AssertedFact,
    ClaimRejection,
    ClaimWarning,
    PipelineStage,
    VerificationSummary,
)


def sample_claim(claim_id: str = "R1") -> AdvocateClaim:
    return AdvocateClaim(
        claim_id=claim_id,
        claim="The unexplained deviation was 0.8 km.",
        evidence_ids=["E03"],
        policy_refs=["ROUTE_UNEXPLAINED_DEVIATION"],
        reasoning_summary="Trusted facts show an unexplained remainder.",
        importance="HIGH",
        asserted_facts=[AssertedFact(fact="UNEXPLAINED_DEVIATION_KM", value=0.8)],
        disputed_evidence_ids=[],
    )


def test_both_sides_use_the_identical_schema() -> None:
    rider = AdvocateOutput(
        side="RIDER",
        summary="Rider summary.",
        claims=[sample_claim("R1")],
        requested_outcome="PARTIAL_REFUND",
        context_acknowledged=True,
    )
    driver = AdvocateOutput(
        side="DRIVER",
        summary="Driver summary.",
        claims=[sample_claim("D1")],
        requested_outcome="NO_REFUND",
        context_acknowledged=True,
    )
    assert set(rider.model_dump().keys()) == set(driver.model_dump().keys())
    assert rider.side == "RIDER"
    assert driver.side == "DRIVER"


def test_advocate_output_has_no_position_field() -> None:
    """Milestone 4 deliberately removed `position`."""
    fields = set(AdvocateOutput.model_fields.keys())
    assert "position" not in fields

    claim_fields = set(AdvocateClaim.model_fields.keys())
    assert "position" not in claim_fields


def test_advocate_output_round_trips_camel_case_json() -> None:
    payload = {
        "side": "RIDER",
        "summary": "Rider summary.",
        "claims": [
            {
                "claimId": "R1",
                "claim": "The unexplained deviation was 0.8 km.",
                "evidenceIds": ["E03"],
                "policyRefs": ["ROUTE_UNEXPLAINED_DEVIATION"],
                "reasoningSummary": "Trusted facts show a remainder.",
                "importance": "HIGH",
                "assertedFacts": [{"fact": "UNEXPLAINED_DEVIATION_KM", "value": 0.8}],
                "disputedEvidenceIds": [],
            }
        ],
        "requestedOutcome": "PARTIAL_REFUND",
        "contextAcknowledged": True,
    }
    parsed = AdvocateOutput.model_validate(payload)
    assert parsed.claims[0].claim_id == "R1"
    assert parsed.claims[0].evidence_ids == ["E03"]
    assert parsed.requested_outcome == "PARTIAL_REFUND"
    assert parsed.context_acknowledged is True

    serialized = parsed.model_dump_json(by_alias=True)
    assert '"claimId"' in serialized
    assert '"requestedOutcome"' in serialized
    assert '"contextAcknowledged"' in serialized
    assert "claim_id" not in serialized


def test_side_result_defaults_are_safe_on_failure() -> None:
    failed = AdvocateSideResult(
        side="DRIVER",
        status="FAILED",
        summary="Driver advocate unavailable.",
        failure_reason="Provider request failed.",
    )
    assert failed.verified_claims == []
    assert failed.rejected_claims == []
    assert failed.warnings == []
    assert failed.requested_outcome is None
    assert failed.context_acknowledged is False


def test_rejection_preserves_the_claim_for_audit() -> None:
    rejection = ClaimRejection(
        claim_id="R9",
        claim="The driver took a detour past Marina Bay.",
        reason="EVIDENCE_ID_NOT_FOUND",
        detail="E99 does not exist in this case record.",
        evidence_ids=["E99"],
        policy_refs=["ROUTE_UNEXPLAINED_DEVIATION"],
    )
    assert rejection.evidence_ids == ["E99"]
    assert rejection.reason == "EVIDENCE_ID_NOT_FOUND"


def test_warning_is_non_blocking_and_carries_a_code() -> None:
    warning = ClaimWarning(
        claim_id="R1",
        code="UNVERIFIED_NUMERIC_PROSE",
        detail="Numbers quoted in prose without structured assertedFacts.",
    )
    assert warning.code == "UNVERIFIED_NUMERIC_PROSE"


def test_verification_summary_counts() -> None:
    summary = VerificationSummary(
        verified_count=3,
        rejected_count=1,
        rejection_reasons=["EVIDENCE_ID_NOT_FOUND"],
    )
    assert summary.verified_count == 3
    assert summary.rejected_count == 1


def test_pipeline_stage_literal_values() -> None:
    assert PipelineStage(stage="JUDGE", status="NOT_RUN").status == "NOT_RUN"
    assert PipelineStage(stage="RIDER_ADVOCATE", status="COMPLETE").stage == "RIDER_ADVOCATE"
