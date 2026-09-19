from __future__ import annotations

from app.models.analysis import RouteCondition, RouteDeviationAnalysis
from app.models.case import DisputeCase
from app.policies import RouteDeviationPolicy


class RouteDeviationService:
    def analyze(self, case: DisputeCase, policy: RouteDeviationPolicy) -> RouteDeviationAnalysis:
        payload = case.analysis_input.route_deviation
        if payload is None:
            raise ValueError("Route deviation analysis input is required")

        evidence_by_id = {item.id: item for item in case.evidence}
        missing = [item_id for item_id in policy.required_evidence_ids if item_id not in evidence_by_id]
        contradictory = any(item.status == "Conflicting" for item in case.evidence)
        distance_difference = max(payload.actual_route_distance_km - payload.expected_route_distance_km, 0)
        deviation_percentage = (distance_difference / payload.expected_route_distance_km) * 100
        duration_difference = max(payload.actual_duration_minutes - payload.expected_duration_minutes, 0)
        fare_difference = max(case.fare.actual - case.fare.quoted, 0)

        valid_conditions = [
            condition for condition in payload.conditions
            if all(evidence_by_id.get(evidence_id) and evidence_by_id[evidence_id].status == "Verified" for evidence_id in condition.evidence_ids)
        ]
        explained_distance = min(sum(condition.explained_distance_km for condition in valid_conditions), distance_difference)
        unexplained_distance = max(distance_difference - explained_distance, 0)
        condition_evidence = [evidence_id for condition in valid_conditions for evidence_id in condition.evidence_ids]
        supporting = list(dict.fromkeys([*policy.required_evidence_ids, *condition_evidence]))

        return RouteDeviationAnalysis(
            expected_route_distance_km=round(payload.expected_route_distance_km, 3),
            actual_route_distance_km=round(payload.actual_route_distance_km, 3),
            distance_difference_km=round(distance_difference, 3),
            deviation_percentage=round(deviation_percentage, 2),
            expected_trip_duration_minutes=round(payload.expected_duration_minutes, 2),
            actual_trip_duration_minutes=round(payload.actual_duration_minutes, 2),
            duration_difference_minutes=round(duration_difference, 2),
            expected_fare=round(case.fare.quoted, 2),
            actual_fare=round(case.fare.actual, 2),
            fare_difference=round(fare_difference, 2),
            explained_deviation_distance_km=round(explained_distance, 3),
            unexplained_deviation_distance_km=round(unexplained_distance, 3),
            conditions=[RouteCondition(type=item.type, explained_distance_km=item.explained_distance_km, evidence_ids=item.evidence_ids) for item in valid_conditions],
            required_evidence_ids=list(policy.required_evidence_ids),
            missing_evidence_ids=missing,
            contradictory_evidence=contradictory,
            supporting_evidence_ids=supporting,
        )
