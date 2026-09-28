"""Builds the JudgeCaseContext using an explicit allow-list.

This class is the safety boundary of Stage 5, and it is stricter than the
advocate equivalent in one specific way: the advocates may see facts, evidence
and policy, but the Judge must additionally be prevented from seeing *rejected*
claims and the deterministic answer.

Why rejected claims are excluded rather than labelled
-----------------------------------------------------
The brief permits showing rejected claims in a separate audit-only section
marked "NOT TRUSTED". This implementation excludes them entirely instead, and
the reason is that a large language model does not reliably honour a label. A
section that says "the following was rejected, do not use it" still puts the
assertion in the context window, where it can shape the reasoning while being
absent from the citations. Exclusion is a structural guarantee; labelling is a
request. The rejected claims remain fully visible to humans through the Stage 4
advocate panels, which is where the audit need actually lives.

What the Judge sees
-------------------
    facts + evidence + applicable policy + VERIFIED claims + resolution mode

What it never sees
------------------
    resolution recommendation, refund amount, ruling, confidence,
    escalation reasons, analysis inputs, rejected claims, other cases, history.
"""

from __future__ import annotations

from app.agents.judge_claims import namespace_claim_id
from app.agents.context_builder import (
    NO_SHOW_RULE_DESCRIPTIONS,
    ROUTE_RULE_DESCRIPTIONS,
    _conflicting_from_rules,
)
from app.models.advocate import AdvocateClaim, AdvocateSideResult
from app.models.analysis import CaseAnalysisResponse
from app.models.agent import (
    AgentCaseContext,
    ContextPolicy,
    ContextPolicyEvaluation,
    ContextPolicyRule,
)
from app.models.case import DisputeCase
from app.models.judge import JUDGE_ALLOWED_OUTCOMES, JudgeCaseContext, JudgeSideClaims
from app.policies import NO_SHOW_POLICY_V1, ROUTE_DEVIATION_POLICY_V1


class JudgeContextBuilder:
    """Projects the trusted Judge context. Never copies the answer."""

    def build(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        advocate_context: AgentCaseContext,
        rider: AdvocateSideResult,
        driver: AdvocateSideResult,
    ) -> JudgeCaseContext:
        """Assemble the Judge context from the case, its analysis and both sides.

        The advocate context is passed in rather than rebuilt so the Judge and
        the advocates are guaranteed to be looking at the same facts, evidence
        and policy. Rebuilding would create a second projection that could drift.
        """
        return JudgeCaseContext(
            case_id=case.id,
            dispute_type=case.dispute_type,
            currency=case.fare.currency,
            deterministic_facts=advocate_context.facts,
            evidence=list(advocate_context.evidence),
            applicable_policy=self._policy(case),
            policy_evaluation=self._policy_evaluation(advocate_context, analysis),
            rider=self._side("RIDER", rider),
            driver=self._side("DRIVER", driver),
            resolution_mode=analysis.resolution_mode,
            allowed_outcomes=sorted(JUDGE_ALLOWED_OUTCOMES[case.dispute_type]),
        )

    @staticmethod
    def _side(side: str, result: AdvocateSideResult) -> JudgeSideClaims:
        """Project one side's VERIFIED claims, with namespaced IDs.

        ``rejected_claims`` is not read here at all. It is not filtered, hidden,
        or labelled — it is never touched, so there is no code path by which a
        rejected claim could reach the Judge prompt.
        """
        verified: list[AdvocateClaim] = [
            claim.model_copy(update={"claim_id": namespace_claim_id(side, claim.claim_id)})
            for claim in result.verified_claims
        ]
        return JudgeSideClaims(
            side=side,  # type: ignore[arg-type]
            verified_claim_count=len(verified),
            has_verified_claims=bool(verified),
            verified_claims=verified,
        )

    @staticmethod
    def _policy(case: DisputeCase) -> ContextPolicy:
        if case.dispute_type == "route_deviation":
            policy = ROUTE_DEVIATION_POLICY_V1
            descriptions = ROUTE_RULE_DESCRIPTIONS
        else:
            policy = NO_SHOW_POLICY_V1
            descriptions = NO_SHOW_RULE_DESCRIPTIONS
        return ContextPolicy(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            dispute_type=case.dispute_type,
            rules=[
                ContextPolicyRule(rule_id=rule_id, description=description)
                for rule_id, description in descriptions.items()
            ],
        )

    @staticmethod
    def _policy_evaluation(
        advocate_context: AgentCaseContext, analysis: CaseAnalysisResponse
    ) -> ContextPolicyEvaluation:
        """PolicyTwin's rule-by-rule result, unchanged from the advocate view.

        This is the *evaluation*, not the recommendation: which rules passed,
        with what actual and required values. It is the same object the
        advocates saw, so the Judge reasons over identical policy state.
        """
        return advocate_context.policy_evaluation


def conflicting_evidence_ids(analysis: CaseAnalysisResponse) -> list[str]:
    """Evidence flagged by failed policy rules. Re-exported for reuse."""
    return _conflicting_from_rules(analysis)


__all__ = ["JudgeContextBuilder", "conflicting_evidence_ids"]
