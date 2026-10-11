"""Deterministic verification of AI-generated Advocate propositions.

The model's prose is retained for audit but is never treated as trusted text.
Only versioned, atomic propositions can become a ``VerifiedClaim``. Code checks
their values, evidence-to-fact coverage, policy result, and registered causal
relationship before generating the summary supplied downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.semantic_registry import (
    CAUSAL_RELATIONS,
    fact_summary,
    supporting_evidence_ids,
)
from app.models.advocate import (
    ATOMIC_PROPOSITION_VERSION,
    AdvocateClaim,
    AdvocateOutput,
    ClaimProposition,
    ClaimRejection,
    ClaimWarning,
    PropositionVerification,
    VerificationSummary,
    VerifiedClaim,
)
from app.models.agent import AgentCaseContext, NoShowFacts, RouteDeviationFacts


# Existing structural codes remain stable for API and replay compatibility.
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

# Atomic semantic codes.
LEGACY_SEMANTIC_UNVERIFIED = "LEGACY_SEMANTIC_UNVERIFIED"
NO_ATOMIC_PROPOSITIONS = "NO_ATOMIC_PROPOSITIONS"
NO_VERIFIABLE_PROPOSITION = "NO_VERIFIABLE_PROPOSITION"
DUPLICATE_PROPOSITION_ID = "DUPLICATE_PROPOSITION_ID"
MALFORMED_PROPOSITION = "MALFORMED_PROPOSITION"
EVIDENCE_NOT_DECLARED_BY_CLAIM = "EVIDENCE_NOT_DECLARED_BY_CLAIM"
EVIDENCE_DOES_NOT_SUPPORT_FACT = "EVIDENCE_DOES_NOT_SUPPORT_FACT"
UNREGISTERED_CAUSAL_RELATION = "UNREGISTERED_CAUSAL_RELATION"
CAUSAL_RELATION_MISMATCH = "CAUSAL_RELATION_MISMATCH"
POLICY_RESULT_MISMATCH = "POLICY_RESULT_MISMATCH"
POLICY_OUTCOME_MISMATCH = "POLICY_OUTCOME_MISMATCH"
POLICY_EVIDENCE_COVERAGE_MISSING = "POLICY_EVIDENCE_COVERAGE_MISSING"
ASSERTED_FACT_NOT_PROPOSITION = "ASSERTED_FACT_NOT_PROPOSITION"

FLOAT_TOLERANCE = 1e-6


def _registry_ids() -> frozenset[str]:
    from app.data.cases import MOCK_CASES

    return frozenset(item.id for case in MOCK_CASES for item in case.evidence)


_KNOWN_REGISTRY_IDS: frozenset[str] = _registry_ids()

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
    verified_claims: list[VerifiedClaim] = field(default_factory=list)
    rejected_claims: list[ClaimRejection] = field(default_factory=list)
    warnings: list[ClaimWarning] = field(default_factory=list)

    def summary(self) -> VerificationSummary:
        return VerificationSummary(
            verified_count=len(self.verified_claims),
            rejected_count=len(self.rejected_claims),
            rejection_reasons=sorted({claim.reason for claim in self.rejected_claims}),
        )


class AdvocateClaimVerificationService:
    """Verify atomic Advocate propositions using current deterministic context."""

    def verify(self, output: AdvocateOutput, context: AgentCaseContext) -> VerificationOutcome:
        outcome = VerificationOutcome()
        seen_claim_ids: set[str] = set()

        for claim in output.claims:
            if claim.claim_id in seen_claim_ids:
                outcome.rejected_claims.append(
                    self._rejection(
                        claim,
                        DUPLICATE_CLAIM_ID,
                        f"Claim ID {claim.claim_id} appears more than once in this Advocate output.",
                    )
                )
                continue
            seen_claim_ids.add(claim.claim_id)

            if output.contract_version != ATOMIC_PROPOSITION_VERSION:
                outcome.rejected_claims.append(
                    self._rejection(
                        claim,
                        LEGACY_SEMANTIC_UNVERIFIED,
                        (
                            "This historical claim predates atomic semantic verification. Its "
                            "original wording is preserved for audit but it is not trusted input."
                        ),
                        verification_level="LEGACY_STRUCTURAL_ONLY",
                    )
                )
                continue

            structural = self._structural_issue(claim, context)
            if structural is not None:
                reason, detail = structural
                outcome.rejected_claims.append(self._rejection(claim, reason, detail))
                continue

            if not claim.propositions:
                outcome.rejected_claims.append(
                    self._rejection(
                        claim,
                        NO_ATOMIC_PROPOSITIONS,
                        "The claim supplied no atomic propositions, so none of its wording can be trusted.",
                    )
                )
                continue

            proposition_results = self._verify_propositions(claim, context)
            material = [
                result for result in proposition_results if result.kind != "ADVOCACY_POSITION"
            ]
            rejected = [result for result in material if result.status == "REJECTED"]

            asserted_issue = self._unrepresented_asserted_fact(claim)
            if asserted_issue is not None:
                proposition_results.append(asserted_issue)
                rejected.append(asserted_issue)

            if not material:
                outcome.rejected_claims.append(
                    self._rejection(
                        claim,
                        NO_VERIFIABLE_PROPOSITION,
                        (
                            "The claim contains only an advocacy position. Positions remain visible "
                            "but are not verified facts and are not sent as trusted Judge material."
                        ),
                        propositions=proposition_results,
                    )
                )
                continue

            if rejected:
                outcome.rejected_claims.append(
                    self._rejection(
                        claim,
                        rejected[0].reason or MALFORMED_PROPOSITION,
                        " ".join(item.detail for item in rejected),
                        propositions=proposition_results,
                    )
                )
                continue

            trusted_parts = [
                item.trusted_summary
                for item in material
                if item.status == "VERIFIED" and item.trusted_summary
            ]
            evidence_ids = sorted(
                {evidence_id for item in material for evidence_id in item.evidence_ids}
            )
            policy_refs = sorted(
                {rule_id for item in material for rule_id in item.policy_rule_ids}
            )
            outcome.verified_claims.append(
                VerifiedClaim(
                    claim_id=claim.claim_id,
                    claim=claim.claim,
                    trusted_summary=" ".join(trusted_parts),
                    evidence_ids=evidence_ids,
                    policy_refs=policy_refs,
                    reasoning_summary=claim.reasoning_summary,
                    importance=claim.importance,
                    asserted_facts=list(claim.asserted_facts),
                    disputed_evidence_ids=list(claim.disputed_evidence_ids),
                    propositions=proposition_results,
                )
            )
            outcome.warnings.extend(self._audit_warnings(claim))

        return outcome

    @staticmethod
    def _rejection(
        claim: AdvocateClaim,
        reason: str,
        detail: str,
        *,
        verification_level: str = "UNVERIFIED",
        propositions: list[PropositionVerification] | None = None,
    ) -> ClaimRejection:
        return ClaimRejection(
            claim_id=claim.claim_id,
            claim=claim.claim,
            reason=reason,
            detail=detail,
            evidence_ids=list(claim.evidence_ids),
            policy_refs=list(claim.policy_refs),
            verification_level=verification_level,
            propositions=list(propositions or []),
        )

    @staticmethod
    def _structural_issue(
        claim: AdvocateClaim, context: AgentCaseContext
    ) -> tuple[str, str] | None:
        evidence_ids = {item.id for item in context.evidence}
        evidence_status = {item.id: item.status for item in context.evidence}
        policy_refs = {rule.rule_id for rule in context.policy.rules}

        if not claim.evidence_ids:
            return NO_EVIDENCE_REFERENCE, f"Claim {claim.claim_id} cites no evidence."
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_ids:
                if evidence_id in _KNOWN_REGISTRY_IDS:
                    return (
                        EVIDENCE_NOT_IN_CASE,
                        f"{evidence_id} is not evidence for case {context.case_id}.",
                    )
                return EVIDENCE_ID_NOT_FOUND, f"{evidence_id} does not exist in this case record."
        if not claim.policy_refs:
            return POLICY_REF_NOT_FOUND, f"Claim {claim.claim_id} cites no policy rule."
        for policy_ref in claim.policy_refs:
            if policy_ref not in policy_refs:
                return (
                    POLICY_REF_NOT_FOUND,
                    f"Policy rule {policy_ref} does not exist for case {context.case_id}.",
                )
        if context.policy.dispute_type != context.dispute_type:
            return (
                POLICY_NOT_APPLICABLE,
                f"Policy {context.policy.policy_id} does not apply to {context.dispute_type}.",
            )
        usable = [
            evidence_id
            for evidence_id in claim.evidence_ids
            if evidence_status.get(evidence_id) == "Verified"
        ]
        if not usable:
            return (
                EVIDENCE_NOT_VERIFIED,
                "Claim rests only on evidence that is missing or conflicting.",
            )
        return None

    def _verify_propositions(
        self, claim: AdvocateClaim, context: AgentCaseContext
    ) -> list[PropositionVerification]:
        results: list[PropositionVerification] = []
        seen: set[str] = set()
        for proposition in claim.propositions:
            if proposition.proposition_id in seen:
                results.append(
                    self._result(
                        proposition,
                        "REJECTED",
                        DUPLICATE_PROPOSITION_ID,
                        f"Proposition ID {proposition.proposition_id} is duplicated in the claim.",
                    )
                )
                continue
            seen.add(proposition.proposition_id)
            results.append(self._verify_proposition(proposition, claim, context))
        return results

    def _verify_proposition(
        self,
        proposition: ClaimProposition,
        claim: AdvocateClaim,
        context: AgentCaseContext,
    ) -> PropositionVerification:
        if proposition.kind == "ADVOCACY_POSITION":
            if not (proposition.position or "").strip():
                return self._result(
                    proposition,
                    "REJECTED",
                    MALFORMED_PROPOSITION,
                    "ADVOCACY_POSITION requires a non-empty position.",
                )
            return self._result(
                proposition,
                "ARGUMENT_ONLY",
                None,
                "Advocacy position preserved as argument only; it is not a verified fact.",
            )

        evidence_issue = self._proposition_evidence_issue(proposition, claim, context)
        if evidence_issue is not None:
            reason, detail = evidence_issue
            return self._result(proposition, "REJECTED", reason, detail)

        if proposition.kind == "FACT":
            return self._verify_fact(proposition, context)
        if proposition.kind == "CAUSAL":
            return self._verify_causal(proposition, context)
        if proposition.kind == "POLICY_APPLICATION":
            return self._verify_policy(proposition, claim, context)
        return self._result(
            proposition,
            "REJECTED",
            MALFORMED_PROPOSITION,
            f"Unsupported proposition kind {proposition.kind}.",
        )

    @staticmethod
    def _proposition_evidence_issue(
        proposition: ClaimProposition,
        claim: AdvocateClaim,
        context: AgentCaseContext,
    ) -> tuple[str, str] | None:
        known = {item.id: item for item in context.evidence}
        declared = set(claim.evidence_ids)
        if not proposition.evidence_ids:
            return (
                NO_EVIDENCE_REFERENCE,
                f"Proposition {proposition.proposition_id} cites no evidence.",
            )
        for evidence_id in proposition.evidence_ids:
            if evidence_id not in known:
                return EVIDENCE_ID_NOT_FOUND, f"Evidence {evidence_id} is not in this case."
            if evidence_id not in declared:
                return (
                    EVIDENCE_NOT_DECLARED_BY_CLAIM,
                    f"Evidence {evidence_id} was not declared by claim {claim.claim_id}.",
                )
            if known[evidence_id].status != "Verified":
                return (
                    EVIDENCE_NOT_VERIFIED,
                    f"Evidence {evidence_id} is {known[evidence_id].status}, not verified.",
                )
        return None

    def _verify_fact(
        self, proposition: ClaimProposition, context: AgentCaseContext
    ) -> PropositionVerification:
        trusted = fact_values(context.facts)
        if not proposition.fact or proposition.operator != "EQUALS" or proposition.value is None:
            return self._result(
                proposition,
                "REJECTED",
                MALFORMED_PROPOSITION,
                "FACT requires fact, value, and operator=EQUALS.",
            )
        if proposition.fact not in trusted:
            return self._result(
                proposition,
                "REJECTED",
                UNKNOWN_ASSERTED_FACT,
                f"{proposition.fact} is not an authoritative fact for this dispute type.",
            )
        expected = trusted[proposition.fact]
        if not values_match(expected, proposition.value):
            return self._result(
                proposition,
                "REJECTED",
                FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS,
                f"{proposition.fact}={proposition.value} contradicts the deterministic value {expected}.",
            )
        support = supporting_evidence_ids(
            context, proposition.fact, proposition.evidence_ids
        )
        if not support:
            return self._result(
                proposition,
                "REJECTED",
                EVIDENCE_DOES_NOT_SUPPORT_FACT,
                (
                    f"The cited evidence cannot establish {proposition.fact}; a valid evidence "
                    "ID is not proof of an unrelated fact."
                ),
            )
        return self._result(
            proposition,
            "VERIFIED",
            None,
            f"{proposition.fact} matches deterministic analysis and has typed evidence coverage.",
            trusted_summary=fact_summary(proposition.fact, expected),
            evidence_ids=support,
        )

    def _verify_causal(
        self, proposition: ClaimProposition, context: AgentCaseContext
    ) -> PropositionVerification:
        if not proposition.relation_id:
            return self._result(
                proposition,
                "REJECTED",
                MALFORMED_PROPOSITION,
                "CAUSAL requires relationId, causeFacts, and effectFact.",
            )
        relation = CAUSAL_RELATIONS.get(proposition.relation_id)
        if relation is None:
            return self._result(
                proposition,
                "REJECTED",
                UNREGISTERED_CAUSAL_RELATION,
                f"{proposition.relation_id} is not a causal relationship calculated by code.",
            )
        if (
            relation.dispute_type != context.dispute_type
            or relation.cause_facts != frozenset(proposition.cause_facts)
            or relation.effect_fact != proposition.effect_fact
        ):
            return self._result(
                proposition,
                "REJECTED",
                CAUSAL_RELATION_MISMATCH,
                "The supplied cause/effect fields do not match the registered deterministic relationship.",
            )
        trusted = fact_values(context.facts)
        all_facts = [*relation.cause_facts, relation.effect_fact]
        if any(fact not in trusted for fact in all_facts):
            return self._result(
                proposition,
                "REJECTED",
                UNKNOWN_ASSERTED_FACT,
                "The registered relationship is not applicable to this deterministic fact set.",
            )
        support: set[str] = set()
        for fact in all_facts:
            covered = supporting_evidence_ids(context, fact, proposition.evidence_ids)
            if not covered:
                return self._result(
                    proposition,
                    "REJECTED",
                    EVIDENCE_DOES_NOT_SUPPORT_FACT,
                    f"The causal assertion lacks typed evidence coverage for {fact}.",
                )
            support.update(covered)
        return self._result(
            proposition,
            "VERIFIED",
            None,
            "Cause and effect match a registered deterministic calculation.",
            trusted_summary=relation.summary,
            evidence_ids=sorted(support),
        )

    def _verify_policy(
        self,
        proposition: ClaimProposition,
        claim: AdvocateClaim,
        context: AgentCaseContext,
    ) -> PropositionVerification:
        if not proposition.policy_rule_id or proposition.asserted_rule_passed is None:
            return self._result(
                proposition,
                "REJECTED",
                MALFORMED_PROPOSITION,
                "POLICY_APPLICATION requires policyRuleId and assertedRulePassed.",
            )
        if proposition.policy_rule_id not in claim.policy_refs:
            return self._result(
                proposition,
                "REJECTED",
                POLICY_REF_NOT_FOUND,
                f"{proposition.policy_rule_id} was not declared by claim {claim.claim_id}.",
            )
        rule = next(
            (
                item
                for item in context.policy_evaluation.evaluated_rules
                if item.rule_id == proposition.policy_rule_id
            ),
            None,
        )
        if rule is None:
            return self._result(
                proposition,
                "REJECTED",
                POLICY_REF_NOT_FOUND,
                f"{proposition.policy_rule_id} has no PolicyTwin evaluation.",
            )
        if rule.passed != proposition.asserted_rule_passed:
            return self._result(
                proposition,
                "REJECTED",
                POLICY_RESULT_MISMATCH,
                f"PolicyTwin recorded passed={rule.passed} for {rule.rule_id}.",
            )
        if (
            proposition.asserted_policy_outcome is not None
            and proposition.asserted_policy_outcome
            != context.policy_evaluation.overall_outcome
        ):
            return self._result(
                proposition,
                "REJECTED",
                POLICY_OUTCOME_MISMATCH,
                (
                    f"PolicyTwin outcome is {context.policy_evaluation.overall_outcome}, not "
                    f"{proposition.asserted_policy_outcome}."
                ),
            )
        rule_evidence = set(rule.evidence_ids)
        support = [
            evidence_id
            for evidence_id in proposition.evidence_ids
            if evidence_id in rule_evidence
        ]
        if not support:
            return self._result(
                proposition,
                "REJECTED",
                POLICY_EVIDENCE_COVERAGE_MISSING,
                f"No cited evidence is part of PolicyTwin's evaluation of {rule.rule_id}.",
            )
        outcome_text = (
            f" PolicyTwin outcome is {context.policy_evaluation.overall_outcome}."
            if proposition.asserted_policy_outcome is not None
            else ""
        )
        return self._result(
            proposition,
            "VERIFIED",
            None,
            "The asserted policy result matches PolicyTwin and its evidence coverage.",
            trusted_summary=(
                f"Policy rule {rule.rule_id} condition is "
                f"{'met' if rule.passed else 'not met'}.{outcome_text}"
            ),
            evidence_ids=support,
            policy_rule_ids=[rule.rule_id],
        )

    @staticmethod
    def _result(
        proposition: ClaimProposition,
        status: str,
        reason: str | None,
        detail: str,
        *,
        trusted_summary: str | None = None,
        evidence_ids: list[str] | None = None,
        policy_rule_ids: list[str] | None = None,
    ) -> PropositionVerification:
        return PropositionVerification(
            proposition_id=proposition.proposition_id,
            kind=proposition.kind,
            status=status,
            reason=reason,
            detail=detail,
            trusted_summary=trusted_summary,
            evidence_ids=list(evidence_ids or []),
            policy_rule_ids=list(policy_rule_ids or []),
        )

    @staticmethod
    def _unrepresented_asserted_fact(
        claim: AdvocateClaim,
    ) -> PropositionVerification | None:
        proposition_pairs = {
            (item.fact, _normalise_value(item.value))
            for item in claim.propositions
            if item.kind == "FACT"
        }
        for asserted in claim.asserted_facts:
            if (asserted.fact, _normalise_value(asserted.value)) not in proposition_pairs:
                return PropositionVerification(
                    proposition_id="SERVER_ASSERTED_FACT_COVERAGE",
                    kind="FACT",
                    status="REJECTED",
                    reason=ASSERTED_FACT_NOT_PROPOSITION,
                    detail=(
                        f"Legacy asserted fact {asserted.fact} was not represented by an atomic "
                        "FACT proposition."
                    ),
                )
        return None

    @staticmethod
    def _audit_warnings(claim: AdvocateClaim) -> list[ClaimWarning]:
        return [
            ClaimWarning(
                claim_id=claim.claim_id,
                code="EVIDENCE_DISPUTED_BY_ADVOCATE",
                detail=f"Advocate disputes evidence {evidence_id}.",
            )
            for evidence_id in claim.disputed_evidence_ids
        ]


def _normalise_value(value: object) -> tuple[str, object]:
    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, (int, float)):
        return "number", float(value)
    return type(value).__name__, value


def fact_values(facts: object) -> dict[str, object]:
    if isinstance(facts, RouteDeviationFacts):
        return {name: getattr(facts, key) for name, key in ROUTE_FACT_KEYS.items()}
    if isinstance(facts, NoShowFacts):
        return {name: getattr(facts, key) for name, key in NO_SHOW_FACT_KEYS.items()}
    return {}


def trusted_facts(context: AgentCaseContext) -> dict[str, object]:
    return fact_values(context.facts)


def values_match(expected: object, actual: object) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        if isinstance(expected, bool) != isinstance(actual, bool):
            return False
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= FLOAT_TOLERANCE
    return expected == actual


_trusted_facts = trusted_facts
_values_match = values_match


__all__ = [
    "ASSERTED_FACT_NOT_PROPOSITION",
    "AdvocateClaimVerificationService",
    "CAUSAL_RELATION_MISMATCH",
    "DUPLICATE_CLAIM_ID",
    "DUPLICATE_PROPOSITION_ID",
    "EVIDENCE_DOES_NOT_SUPPORT_FACT",
    "EVIDENCE_ID_NOT_FOUND",
    "EVIDENCE_NOT_DECLARED_BY_CLAIM",
    "EVIDENCE_NOT_IN_CASE",
    "EVIDENCE_NOT_VERIFIED",
    "FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS",
    "LEGACY_SEMANTIC_UNVERIFIED",
    "MALFORMED_ADVOCATE_OUTPUT",
    "MALFORMED_PROPOSITION",
    "NO_ATOMIC_PROPOSITIONS",
    "NO_EVIDENCE_REFERENCE",
    "NO_VERIFIABLE_PROPOSITION",
    "POLICY_EVIDENCE_COVERAGE_MISSING",
    "POLICY_NOT_APPLICABLE",
    "POLICY_OUTCOME_MISMATCH",
    "POLICY_REF_NOT_FOUND",
    "POLICY_RESULT_MISMATCH",
    "UNKNOWN_ASSERTED_FACT",
    "UNREGISTERED_CAUSAL_RELATION",
    "VerificationOutcome",
    "WRONG_SIDE",
    "fact_values",
    "trusted_facts",
    "values_match",
]
