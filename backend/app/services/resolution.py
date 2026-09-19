from __future__ import annotations

from app.models.analysis import NoShowAnalysis, PolicyEvaluation, ResolutionCalculation, ResolutionRecommendation, RouteDeviationAnalysis


class ResolutionEngine:
    def recommend_route(self, analysis: RouteDeviationAnalysis, policy: PolicyEvaluation, currency: str) -> ResolutionRecommendation:
        distance_ratio = analysis.unexplained_deviation_distance_km / analysis.distance_difference_km if analysis.distance_difference_km > 0 else 0
        unexplained_fare_impact = round(analysis.fare_difference * distance_ratio, 2)
        explained_fare_impact = round(analysis.fare_difference - unexplained_fare_impact, 2)
        calculation = ResolutionCalculation(
            fare_difference=analysis.fare_difference,
            explained_fare_impact=explained_fare_impact,
            unexplained_fare_impact=unexplained_fare_impact,
            ratio_applied=round(distance_ratio, 4),
        )
        if policy.overall_outcome == "POLICY_NOT_APPLICABLE":
            return ResolutionRecommendation(
                ruling="Pending deterministic validation", recommended_action="HUMAN_REVIEW", refund_amount=0, currency=currency,
                calculation=calculation,
                explanation="Critical route evidence is missing or contradictory, so the prototype policy cannot be applied safely.",
                counterfactual_explanation="A complete, consistent route and fare record would allow a deterministic adjustment recommendation.",
            )
        if policy.overall_outcome == "NO_ELIGIBLE_UNEXPLAINED_DEVIATION":
            return ResolutionRecommendation(
                ruling="No refund recommended", recommended_action="NO_REFUND", refund_amount=0, currency=currency,
                calculation=calculation,
                explanation="All measured route deviation is explained by verified structured conditions or is below the prototype threshold.",
                counterfactual_explanation="An additional verified unexplained route segment above the prototype threshold would create an adjustment recommendation.",
            )
        action = "FULL_FARE_DIFFERENCE_REFUND" if unexplained_fare_impact == analysis.fare_difference else "PARTIAL_REFUND"
        return ResolutionRecommendation(
            ruling="Partial refund recommended" if action == "PARTIAL_REFUND" else "Fare difference refund recommended",
            recommended_action=action,
            refund_amount=unexplained_fare_impact,
            currency=currency,
            calculation=calculation,
            explanation="The refund is calculated from the fare difference in proportion to the verified unexplained route deviation.",
            counterfactual_explanation="Verified conditions explaining the remaining route deviation would reduce or remove the calculated refund.",
        )

    def recommend_no_show(self, analysis: NoShowAnalysis, policy: PolicyEvaluation, currency: str) -> ResolutionRecommendation:
        calculation = ResolutionCalculation(fare_difference=0, explained_fare_impact=0, unexplained_fare_impact=analysis.cancellation_charge_amount, ratio_applied=1)
        if policy.overall_outcome == "POLICY_NOT_APPLICABLE":
            return ResolutionRecommendation(
                ruling="Pending deterministic validation", recommended_action="HUMAN_REVIEW", refund_amount=0, currency=currency,
                calculation=calculation,
                explanation="Conflicting or missing arrival and cancellation evidence prevents the prototype no-show policy from being applied.",
                counterfactual_explanation="Consistent GPS and timestamp records would allow the pickup-radius and waiting-time rules to decide the charge.",
            )
        if policy.overall_outcome == "UPHOLD_CANCELLATION_CHARGE":
            return ResolutionRecommendation(
                ruling="Cancellation charge upheld", recommended_action="UPHOLD_CANCELLATION_CHARGE", refund_amount=0, currency=currency,
                calculation=ResolutionCalculation(fare_difference=0, explained_fare_impact=analysis.cancellation_charge_amount, unexplained_fare_impact=0, ratio_applied=0),
                explanation="The driver arrived within the configured pickup radius and waited at least the prototype threshold.",
                counterfactual_explanation="A distance outside the pickup radius or a verified wait below the threshold would refund the cancellation charge.",
            )
        return ResolutionRecommendation(
            ruling="Cancellation charge refund recommended", recommended_action="REFUND_CANCELLATION_CHARGE", refund_amount=analysis.cancellation_charge_amount, currency=currency,
            calculation=calculation,
            explanation="The prototype no-show policy conditions for upholding the cancellation charge were not met.",
            counterfactual_explanation="Verified arrival within the pickup radius and a wait above the threshold would uphold the charge.",
        )
