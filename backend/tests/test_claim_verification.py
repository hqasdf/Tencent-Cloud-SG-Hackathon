"""Tests for deterministic verification of AI-generated claims.

This is the second safety-critical unit of Stage 4. The most important test in
this file is the deliberate hallucination test: an agent cites an evidence ID
that does not exist, and the claim must be rejected exactly as produced, remain
visible in the audit output, and never appear in verifiedClaims.
"""

from app.agents.claim_verification import (
    DUPLICATE_CLAIM_ID,
    EVIDENCE_ID_NOT_FOUND,
    EVIDENCE_NOT_VERIFIED,
    FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS,
    NO_EVIDENCE_REFERENCE,
    POLICY_REF_NOT_FOUND,
    UNKNOWN_ASSERTED_FACT,
    AdvocateClaimVerificationService,
)
from app.agents.context_builder import AdvocateContextBuilder
from app.data.cases import MOCK_CASES
from app.models.advocate import AdvocateClaim, AdvocateOutput, AssertedFact
from app.models.agent import AgentCaseContext
from app.services.dispute_analysis import DisputeAnalysisService

VERIFIER = AdvocateClaimVerificationService()


def context_for(case_id: str) -> AgentCaseContext:
    case = next(item for item in MOCK_CASES if item.id == case_id)
    analysis = DisputeAnalysisService().analyze(case)
    return AdvocateContextBuilder().build(case, analysis)


ROUTE_CASE = "CASE-2026-1041"
NO_SHOW_CASE = "CASE-2026-1043"


def claim(
    claim_id: str = "C1",
    *,
    text: str = "The route deviated.",
    evidence: list[str] | None = None,
    policy: list[str] | None = None,
    asserted: list[AssertedFact] | None = None,
    disputed: list[str] | None = None,
) -> AdvocateClaim:
    """Build a well-formed claim.

    The default policy ref is route-specific, so tests that target a no-show case
    must pass an explicit no-show rule. Use ``policy=[]`` to test the
    POLICY_REF_NOT_FOUND path deliberately.
    """
    return AdvocateClaim(
        claim_id=claim_id,
        claim=text,
        evidence_ids=evidence if evidence is not None else ["E03"],
        policy_refs=policy if policy is not None else ["ROUTE_UNEXPLAINED_DEVIATION"],
        reasoning_summary="Because the trusted facts say so.",
        importance="HIGH",
        asserted_facts=asserted or [],
        disputed_evidence_ids=disputed or [],
    )


NO_SHOW_POLICY = "NO_SHOW_WAIT_TIME"


def output(side: str, claims: list[AdvocateClaim]) -> AdvocateOutput:
    return AdvocateOutput(
        side=side,
        summary="Summary.",
        claims=claims,
        requested_outcome="PARTIAL_REFUND",
        context_acknowledged=True,
    )


# ---------------------------------------------------------------------------
# The deliberate hallucination test (mandatory for Milestone 4)
# ---------------------------------------------------------------------------

def test_hallucinated_evidence_id_is_rejected_and_never_verified() -> None:
    """An agent citing a non-existent evidence ID must be caught by CODE."""
    context = context_for(ROUTE_CASE)
    hallucinated = claim("C1", evidence=["E_DOES_NOT_EXIST"])

    outcome = VERIFIER.verify(output("RIDER", [hallucinated]), context)

    # Nothing is verified.
    assert outcome.verified_claims == []

    # The claim is rejected, with a machine-readable reason and a human detail.
    assert len(outcome.rejected_claims) == 1
    rejection = outcome.rejected_claims[0]
    assert rejection.reason == EVIDENCE_ID_NOT_FOUND
    assert "E_DOES_NOT_EXIST" in rejection.detail

    # The rejection preserves the claim EXACTLY as produced for audit: it is
    # never repaired, rewritten, or dropped.
    assert rejection.claim_id == "C1"
    assert rejection.claim == hallucinated.claim
    assert rejection.evidence_ids == ["E_DOES_NOT_EXIST"]


def test_hallucinated_short_evidence_id_is_also_rejected() -> None:
    """The E99 form from the brief."""
    context = context_for(ROUTE_CASE)
    outcome = VERIFIER.verify(output("RIDER", [claim("C1", evidence=["E99"])]), context)
    assert outcome.verified_claims == []
    assert outcome.rejected_claims[0].reason == EVIDENCE_ID_NOT_FOUND


def test_evidence_from_another_case_is_not_accepted() -> None:
    """An ID that exists in the registry but not in this case is rejected."""
    other = next(item for item in MOCK_CASES if item.id == NO_SHOW_CASE)
    foreign_id = other.evidence[0].id

    context = context_for(ROUTE_CASE)
    local_ids = {item.id for item in context.evidence}
    if foreign_id in local_ids:  # pragma: no cover - fixture guard
        foreign_id = f"{foreign_id}X"

    outcome = VERIFIER.verify(output("RIDER", [claim("C1", evidence=[foreign_id])]), context)
    assert outcome.verified_claims == []
    assert outcome.rejected_claims[0].reason in (
        EVIDENCE_ID_NOT_FOUND,
        "EVIDENCE_NOT_IN_CASE",
    )


def test_well_formed_claim_is_verified() -> None:
    context = context_for(ROUTE_CASE)
    good = claim(
        "C1",
        evidence=["E03"],
        asserted=[AssertedFact(fact="UNEXPLAINED_DEVIATION_KM", value=0.8)],
    )
    outcome = VERIFIER.verify(output("RIDER", [good]), context)
    assert [item.claim_id for item in outcome.verified_claims] == ["C1"]
    assert outcome.rejected_claims == []


# ---------------------------------------------------------------------------
# Structured fact verification — the hard layer
# ---------------------------------------------------------------------------

def test_fact_contradiction_is_hard_rejected() -> None:
    """A structured fact that contradicts the deterministic value is rejected."""
    context = context_for(ROUTE_CASE)
    lying = claim(
        "C1",
        asserted=[AssertedFact(fact="EXPLAINED_DEVIATION_KM", value=1.3)],
    )
    outcome = VERIFIER.verify(output("RIDER", [lying]), context)
    assert outcome.verified_claims == []
    assert outcome.rejected_claims[0].reason == FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS


def test_float_values_within_tolerance_are_accepted() -> None:
    context = context_for(ROUTE_CASE)
    close_enough = claim(
        "C1",
        asserted=[AssertedFact(fact="EXPLAINED_DEVIATION_KM", value=0.5000000001)],
    )
    outcome = VERIFIER.verify(output("RIDER", [close_enough]), context)
    assert [item.claim_id for item in outcome.verified_claims] == ["C1"]


def test_boolean_fact_is_compared_strictly() -> None:
    context = context_for(NO_SHOW_CASE)
    wrong_bool = claim(
        "C1",
        evidence=["E03"],
        policy=["NO_SHOW_PICKUP_RADIUS"],
        asserted=[AssertedFact(fact="WITHIN_PICKUP_RADIUS", value=False)],
    )
    outcome = VERIFIER.verify(output("DRIVER", [wrong_bool]), context)
    assert outcome.verified_claims == []
    assert outcome.rejected_claims[0].reason == FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS


def test_unknown_asserted_fact_name_is_rejected() -> None:
    context = context_for(ROUTE_CASE)
    invented = claim(
        "C1",
        asserted=[AssertedFact(fact="DRIVER_WAS_RUDE", value=True)],
    )
    outcome = VERIFIER.verify(output("RIDER", [invented]), context)
    assert outcome.verified_claims == []
    assert outcome.rejected_claims[0].reason == UNKNOWN_ASSERTED_FACT


def test_route_only_fact_is_unknown_for_no_show_case() -> None:
    """Fact vocabulary is scoped per dispute type."""
    context = context_for(NO_SHOW_CASE)
    wrong_domain = claim(
        "C1",
        evidence=["E03"],
        policy=["NO_SHOW_WAIT_TIME"],
        asserted=[AssertedFact(fact="DEVIATION_PERCENTAGE", value=22.41)],
    )
    outcome = VERIFIER.verify(output("DRIVER", [wrong_domain]), context)
    assert outcome.rejected_claims[0].reason == UNKNOWN_ASSERTED_FACT


# ---------------------------------------------------------------------------
# Structural claim discipline
# ---------------------------------------------------------------------------

def test_claim_without_evidence_reference_is_rejected() -> None:
    context = context_for(ROUTE_CASE)
    outcome = VERIFIER.verify(output("RIDER", [claim("C1", evidence=[])]), context)
    assert outcome.rejected_claims[0].reason == NO_EVIDENCE_REFERENCE


def test_claim_with_unknown_policy_ref_is_rejected() -> None:
    context = context_for(ROUTE_CASE)
    outcome = VERIFIER.verify(
        output("RIDER", [claim("C1", policy=["POLICY_I_INVENTED"])]), context
    )
    assert outcome.rejected_claims[0].reason == POLICY_REF_NOT_FOUND


def test_claim_without_policy_ref_is_rejected() -> None:
    context = context_for(ROUTE_CASE)
    outcome = VERIFIER.verify(output("RIDER", [claim("C1", policy=[])]), context)
    assert outcome.rejected_claims[0].reason == POLICY_REF_NOT_FOUND


def test_duplicate_claim_ids_are_rejected_on_second_occurrence() -> None:
    context = context_for(ROUTE_CASE)
    duplicate = claim("C1")
    outcome = VERIFIER.verify(output("RIDER", [duplicate, duplicate]), context)
    assert [item.claim_id for item in outcome.verified_claims] == ["C1"]
    assert outcome.rejected_claims[0].reason == DUPLICATE_CLAIM_ID


def test_claim_resting_only_on_unverified_evidence_is_rejected() -> None:
    """A claim built only on Missing/Conflicting evidence is not established fact."""
    context = context_for("CASE-2026-1044")
    unusable = [
        item.id for item in context.evidence if item.status in ("Missing", "Conflicting")
    ]
    assert unusable, "fixture must contain unverified evidence"

    bad = claim("C1", evidence=[unusable[0]], policy=[NO_SHOW_POLICY])
    outcome = VERIFIER.verify(output("RIDER", [bad]), context)
    assert outcome.rejected_claims[0].reason == EVIDENCE_NOT_VERIFIED


def test_claim_with_at_least_one_verified_evidence_passes_the_quality_check() -> None:
    context = context_for("CASE-2026-1044")
    usable = [item.id for item in context.evidence if item.status == "Verified"]
    unusable = [
        item.id for item in context.evidence if item.status in ("Missing", "Conflicting")
    ]
    assert usable and unusable, "fixture must contain both kinds of evidence"

    mixed = claim("C1", evidence=[usable[0], unusable[0]], policy=[NO_SHOW_POLICY])
    outcome = VERIFIER.verify(output("RIDER", [mixed]), context)
    assert [item.claim_id for item in outcome.verified_claims] == ["C1"]


# ---------------------------------------------------------------------------
# Prose contradictions are warnings only
# ---------------------------------------------------------------------------

def test_numeric_prose_without_asserted_facts_produces_a_warning() -> None:
    context = context_for(ROUTE_CASE)
    vague = claim("C1", text="The trip was 3 km longer than it should have been.")
    outcome = VERIFIER.verify(output("RIDER", [vague]), context)

    # Not a rejection: Milestone 4 does not use a second AI to judge prose.
    assert [item.claim_id for item in outcome.verified_claims] == ["C1"]
    assert outcome.rejected_claims == []
    assert any(item.code == "UNVERIFIED_NUMERIC_PROSE" for item in outcome.warnings)


def test_prose_numbers_backed_by_asserted_facts_do_not_warn_numerically() -> None:
    context = context_for(ROUTE_CASE)
    backed = claim(
        "C1",
        text="The trip was 0.8 km unexplained.",
        asserted=[AssertedFact(fact="UNEXPLAINED_DEVIATION_KM", value=0.8)],
    )
    outcome = VERIFIER.verify(output("RIDER", [backed]), context)
    assert outcome.verified_claims[0].claim_id == "C1"
    assert not any(item.code == "UNVERIFIED_NUMERIC_PROSE" for item in outcome.warnings)


def test_disputing_evidence_produces_a_warning_not_a_rejection() -> None:
    context = context_for(ROUTE_CASE)
    disputing = claim("C1", disputed=["E06"])
    outcome = VERIFIER.verify(output("RIDER", [disputing]), context)
    assert [item.claim_id for item in outcome.verified_claims] == ["C1"]
    assert any(item.code == "EVIDENCE_DISPUTED_BY_ADVOCATE" for item in outcome.warnings)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def test_summary_reports_counts_and_deduplicated_reasons() -> None:
    context = context_for(ROUTE_CASE)
    claims = [
        claim("C1"),
        claim("C2", evidence=["E99"]),
        claim("C3", evidence=["E98"]),
    ]
    outcome = VERIFIER.verify(output("RIDER", claims), context)
    summary = outcome.summary()
    assert summary.verified_count == 1
    assert summary.rejected_count == 2
    assert summary.rejection_reasons == [EVIDENCE_ID_NOT_FOUND]
