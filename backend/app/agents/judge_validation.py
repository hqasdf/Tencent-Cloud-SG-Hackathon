"""Deterministic validation of Judge output.

The Judge is not trusted because a model produced it. Every reference it makes is
checked against the trusted context, and every constraint the brief imposes on it
is enforced here rather than requested in a prompt.

The design choice that matters most: **a validation failure rejects the whole
response.** Nothing is repaired, nothing is partially accepted, and no invalid
reference is quietly dropped. A Judge response that cites ``E99`` is not "a good
response with one bad citation" — it is a response whose reasoning cannot be
verified, and Stage 5 would rather show ``FAILED`` than show a decision built on
an invented citation.

This mirrors ``AdvocateClaimVerificationService`` deliberately. A rejected claim
there and a rejected Judge output here are the same kind of event: an AI
assertion that failed a deterministic check and is preserved for audit.
"""

from __future__ import annotations

import re

from app.agents.judge_claims import known_claim_ids, namespace_claim_id, split_claim_id
from app.models.judge import (
    JUDGE_ALLOWED_OUTCOMES,
    JudgeCaseContext,
    JudgeOutput,
    JudgeValidationIssue,
    JudgeValidationResult,
)

# Issue codes. Machine-readable so the API, the audit trail and the tests all
# refer to the same vocabulary.
OUTCOME_NOT_ALLOWED_FOR_DISPUTE = "OUTCOME_NOT_ALLOWED_FOR_DISPUTE"
HUMAN_REVIEW_OVERRIDE_ATTEMPTED = "HUMAN_REVIEW_OVERRIDE_ATTEMPTED"
HUMAN_REVIEW_FLAG_INCONSISTENT = "HUMAN_REVIEW_FLAG_INCONSISTENT"
UNKNOWN_CLAIM_ID = "UNKNOWN_CLAIM_ID"
CLAIM_ID_BELONGS_TO_OTHER_SIDE = "CLAIM_ID_BELONGS_TO_OTHER_SIDE"
CLAIM_BOTH_ACCEPTED_AND_REJECTED = "CLAIM_BOTH_ACCEPTED_AND_REJECTED"
DUPLICATE_CLAIM_ID = "DUPLICATE_CLAIM_ID"
UNKNOWN_EVIDENCE_ID = "UNKNOWN_EVIDENCE_ID"
UNKNOWN_POLICY_RULE_ID = "UNKNOWN_POLICY_RULE_ID"
EMPTY_REASONING_SUMMARY = "EMPTY_REASONING_SUMMARY"
MISSING_CLAIM_CITATIONS = "MISSING_CLAIM_CITATIONS"
INVENTED_MONETARY_VALUE = "INVENTED_MONETARY_VALUE"

# Currency tokens and amounts, used to detect a monetary figure in prose. The
# Judge has no amount field, so prose is the only route left for one to appear.
_CURRENCY = r"(?:SGD|USD|MYR|IDR|THB|PHP|VND|CNY|RMB|EUR|GBP|S\$|US\$|HK\$|A\$|[$€£¥])"
_AMOUNT = r"\d+(?:[.,]\d+)?"
_MONEY_PATTERNS = (
    re.compile(rf"{_CURRENCY}\s*({_AMOUNT})", re.IGNORECASE),
    re.compile(rf"({_AMOUNT})\s*{_CURRENCY}", re.IGNORECASE),
)

# Amounts are compared after rounding to cents; a Judge quoting a supplied fact
# should not fail on float representation.
_MONEY_TOLERANCE = 0.005


class JudgeOutputValidationService:
    """Checks a Judge response against the trusted context. Pure and offline."""

    def validate(self, output: JudgeOutput, context: JudgeCaseContext) -> JudgeValidationResult:
        issues: list[JudgeValidationIssue] = []

        self._check_outcome(output, context, issues)
        self._check_claims(output, context, issues)
        self._check_evidence(output, context, issues)
        self._check_policy(output, context, issues)
        self._check_reasoning(output, context, issues)

        return JudgeValidationResult(valid=not issues, issues=issues)

    # -- outcome ----------------------------------------------------------

    @staticmethod
    def _check_outcome(
        output: JudgeOutput, context: JudgeCaseContext, issues: list[JudgeValidationIssue]
    ) -> None:
        allowed = JUDGE_ALLOWED_OUTCOMES.get(context.dispute_type, frozenset())
        if output.recommended_outcome not in allowed:
            issues.append(
                JudgeValidationIssue(
                    code=OUTCOME_NOT_ALLOWED_FOR_DISPUTE,
                    detail=(
                        f"Outcome {output.recommended_outcome!r} is not available for a "
                        f"{context.dispute_type} dispute. Allowed: {', '.join(sorted(allowed))}."
                    ),
                )
            )

        if context.resolution_mode == "HUMAN_REVIEW":
            # The deterministic gate has already decided this case cannot be
            # automated. A Judge that claims otherwise is attempting to overrule
            # code, so the response is rejected outright rather than downgraded.
            if output.status != "PENDING_HUMAN_REVIEW":
                issues.append(
                    JudgeValidationIssue(
                        code=HUMAN_REVIEW_OVERRIDE_ATTEMPTED,
                        detail=(
                            "Deterministic analysis requires human review, but the Judge "
                            f"returned status {output.status!r}. Automation eligibility is "
                            "decided by code, not by the Judge."
                        ),
                    )
                )
            if not output.requires_human_review:
                issues.append(
                    JudgeValidationIssue(
                        code=HUMAN_REVIEW_FLAG_INCONSISTENT,
                        detail=(
                            "Deterministic analysis requires human review, but the Judge set "
                            "requiresHumanReview=false."
                        ),
                    )
                )
        elif output.status == "PENDING_HUMAN_REVIEW" and not output.requires_human_review:
            issues.append(
                JudgeValidationIssue(
                    code=HUMAN_REVIEW_FLAG_INCONSISTENT,
                    detail=(
                        "Judge returned PENDING_HUMAN_REVIEW but set "
                        "requiresHumanReview=false."
                    ),
                )
            )

    # -- claims -----------------------------------------------------------

    @staticmethod
    def _check_claims(
        output: JudgeOutput, context: JudgeCaseContext, issues: list[JudgeValidationIssue]
    ) -> None:
        verified = known_claim_ids(
            context.rider.verified_claims, context.driver.verified_claims
        )

        lists = {
            "acceptedRiderClaimIds": output.accepted_rider_claim_ids,
            "acceptedDriverClaimIds": output.accepted_driver_claim_ids,
            "rejectedRiderClaimIds": output.rejected_rider_claim_ids,
            "rejectedDriverClaimIds": output.rejected_driver_claim_ids,
        }
        expected_side = {
            "acceptedRiderClaimIds": "RIDER",
            "acceptedDriverClaimIds": "DRIVER",
            "rejectedRiderClaimIds": "RIDER",
            "rejectedDriverClaimIds": "DRIVER",
        }

        accepted: set[str] = set()
        rejected: set[str] = set()

        for field, ids in lists.items():
            seen: set[str] = set()
            expected = expected_side[field]
            for reference in ids:
                # Resolve the side prefix BEFORE namespacing. Namespacing first
                # would turn a foreign ID such as "DRIVER-D1" into
                # "RIDER-DRIVER-D1", which then looks like an unknown Rider claim
                # rather than a Driver claim cited on the wrong side — a
                # different and less useful diagnosis.
                owner, bare = split_claim_id(reference)
                if owner is not None and owner != expected:
                    issues.append(
                        JudgeValidationIssue(
                            code=CLAIM_ID_BELONGS_TO_OTHER_SIDE,
                            detail=(
                                f"{reference} is a {owner} claim but was cited in {field}."
                            ),
                        )
                    )
                    continue

                normalised = namespace_claim_id(expected, bare)
                if normalised in seen:
                    issues.append(
                        JudgeValidationIssue(
                            code=DUPLICATE_CLAIM_ID,
                            detail=f"Claim {normalised} is listed more than once in {field}.",
                        )
                    )
                seen.add(normalised)

                if normalised not in verified:
                    issues.append(
                        JudgeValidationIssue(
                            code=UNKNOWN_CLAIM_ID,
                            detail=(
                                f"{normalised} is not a verified claim in this case. "
                                "Only verified claims may be cited."
                            ),
                        )
                    )
                    continue

                if field.startswith("accepted"):
                    accepted.add(normalised)
                else:
                    rejected.add(normalised)

        for claim_id in sorted(accepted & rejected):
            issues.append(
                JudgeValidationIssue(
                    code=CLAIM_BOTH_ACCEPTED_AND_REJECTED,
                    detail=f"Claim {claim_id} is both accepted and rejected.",
                )
            )

    # -- evidence ---------------------------------------------------------

    @staticmethod
    def _check_evidence(
        output: JudgeOutput, context: JudgeCaseContext, issues: list[JudgeValidationIssue]
    ) -> None:
        known = {item.id for item in context.evidence}
        for evidence_id in output.evidence_ids:
            if evidence_id not in known:
                issues.append(
                    JudgeValidationIssue(
                        code=UNKNOWN_EVIDENCE_ID,
                        detail=(
                            f"Evidence {evidence_id} does not exist in the trusted case "
                            "context."
                        ),
                    )
                )

    # -- policy -----------------------------------------------------------

    @staticmethod
    def _check_policy(
        output: JudgeOutput, context: JudgeCaseContext, issues: list[JudgeValidationIssue]
    ) -> None:
        known = {rule.rule_id for rule in context.applicable_policy.rules}
        for rule_id in output.policy_rule_ids:
            if rule_id not in known:
                issues.append(
                    JudgeValidationIssue(
                        code=UNKNOWN_POLICY_RULE_ID,
                        detail=(
                            f"Policy rule {rule_id} is not applicable to this dispute. "
                            f"Applicable rules: {', '.join(sorted(known))}."
                        ),
                    )
                )

    # -- reasoning prose --------------------------------------------------

    @staticmethod
    def _check_reasoning(
        output: JudgeOutput, context: JudgeCaseContext, issues: list[JudgeValidationIssue]
    ) -> None:
        summary = (output.reasoning_summary or "").strip()
        if not summary:
            issues.append(
                JudgeValidationIssue(
                    code=EMPTY_REASONING_SUMMARY,
                    detail="The Judge returned an empty reasoning summary.",
                )
            )

        cited_claims = (
            output.accepted_rider_claim_ids
            + output.accepted_driver_claim_ids
            + output.rejected_rider_claim_ids
            + output.rejected_driver_claim_ids
        )
        if cited_claims and not output.evidence_ids and not output.policy_rule_ids:
            issues.append(
                JudgeValidationIssue(
                    code=MISSING_CLAIM_CITATIONS,
                    detail=(
                        "The Judge assessed claims but cited no evidence and no policy rule."
                    ),
                )
            )

        trusted = _trusted_numeric_values(context)
        for amount in _monetary_amounts(summary):
            if not any(abs(amount - value) <= _MONEY_TOLERANCE for value in trusted):
                issues.append(
                    JudgeValidationIssue(
                        code=INVENTED_MONETARY_VALUE,
                        detail=(
                            f"The reasoning states the monetary amount {amount:g}, which is "
                            "not a value supplied in the trusted facts. The Judge must not "
                            "state an amount; the refund engine calculates it."
                        ),
                    )
                )


def _monetary_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for pattern in _MONEY_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                amounts.append(float(raw))
            except ValueError:
                continue
    return amounts


def _trusted_numeric_values(context: JudgeCaseContext) -> list[float]:
    """Every numeric value the deterministic layer supplied.

    A Judge quoting ``fareDifference`` is repeating a fact it was given, which is
    allowed. A Judge stating a figure that appears nowhere in the facts has
    produced a number from nowhere, which is the failure this guards against.
    """
    values: list[float] = []

    def collect(node: object) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            values.append(float(node))
        elif isinstance(node, dict):
            for item in node.values():
                collect(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                collect(item)

    collect(context.deterministic_facts.model_dump())
    return values


__all__ = [
    "CLAIM_BOTH_ACCEPTED_AND_REJECTED",
    "CLAIM_ID_BELONGS_TO_OTHER_SIDE",
    "DUPLICATE_CLAIM_ID",
    "EMPTY_REASONING_SUMMARY",
    "HUMAN_REVIEW_FLAG_INCONSISTENT",
    "HUMAN_REVIEW_OVERRIDE_ATTEMPTED",
    "INVENTED_MONETARY_VALUE",
    "JudgeOutputValidationService",
    "MISSING_CLAIM_CITATIONS",
    "OUTCOME_NOT_ALLOWED_FOR_DISPUTE",
    "UNKNOWN_CLAIM_ID",
    "UNKNOWN_EVIDENCE_ID",
    "UNKNOWN_POLICY_RULE_ID",
]
