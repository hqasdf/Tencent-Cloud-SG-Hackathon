"""Deterministic counterfactuals.

"What would have changed the decision?" is answered here by varying exactly one
thing: the measured value a policy rule compares against its own threshold.

Every threshold comes from the PolicyTwin evaluation's ``requiredValue``. This
module does not define, guess, or round a single threshold, and there is no field
in its output that could carry a hypothetical narrative. That restriction is the
whole design:

    good   waitingDurationSeconds: 480 -> policy threshold 300
    bad    "maybe the rider called the driver"

The first is a counterfactual, because it names an input the deterministic engine
actually reads and a threshold the policy actually sets. The second is a story,
and a story cannot be evaluated — it would let the system appear to reason about
possibilities it has no basis to consider.

The comparison and current result together determine the exact movement needed
to flip a rule. A minimum-wait rule uses ``actual >= threshold``: a failing value
must rise *to or above* the threshold, while a passing value must fall *below*
it. A pickup-radius rule uses ``actual <= threshold`` and reverses those moves.
Equality is therefore explicit rather than hidden inside a vague direction.
"""

from __future__ import annotations

from typing import Literal

from app.models.analysis import CaseAnalysisResponse
from app.models.explanation import CounterfactualThreshold, DecisionCounterfactual
from app.services.explanation import THRESHOLD_RULES

# Which way a measured value must move for the rule to fail.
#
#   ROUTE_UNEXPLAINED_DEVIATION  passes when actual >= required  -> fails BELOW
#   NO_SHOW_PICKUP_RADIUS        passes when actual <= required  -> fails ABOVE
#   NO_SHOW_WAIT_TIME            passes when actual >= required  -> fails BELOW
_DIRECTIONS: dict[str, Literal["BELOW", "ABOVE"]] = {
    "ROUTE_UNEXPLAINED_DEVIATION": "BELOW",
    "NO_SHOW_PICKUP_RADIUS": "ABOVE",
    "NO_SHOW_WAIT_TIME": "BELOW",
}

_COMPARISONS: dict[
    str, Literal["GREATER_THAN_OR_EQUAL", "LESS_THAN_OR_EQUAL"]
] = {
    "ROUTE_UNEXPLAINED_DEVIATION": "GREATER_THAN_OR_EQUAL",
    "NO_SHOW_PICKUP_RADIUS": "LESS_THAN_OR_EQUAL",
    "NO_SHOW_WAIT_TIME": "GREATER_THAN_OR_EQUAL",
}

_NO_THRESHOLD_STATEMENT = (
    "This decision does not turn on a policy threshold that the recorded values "
    "could have crossed, so no change to a measured value would alter it."
)


class CounterfactualService:
    """Projects the threshold counterfactuals. Pure and offline."""

    def build(self, analysis: CaseAnalysisResponse) -> DecisionCounterfactual:
        recommendation = analysis.resolution_recommendation
        thresholds = self._thresholds(analysis)

        if thresholds:
            statement = " ".join(item.statement for item in thresholds)
        else:
            statement = _NO_THRESHOLD_STATEMENT

        return DecisionCounterfactual(
            thresholds=thresholds,
            statement=statement,
            # The deterministic engine's own counterfactual text is copied
            # verbatim rather than paraphrased. Paraphrasing would be a place for
            # a new claim to enter.
            deterministic_basis=recommendation.counterfactual_explanation,
            generated_by="DETERMINISTIC_ENGINE",
        )

    @staticmethod
    def _thresholds(analysis: CaseAnalysisResponse) -> list[CounterfactualThreshold]:
        thresholds: list[CounterfactualThreshold] = []
        for rule in analysis.policy_evaluation.evaluated_rules:
            threshold_rule = THRESHOLD_RULES.get(rule.rule_id)
            direction = _DIRECTIONS.get(rule.rule_id)
            comparison = _COMPARISONS.get(rule.rule_id)
            if threshold_rule is None or direction is None or comparison is None:
                continue
            change_direction = _change_direction(comparison, rule.passed)
            thresholds.append(
                CounterfactualThreshold(
                    rule_id=rule.rule_id,
                    fact=threshold_rule.fact,
                    actual_value=rule.actual_value,
                    threshold=rule.required_value,
                    direction=direction,
                    comparison_operator=comparison,
                    change_direction=change_direction,
                    passed=rule.passed,
                    statement=_statement(
                        fact=threshold_rule.fact,
                        actual=rule.actual_value,
                        threshold=rule.required_value,
                        comparison=comparison,
                        passed=rule.passed,
                    ),
                )
            )
        return thresholds


def _statement(
    *,
    fact: str,
    actual: object,
    threshold: object,
    comparison: str,
    passed: bool,
) -> str:
    """One sentence naming the fact, its current value, and the policy threshold.

    Written from the rule's own numbers. ``passed`` changes only the framing, not
    the numbers, so a passing rule still reports the margin it passed by.
    """
    if passed:
        failure_boundary = (
            f"below {threshold}"
            if comparison == "GREATER_THAN_OR_EQUAL"
            else f"above {threshold}"
        )
        return (
            f"{fact} is {actual}, which meets the policy threshold of {threshold}. "
            f"Had it been {failure_boundary}, this rule would not have been met."
        )
    passing_boundary = (
        f"at or above {threshold}"
        if comparison == "GREATER_THAN_OR_EQUAL"
        else f"at or below {threshold}"
    )
    return (
        f"{fact} is {actual}, which does not meet the policy threshold of {threshold}. "
        f"Had it been {passing_boundary}, this rule would have been met."
    )


def _change_direction(
    comparison: str, passed: bool
) -> Literal[
    "INCREASE_TO_OR_ABOVE",
    "DECREASE_BELOW",
    "DECREASE_TO_OR_BELOW",
    "INCREASE_ABOVE",
]:
    """Return the inclusive/exclusive move that flips the current rule result."""
    if comparison == "GREATER_THAN_OR_EQUAL":
        return "DECREASE_BELOW" if passed else "INCREASE_TO_OR_ABOVE"
    return "INCREASE_ABOVE" if passed else "DECREASE_TO_OR_BELOW"


__all__ = ["CounterfactualService"]
