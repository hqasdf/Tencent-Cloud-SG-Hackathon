from __future__ import annotations

from dataclasses import dataclass, field

from app.models.advocate import (
    AdvocateClaim,
    AdvocateOutput,
    ClaimRejection,
    ClaimWarning,
    VerificationSummary,
)
from app.models.agent import (
    AgentCaseContext,
    NoShowFacts,
    RouteDeviationFacts,
)

# Machine-readable rejection codes.
MALFORMED_ADVOCATE_OUTPUT = "MALFORMED_ADVOCATE_OUTPUT"
WRONG_SIDE = "WRONG_SIDE"
DUPLICATE_CLAIM_ID = "DUPLICATE_CLAIM_ID"
EVIDENCE_ID_NOT_FOUND = "EVIDENCE_ID_NOT_FOUND"
EVIDENCE_NOT_IN_CASE = "EVIDENCE_NOT_IN_CASE"
EVIDENCE_NOT_VERIFIED = "EVIDENCE_NOT_VERIFIED"
NO_EVIDENCE_REFERENCE = "NO_EVIDENCE_REFERENCE"
POLICY_REF_NOT_FOUND = "POLICY_REF_NOT_FOUND"
POLICY_NOT_APPLICABLE = "POLICY_NOT_APPLICABLE"
FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS = "FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS"
UNKNOWN_ASSERTED_FACT = "UNKNOWN_ASSERTED_FACT"

FLOAT_TOLERANCE = 1e-6

# Evidence IDs that exist somewhere in the synthetic backend dataset. Used only
# to distinguish "this ID belongs to another case" from "this ID does not exist
# at all". Imported lazily to avoid a circular import at module load.
def _registry_ids() -> frozenset[str]:
    from app.data.cases import MOCK_CASES

    return frozenset(item.id for case in MOCK_CASES for item in case.evidence)


_KNOWN_REGISTRY_IDS: frozenset[str] = _registry_ids()

# Fact names an agent may assert, mapped to how they are read from the trusted context.
ROUTE_FACT_KEYS = {
    "EXPECTED_DISTANCE_KM": "expected_route_distance_km",
    "ACTUAL_DISTANCE_KM": "actual_route_distance_km",
    "DISTANCE_DIFFERENCE_KM": "distance_difference_km",
    "DEVIATION_PERCENTAGE": "deviation_percentage",
    "EXPECTED_DURATION_MINUTES": "expected_trip_duration_minutes",
    "ACTUAL_DURATION_MINUTES": "actual_trip_duration_minutes",
    "DURATION_DIFFERENCE_MINUTES": "duration_difference_minutes",
    "EXPECTED_FARE": "expected_fare",
    "ACTUAL_FARE": "actual_fare",
    "FARE_DIFFERENCE": "fare_difference",
    "EXPLAINED_DEVIATION_KM": "explained_deviation_distance_km",
    "UNEXPLAINED_DEVIATION_KM": "unexplained_deviation_distance_km",
}

NO_SHOW_FACT_KEYS = {
    "DRIVER_PICKUP_DISTANCE_METERS": "driver_distance_to_pickup_meters",
    "WITHIN_PICKUP_RADIUS": "driver_within_pickup_radius",
    "WAITING_DURATION_SECONDS": "waiting_duration_seconds",
    "CANCELLATION_CHARGE_AMOUNT": "cancellation_charge_amount",
}


@dataclass
class VerificationOutcome:
    verified_claims: list[AdvocateClaim] = field(default_factory=list)
    rejected_claims: list[ClaimRejection] = field(default_factory=list)
    warnings: list[ClaimWarning] = field(default_factory=list)

    def summary(self) -> VerificationSummary:
        return VerificationSummary(
            verified_count=len(self.verified_claims),
            rejected_count=len(self.rejected_claims),
            rejection_reasons=sorted({claim.reason for claim in self.rejected_claims}),
        )


class AdvocateClaimVerificationService:
    """Deterministic verification of AI-generated claims.

    This is intentionally separate from EvidenceValidationService:

      EvidenceValidationService   validates the CASE RECORD, before analysis.
      AdvocateClaimVerificationService validates AI-GENERATED CLAIMS, after agents run.

    A rejected claim is never repaired, rewritten, or dropped. If an agent cites
    E99 and E99 does not exist, the claim is rejected exactly as produced and
    remains visible for audit.
    """

    def verify(self, output: AdvocateOutput, context: AgentCaseContext) -> VerificationOutcome:
        outcome = VerificationOutcome()
        evidence_ids = {item.id for item in context.evidence}
        evidence_status = {item.id: item.status for item in context.evidence}
        policy_refs = {rule.rule_id for rule in context.policy.rules}
        trusted = _trusted_facts(context)
        seen_claim_ids: set[str] = set()

        for claim in output.claims:
            rejection = self._reject_claim(
                claim=claim,
                expected_side=output.side,
                actual_side=output.side,
                evidence_ids=evidence_ids,
                evidence_status=evidence_status,
                policy_refs=policy_refs,
                context=context,
                trusted=trusted,
                seen_claim_ids=seen_claim_ids,
            )
            if rejection is not None:
                outcome.rejected_claims.append(rejection)
                continue

            outcome.verified_claims.append(claim)
            seen_claim_ids.add(claim.claim_id)
            outcome.warnings.extend(self._prose_warnings(claim))

        return outcome

    def _reject_claim(
        self,
        *,
        claim: AdvocateClaim,
        expected_side: str,
        actual_side: str,
        evidence_ids: set[str],
        evidence_status: dict[str, str],
        policy_refs: set[str],
        context: AgentCaseContext,
        trusted: dict[str, object],
        seen_claim_ids: set[str],
    ) -> ClaimRejection | None:
        def reject(reason: str, detail: str) -> ClaimRejection:
            return ClaimRejection(
                claim_id=claim.claim_id,
                claim=claim.claim,
                reason=reason,
                detail=detail,
                evidence_ids=list(claim.evidence_ids),
                policy_refs=list(claim.policy_refs),
            )

        if expected_side != actual_side:
            return reject(
                WRONG_SIDE,
                f"Claim {claim.claim_id} was produced for side {actual_side}.",
            )

        if claim.claim_id in seen_claim_ids:
            return reject(
                DUPLICATE_CLAIM_ID,
                f"Claim ID {claim.claim_id} appears more than once in this advocate output.",
            )

        # Evidence references.
        if not claim.evidence_ids:
            return reject(
                NO_EVIDENCE_REFERENCE,
                f"Claim {claim.claim_id} makes a factual assertion but cites no evidence.",
            )
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_ids:
                # Unknown IDs are reported as EVIDENCE_ID_NOT_FOUND. EVIDENCE_NOT_IN_CASE
                # covers a registry-wide ID that exists but is not part of this case,
                # which is unreachable today because the context carries only its own
                # evidence; the check exists so a future shared registry cannot leak.
                if evidence_id in _KNOWN_REGISTRY_IDS:
                    return reject(
                        EVIDENCE_NOT_IN_CASE,
                        f"{evidence_id} is not evidence for case {context.case_id}.",
                    )
                return reject(
                    EVIDENCE_ID_NOT_FOUND,
                    f"{evidence_id} does not exist in this case record.",
                )

        # Policy references.
        if not claim.policy_refs:
            return reject(
                POLICY_REF_NOT_FOUND,
                f"Claim {claim.claim_id} cites no policy rule.",
            )
        for policy_ref in claim.policy_refs:
            if policy_ref not in policy_refs:
                return reject(
                    POLICY_REF_NOT_FOUND,
                    f"Policy rule {policy_ref} does not exist for case {context.case_id}.",
                )

        if context.policy.dispute_type != context.dispute_type:
            return reject(
                POLICY_NOT_APPLICABLE,
                f"Policy {context.policy.policy_id} does not apply to {context.dispute_type}.",
            )

        # Structured fact verification: the hard layer.
        for asserted in claim.asserted_facts:
            if asserted.fact not in trusted:
                return reject(
                    UNKNOWN_ASSERTED_FACT,
                    f"Asserted fact {asserted.fact} is not a verifiable fact for this dispute type.",
                )
            expected = trusted[asserted.fact]
            if not _values_match(expected, asserted.value):
                return reject(
                    FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS,
                    (
                        f"Asserted {asserted.fact}={asserted.value} contradicts the trusted "
                        f"deterministic value {expected}."
                    ),
                )

        # Evidence quality. A claim must not rest exclusively on evidence that the
        # case record marks Missing or Conflicting, because that is not established
        # fact. The failure is reported per-claim and never repaired.
        unusable = [
            evidence_id
            for evidence_id in claim.evidence_ids
            if evidence_status.get(evidence_id) in ("Missing", "Conflicting")
        ]
        if len(unusable) == len(claim.evidence_ids):
            return reject(
                EVIDENCE_NOT_VERIFIED,
                (
                    "Claim rests only on evidence that is not verified: "
                    + ", ".join(unusable)
                    + "."
                ),
            )

        return None

    @staticmethod
    def _prose_warnings(claim: AdvocateClaim) -> list[ClaimWarning]:
        """Best-effort prose check. Never a rejection.

        Milestone 4 deliberately does not build a second AI system to judge
        whether the first AI hallucinated. Structured facts are enforced; free
        text is only flagged.
        """
        warnings: list[ClaimWarning] = []
        for evidence_id in claim.disputed_evidence_ids:
            warnings.append(
                ClaimWarning(
                    claim_id=claim.claim_id,
                    code="EVIDENCE_DISPUTED_BY_ADVOCATE",
                    detail=f"Advocate disputes evidence {evidence_id}.",
                )
            )
        if not claim.asserted_facts and _mentions_numbers(claim.claim):
            warnings.append(
                ClaimWarning(
                    claim_id=claim.claim_id,
                    code="UNVERIFIED_NUMERIC_PROSE",
                    detail=(
                        "Claim quotes numeric values in prose without structured assertedFacts, "
                        "so they could not be machine-verified."
                    ),
                )
            )
        return warnings


def _mentions_numbers(text: str) -> bool:
    return any(character.isdigit() for character in text)


def _trusted_facts(context: AgentCaseContext) -> dict[str, object]:
    facts = context.facts
    if isinstance(facts, RouteDeviationFacts):
        return {name: getattr(facts, key) for name, key in ROUTE_FACT_KEYS.items()}
    if isinstance(facts, NoShowFacts):
        return {name: getattr(facts, key) for name, key in NO_SHOW_FACT_KEYS.items()}
    return {}


def _values_match(expected: object, actual: object) -> bool:
    """Deterministic comparison with a small tolerance for floats."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        if isinstance(expected, bool) != isinstance(actual, bool):
            return False
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= FLOAT_TOLERANCE
    return expected == actual
