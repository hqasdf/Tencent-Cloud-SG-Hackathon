"""Deterministic explanation of a decision.

"Why this decision?" is answered here by projection, not by generation. Every
field is assembled from data the deterministic layer or the validators already
produced:

    accepted claims      <- the Judge's validated citations, resolved to claims
    relevant rebuttals   <- the verified rebuttals, marked if the Judge cited them
    decisive facts       <- the PolicyTwin rule evaluations and their thresholds
    policy rules         <- the PolicyTwin rule evaluations, verbatim
    final action         <- the ResolutionEngine recommendation

Nothing in this module can introduce a fact. It has no model, no provider and no
prompt; it is a pure function of its inputs. That is deliberate: an explanation
service that could generate new factual claims would be a second, unverified
analysis engine sitting next to the verified one.

The ``judge_advisory_differs`` flag is the one place this module says something
the deterministic layer did not. It reports a *disagreement* between the AI's
recommendation and the deterministic action. Surfacing it is the point: a
reviewer needs to know when the AI and the code reached different conclusions,
and hiding it would make the AI look more authoritative than it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.advocate import AdvocateRunResponse
from app.models.analysis import CaseAnalysisResponse
from app.models.explanation import (
    DecisionExplanation,
    ExplainedClaim,
    ExplainedFact,
    ExplainedRebuttal,
    ExplainedRule,
)
from app.models.judge import JudgeResult
from app.models.rebuttal import RebuttalRunResponse


@dataclass(frozen=True)
class ThresholdRule:
    """A PolicyTwin rule that compares a measured fact to a policy threshold.

    ``fact`` is a label only. The values always come from the rule evaluation, so
    this mapping cannot become a second source of truth for a measurement.
    """

    rule_id: str
    fact: str


# The rules whose ``actualValue`` is a measured fact a human would recognise.
# Rules about evidence presence or consistency are excluded: their "actual value"
# is a boolean about the record, not a measurement, and presenting it as a
# decisive physical fact would misdescribe it.
THRESHOLD_RULES: dict[str, ThresholdRule] = {
    "ROUTE_UNEXPLAINED_DEVIATION": ThresholdRule(
        rule_id="ROUTE_UNEXPLAINED_DEVIATION", fact="UNEXPLAINED_DEVIATION_KM"
    ),
    "NO_SHOW_PICKUP_RADIUS": ThresholdRule(
        rule_id="NO_SHOW_PICKUP_RADIUS", fact="DRIVER_PICKUP_DISTANCE_METERS"
    ),
    "NO_SHOW_WAIT_TIME": ThresholdRule(
        rule_id="NO_SHOW_WAIT_TIME", fact="WAITING_DURATION_SECONDS"
    ),
}


class DecisionExplanationService:
    """Projects the trusted explanation. Pure and offline."""

    def build(
        self,
        analysis: CaseAnalysisResponse,
        advocate_run: AdvocateRunResponse,
        rebuttal_run: RebuttalRunResponse,
        judge_result: JudgeResult,
    ) -> DecisionExplanation:
        recommendation = analysis.resolution_recommendation
        advisory = judge_result.recommended_outcome

        return DecisionExplanation(
            accepted_claims=self._claims(advocate_run, judge_result),
            relevant_rebuttals=self._rebuttals(rebuttal_run, judge_result),
            decisive_facts=self._facts(analysis),
            policy_rules=self._rules(analysis),
            final_deterministic_action=recommendation.recommended_action,
            ruling=recommendation.ruling,
            deterministic_basis=recommendation.explanation,
            judge_advisory_outcome=advisory,
            judge_advisory_differs=bool(
                advisory and advisory != recommendation.recommended_action
            ),
        )

    # -- claims -----------------------------------------------------------

    @staticmethod
    def _claims(
        advocate_run: AdvocateRunResponse, judge_result: JudgeResult
    ) -> list[ExplainedClaim]:
        """Every verified claim, marked with whether the Judge relied on it.

        Verified-but-not-accepted claims are included with ``accepted=False``.
        Omitting them would make the explanation look as though the Judge
        considered only what it cited, which is a stronger claim than the record
        supports.
        """
        accepted = set(judge_result.accepted_rider_claim_ids) | set(
            judge_result.accepted_driver_claim_ids
        )
        explained: list[ExplainedClaim] = []
        for side_result in (advocate_run.rider, advocate_run.driver):
            for claim in side_result.verified_claims:
                # Judge citations are namespaced (RIDER-R1); the claim carries its
                # bare ID, so match on both forms.
                namespaced = f"{side_result.side}-{claim.claim_id}"
                explained.append(
                    ExplainedClaim(
                        claim_id=namespaced,
                        side=side_result.side,  # type: ignore[arg-type]
                        claim=claim.claim,
                        accepted=namespaced in accepted or claim.claim_id in accepted,
                    )
                )
        return explained

    # -- rebuttals --------------------------------------------------------

    @staticmethod
    def _rebuttals(
        rebuttal_run: RebuttalRunResponse, judge_result: JudgeResult
    ) -> list[ExplainedRebuttal]:
        """Verified rebuttals only, marked with whether the Judge cited them.

        Rejected rebuttals are absent. They are visible in the cross-examination
        panel where a reviewer can see why they failed, but they are not part of
        the explanation of a decision they never influenced.
        """
        considered = set(judge_result.considered_rider_rebuttal_ids) | set(
            judge_result.considered_driver_rebuttal_ids
        )
        explained: list[ExplainedRebuttal] = []
        for side_result in (rebuttal_run.rider, rebuttal_run.driver):
            for rebuttal in side_result.verified_rebuttals:
                explained.append(
                    ExplainedRebuttal(
                        rebuttal_id=rebuttal.rebuttal_id,
                        side=rebuttal.side,  # type: ignore[arg-type]
                        target_claim_id=rebuttal.target_claim_id,
                        stance=rebuttal.stance,
                        reasoning_summary=rebuttal.reasoning_summary,
                        considered_by_judge=rebuttal.rebuttal_id in considered,
                    )
                )
        return explained

    # -- facts and rules --------------------------------------------------

    @staticmethod
    def _facts(analysis: CaseAnalysisResponse) -> list[ExplainedFact]:
        """Measured facts, each paired with the threshold it was tested against."""
        facts: list[ExplainedFact] = []
        for rule in analysis.policy_evaluation.evaluated_rules:
            threshold_rule = THRESHOLD_RULES.get(rule.rule_id)
            if threshold_rule is None:
                continue
            facts.append(
                ExplainedFact(
                    fact=threshold_rule.fact,
                    value=rule.actual_value,
                    rule_id=rule.rule_id,
                    threshold=rule.required_value,
                    passed=rule.passed,
                    description=rule.description,
                )
            )
        return facts

    @staticmethod
    def _rules(analysis: CaseAnalysisResponse) -> list[ExplainedRule]:
        return [
            ExplainedRule(
                rule_id=rule.rule_id,
                description=rule.description,
                passed=rule.passed,
                actual_value=rule.actual_value,
                required_value=rule.required_value,
                evidence_ids=list(rule.evidence_ids),
            )
            for rule in analysis.policy_evaluation.evaluated_rules
        ]


__all__ = ["DecisionExplanationService", "ThresholdRule", "THRESHOLD_RULES"]
