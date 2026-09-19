from __future__ import annotations

from datetime import datetime
from math import asin, cos, radians, sin, sqrt

from app.models.analysis import GeoPoint, NoShowAnalysis
from app.models.case import DisputeCase
from app.policies import NoShowPolicy


class NoShowAnalysisService:
    @staticmethod
    def haversine_distance_meters(origin: GeoPoint, destination: GeoPoint) -> float:
        earth_radius_meters = 6_371_008.8
        delta_latitude = radians(destination.latitude - origin.latitude)
        delta_longitude = radians(destination.longitude - origin.longitude)
        latitude_1 = radians(origin.latitude)
        latitude_2 = radians(destination.latitude)
        haversine = sin(delta_latitude / 2) ** 2 + cos(latitude_1) * cos(latitude_2) * sin(delta_longitude / 2) ** 2
        return earth_radius_meters * 2 * asin(sqrt(haversine))

    def analyze(self, case: DisputeCase, policy: NoShowPolicy) -> NoShowAnalysis:
        payload = case.analysis_input.no_show
        if payload is None:
            raise ValueError("No-show analysis input is required")
        evidence_by_id = {item.id: item for item in case.evidence}
        missing = [item_id for item_id in policy.required_evidence_ids if item_id not in evidence_by_id or evidence_by_id[item_id].status == "Missing"]
        contradictory = any(item.status == "Conflicting" for item in case.evidence)
        arrival = datetime.strptime(payload.driver_arrival_timestamp, "%H:%M:%S")
        cancellation = datetime.strptime(payload.cancellation_timestamp, "%H:%M:%S")
        waiting_seconds = max(int((cancellation - arrival).total_seconds()), 0)
        distance_meters = self.haversine_distance_meters(payload.pickup_coordinates, payload.driver_arrival_coordinates)
        support = list(dict.fromkeys([*policy.required_evidence_ids, *payload.rider_message_evidence_ids, *payload.driver_message_evidence_ids]))

        return NoShowAnalysis(
            driver_distance_to_pickup_meters=round(distance_meters, 2),
            driver_within_pickup_radius=distance_meters <= policy.pickup_radius_meters,
            driver_arrival_timestamp=payload.driver_arrival_timestamp,
            cancellation_timestamp=payload.cancellation_timestamp,
            waiting_duration_seconds=waiting_seconds,
            rider_message_evidence_ids=payload.rider_message_evidence_ids,
            driver_message_evidence_ids=payload.driver_message_evidence_ids,
            cancellation_charge_amount=round(payload.cancellation_charge_amount, 2),
            required_evidence_ids=list(policy.required_evidence_ids),
            missing_evidence_ids=missing,
            contradictory_evidence=contradictory,
            supporting_evidence_ids=support,
        )
