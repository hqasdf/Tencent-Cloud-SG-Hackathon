"""Builds the RebuttalCaseContext using an explicit allow-list.

This is Stage 6's safety boundary, and it has one job beyond the advocate
equivalent: make it impossible for a rebuttal to be about anything except the
opposing side's *verified* claims.

The claim lists are projected from ``verified_claims`` only. ``rejected_claims``
is not filtered, hidden, or labelled here — it is never read, so there is no code
path by which a rejected claim could become a rebuttal target or a rebuttal's
supporting reference. That matters more here than anywhere else in the pipeline:
a rebuttal is an argument *about* a claim, so if rejected claims were reachable
the model would be invited to argue against something code already disbelieved.

The other side's rebuttal is absent too. There is one round, and a context that
cannot name a rebuttal cannot produce a rebuttal-to-a-rebuttal.
"""

from __future__ import annotations

from app.agents.context_builder import (
    NO_SHOW_RULE_DESCRIPTIONS,
    ROUTE_RULE_DESCRIPTIONS,
)
from app.agents.judge_claims import namespace_claim_id
from app.models.advocate import AdvocateClaim, AdvocateSideResult
from app.models.analysis import CaseAnalysisResponse
from app.models.agent import (
    AdvocateSide,
    AgentCaseContext,
    ContextPolicy,
    ContextPolicyRule,
)
from app.models.case import DisputeCase
from app.models.rebuttal import MAX_REBUTTAL_ROUNDS, STANCES, RebuttalCaseContext
from app.policies import NO_SHOW_POLICY_V1, ROUTE_DEVIATION_POLICY_V1

_OPPOSING: dict[str, AdvocateSide] = {"RIDER": "DRIVER", "DRIVER": "RIDER"}


class RebuttalContextBuilder:
    """Projects the trusted rebuttal context. Never copies the answer."""

    def build(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        advocate_context: AgentCaseContext,
        rider: AdvocateSideResult,
        driver: AdvocateSideResult,
        side: AdvocateSide,
    ) -> RebuttalCaseContext:
        """Assemble one side's rebuttal context.

        ``advocate_context`` is passed in rather than rebuilt so the rebuttal,
        the advocates and the Judge are guaranteed to be reasoning over the same
        facts, evidence and policy. Rebuilding would create a second projection
        that could drift.
        """
        own = rider if side == "RIDER" else driver
        opposing = driver if side == "RIDER" else rider

        return RebuttalCaseContext(
            case_id=case.id,
            dispute_type=case.dispute_type,
            currency=case.fare.currency,
            own_side=side,
            deterministic_facts=advocate_context.facts,
            evidence=list(advocate_context.evidence),
            applicable_policy=self._policy(case),
            policy_evaluation=advocate_context.policy_evaluation,
            own_verified_claims=self._claims(side, own),
            opposing_verified_claims=self._claims(_OPPOSING[side], opposing),
            allowed_stances=list(STANCES),
            rebuttal_round=MAX_REBUTTAL_ROUNDS,
            max_rebuttal_rounds=MAX_REBUTTAL_ROUNDS,
        )

    @staticmethod
    def _claims(side: AdvocateSide, result: AdvocateSideResult) -> list[AdvocateClaim]:
        """Project one side's VERIFIED claims, with namespaced IDs.

        ``rejected_claims`` is not read at all. See the module docstring.
        """
        return [
            claim.model_copy(update={"claim_id": namespace_claim_id(side, claim.claim_id)})
            for claim in result.verified_claims
        ]

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


__all__ = ["RebuttalContextBuilder"]
