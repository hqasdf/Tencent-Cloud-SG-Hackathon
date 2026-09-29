"""Deterministic validation of rebuttal output.

A rebuttal is an argument *about* a verified claim, which makes it the easiest
place in the pipeline for a model to smuggle something in. It has four ways to
do that, and each is closed here rather than requested in a prompt:

  - **target something that does not exist** — a claim ID that was never
    verified, or belongs to the other side of the exchange
  - **bring new material** — an evidence or policy ID that is not in the
    trusted context
  - **assert a fact that is not true** — a structured value contradicting the
    deterministic analysis
  - **state money** — a figure in prose that appears nowhere in the facts

The design choice that matters most, and it mirrors the Judge validator: **a
rejection rejects the whole response.** Nothing is repaired, nothing is
partially accepted, and no invalid reference is quietly dropped. A rebuttal that
cites ``E99`` is not "a good rebuttal with one bad citation" — its reasoning
cannot be verified, so it does not become trusted input.

Rejections are preserved in full and surfaced to the human reviewer, marked as
not used by the Judge. Discarding them would hide exactly the signal a reviewer
needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.claim_verification import fact_values, values_match
from app.agents.monetary import invented_monetary_amounts
from app.agents.rebuttal_ids import namespace_rebuttal_id
from app.models.advocate import AdvocateClaim
from app.models.rebuttal import (
    CONCEDING_STANCES,
    RebuttalCaseContext,
    RebuttalOutput,
    RebuttalRejection,
    RebuttalVerificationSummary,
    VerifiedRebuttal,
)

# Machine-readable rejection codes.
#
# Note there is no ``TARGET_CLAIM_NOT_VERIFIED``. A target that exists but failed
# verification is indistinguishable from one that never existed, because rejected
# claims are absent from the context by design. ``TARGET_CLAIM_NOT_FOUND`` covers
# both, and its detail says so rather than implying the ID was invented.
TARGET_CLAIM_NOT_FOUND = "TARGET_CLAIM_NOT_FOUND"
TARGET_CLAIM_IS_OWN_SIDE = "TARGET_CLAIM_IS_OWN_SIDE"
DUPLICATE_TARGET_CLAIM = "DUPLICATE_TARGET_CLAIM"
EVIDENCE_ID_NOT_FOUND = "EVIDENCE_ID_NOT_FOUND"
POLICY_REF_NOT_FOUND = "POLICY_REF_NOT_FOUND"
UNKNOWN_ASSERTED_FACT = "UNKNOWN_ASSERTED_FACT"
FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS = "FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS"
CONCESSION_CONTRADICTS_STANCE = "CONCESSION_CONTRADICTS_STANCE"
CONCESSION_NOT_A_TARGET = "CONCESSION_NOT_A_TARGET"
EMPTY_REASONING_SUMMARY = "EMPTY_REASONING_SUMMARY"
INVENTED_MONETARY_VALUE = "INVENTED_MONETARY_VALUE"


@dataclass
class RebuttalVerificationOutcome:
    """Verified and rejected rebuttals, plus the summary counts."""

    verified_rebuttals: list[VerifiedRebuttal] = field(default_factory=list)
    rejected_rebuttals: list[RebuttalRejection] = field(default_factory=list)
    generated_count: int = 0

    def summary(self) -> RebuttalVerificationSummary:
        return RebuttalVerificationSummary(
            verified_count=len(self.verified_rebuttals),
            rejected_count=len(self.rejected_rebuttals),
            rejection_reasons=sorted({item.reason for item in self.rejected_rebuttals}),
            generated_count=self.generated_count,
        )

    def conceded_target_ids(self) -> list[str]:
        """Targets conceded, derived from verified stances only.

        Derived by code rather than read from the model's own ``concessions``
        list, so what the Judge and the UI see cannot disagree with the stances
        that actually passed verification.
        """
        return sorted(
            {
                item.target_claim_id
                for item in self.verified_rebuttals
                if item.stance in CONCEDING_STANCES
            }
        )


class RebuttalValidationService:
    """Checks a rebuttal against the trusted context. Pure and offline."""

    def validate(
        self, output: RebuttalOutput, context: RebuttalCaseContext
    ) -> RebuttalVerificationOutcome:
        outcome = RebuttalVerificationOutcome(generated_count=len(output.responses))

        own_index = self._index(context.own_verified_claims)
        opposing_index = self._index(context.opposing_verified_claims)
        evidence_ids = {item.id for item in context.evidence}
        policy_ids = {rule.rule_id for rule in context.applicable_policy.rules}
        trusted = fact_values(context.deterministic_facts)
        opposing_side = "DRIVER" if context.own_side == "RIDER" else "RIDER"

        seen_targets: set[str] = set()
        verified_targets: dict[str, str] = {}

        for response in output.responses:
            issues = self._check_response(
                response=response,
                own_index=own_index,
                opposing_index=opposing_index,
                evidence_ids=evidence_ids,
                policy_ids=policy_ids,
                trusted=trusted,
                seen_targets=seen_targets,
                facts=context.deterministic_facts,
            )
            if issues:
                outcome.rejected_rebuttals.append(
                    RebuttalRejection(
                        target_claim_id=response.target_claim_id or None,
                        stance=response.stance,
                        # The first issue is the primary reason; the detail keeps
                        # all of them so a reviewer sees the full picture rather
                        # than only the first thing that went wrong.
                        reason=issues[0][0],
                        detail=" ".join(detail for _, detail in issues),
                        evidence_ids=list(response.evidence_ids),
                        policy_rule_ids=list(response.policy_rule_ids),
                    )
                )
                continue

            seen_targets.add(response.target_claim_id)
            verified_targets[response.target_claim_id] = response.stance
            outcome.verified_rebuttals.append(
                VerifiedRebuttal(
                    # IDs are assigned by code, densely over the responses that
                    # actually passed, so an ID always addresses a rebuttal the
                    # Judge was given.
                    rebuttal_id=namespace_rebuttal_id(
                        context.own_side, len(outcome.verified_rebuttals) + 1
                    ),
                    side=context.own_side,
                    target_claim_id=response.target_claim_id,
                    target_claim_side=opposing_side,
                    stance=response.stance,
                    reasoning_summary=response.reasoning_summary,
                    evidence_ids=list(response.evidence_ids),
                    policy_rule_ids=list(response.policy_rule_ids),
                    asserted_facts=list(response.asserted_facts),
                )
            )

        outcome.rejected_rebuttals.extend(
            self._check_concessions(output, verified_targets)
        )
        return outcome

    # -- one response -----------------------------------------------------

    def _check_response(
        self,
        *,
        response: object,
        own_index: dict[str, AdvocateClaim],
        opposing_index: dict[str, AdvocateClaim],
        evidence_ids: set[str],
        policy_ids: set[str],
        trusted: dict[str, object],
        seen_targets: set[str],
        facts: object,
    ) -> list[tuple[str, str]]:
        issues: list[tuple[str, str]] = []
        target = getattr(response, "target_claim_id", "") or ""

        # Target resolution. Order matters: an own-side target is reported as
        # such rather than as "not found", because "you rebutted yourself" and
        # "that claim does not exist" are different mistakes with different fixes.
        if target in own_index:
            issues.append(
                (
                    TARGET_CLAIM_IS_OWN_SIDE,
                    f"{target} is {own_index[target].claim_id}'s own claim; a side may not "
                    "rebut its own claim.",
                )
            )
        elif target not in opposing_index:
            issues.append(
                (
                    TARGET_CLAIM_NOT_FOUND,
                    f"{target or '(empty)'} is not a verified claim belonging to the "
                    "opposing side, so it cannot be rebutted. Only claims present in "
                    "opposingVerifiedClaims are valid targets.",
                )
            )
        elif target in seen_targets:
            issues.append(
                (
                    DUPLICATE_TARGET_CLAIM,
                    f"{target} is targeted more than once. Consolidate into a single "
                    "response.",
                )
            )

        # Evidence references: existence only. A concession needs no evidence, so
        # requiring it would force a model to cite something it does not rely on.
        for evidence_id in getattr(response, "evidence_ids", []):
            if evidence_id not in evidence_ids:
                issues.append(
                    (
                        EVIDENCE_ID_NOT_FOUND,
                        f"Evidence {evidence_id} does not exist in the trusted case "
                        "context. No new evidence may be introduced in rebuttal.",
                    )
                )

        # Policy references.
        for rule_id in getattr(response, "policy_rule_ids", []):
            if rule_id not in policy_ids:
                issues.append(
                    (
                        POLICY_REF_NOT_FOUND,
                        f"Policy rule {rule_id} is not applicable to this dispute. "
                        f"Applicable rules: {', '.join(sorted(policy_ids))}.",
                    )
                )

        # Structured fact verification: the hard layer, identical to Stage 4.
        for asserted in getattr(response, "asserted_facts", []):
            if asserted.fact not in trusted:
                issues.append(
                    (
                        UNKNOWN_ASSERTED_FACT,
                        f"Asserted fact {asserted.fact} is not a verifiable fact for "
                        "this dispute type.",
                    )
                )
                continue
            expected = trusted[asserted.fact]
            if not values_match(expected, asserted.value):
                issues.append(
                    (
                        FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS,
                        f"Asserted {asserted.fact}={asserted.value} contradicts the "
                        f"trusted deterministic value {expected}.",
                    )
                )

        summary = (getattr(response, "reasoning_summary", "") or "").strip()
        if not summary:
            issues.append(
                (EMPTY_REASONING_SUMMARY, "The response has an empty reasoning summary.")
            )

        # Money. The schema has no amount field, so prose is the only route left.
        for amount in invented_monetary_amounts(summary, facts):
            issues.append(
                (
                    INVENTED_MONETARY_VALUE,
                    f"The reasoning states the monetary amount {amount:g}, which is not a "
                    "value supplied in the trusted facts. The refund engine calculates it.",
                )
            )

        return issues

    # -- concessions ------------------------------------------------------

    @staticmethod
    def _check_concessions(
        output: RebuttalOutput, verified_targets: dict[str, str]
    ) -> list[RebuttalRejection]:
        """Every concession must be a target the side actually conceded.

        ``concessions`` is a list of claim IDs rather than prose precisely so
        this can be checked. A model that lists a claim as conceded while having
        challenged it has produced an internally inconsistent response, which is
        reported rather than resolved in the model's favour.
        """
        rejections: list[RebuttalRejection] = []
        for target in output.concessions:
            stance = verified_targets.get(target)
            if stance is None:
                rejections.append(
                    RebuttalRejection(
                        target_claim_id=target,
                        stance=None,
                        reason=CONCESSION_NOT_A_TARGET,
                        detail=(
                            f"{target} is listed as a concession but is not a target of any "
                            "verified response, so the concession cannot be attributed."
                        ),
                    )
                )
                continue
            if stance not in CONCEDING_STANCES:
                rejections.append(
                    RebuttalRejection(
                        target_claim_id=target,
                        stance=stance,
                        reason=CONCESSION_CONTRADICTS_STANCE,
                        detail=(
                            f"{target} is listed as a concession but its verified stance is "
                            f"{stance}, which does not concede it."
                        ),
                    )
                )
        return rejections

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _index(claims: list[AdvocateClaim]) -> dict[str, AdvocateClaim]:
        """Namespaced claim ID -> claim. IDs are already namespaced by the builder."""
        return {claim.claim_id: claim for claim in claims}


__all__ = [
    "CONCESSION_CONTRADICTS_STANCE",
    "CONCESSION_NOT_A_TARGET",
    "DUPLICATE_TARGET_CLAIM",
    "EMPTY_REASONING_SUMMARY",
    "EVIDENCE_ID_NOT_FOUND",
    "FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS",
    "INVENTED_MONETARY_VALUE",
    "POLICY_REF_NOT_FOUND",
    "RebuttalValidationService",
    "RebuttalVerificationOutcome",
    "TARGET_CLAIM_IS_OWN_SIDE",
    "TARGET_CLAIM_NOT_FOUND",
    "UNKNOWN_ASSERTED_FACT",
]
