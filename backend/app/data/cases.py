from __future__ import annotations

from app.models.case import DisputeCase


ROUTE_METRICS = {
    "expectedRouteDistanceKm": 5.8,
    "actualRouteDistanceKm": 7.1,
    "expectedDurationMinutes": 18,
    "actualDurationMinutes": 26,
}


def _timeline(is_route_case: bool, conflicting: bool = False) -> list[dict]:
    completion_time = "09:40" if is_route_case else "09:15"
    events = [
        {"id": "E01", "timestamp": "09:02", "type": "Booking accepted", "description": "Driver accepted the booking.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "09:09", "type": "Driver arrived", "description": "Driver location recorded at pickup point.", "evidence_ids": ["E02"]},
        {"id": "E03", "timestamp": "09:12", "type": "Trip started", "description": "Trip started and GPS telemetry began.", "evidence_ids": ["E03"]},
    ]
    if is_route_case:
        events.append({"id": "E04", "timestamp": "09:26", "type": "Route deviation", "description": "Actual route diverged from the recommended route.", "evidence_ids": ["E04", "E05"], "severity": "attention"})
    events.extend([
        {"id": "E06", "timestamp": "09:31" if is_route_case else "09:11", "type": "Chat message", "description": "Driver reported heavier traffic ahead." if is_route_case else "Driver notified rider that waiting time had started.", "evidence_ids": ["E06"]},
        {"id": "E07", "timestamp": completion_time, "type": "Trip completed" if is_route_case else "Cancellation", "description": "Trip completed and final fare calculated." if is_route_case else "System recorded cancellation and payment event.", "evidence_ids": ["E07", "E08"], "severity": "conflict" if conflicting else "normal"},
    ])
    return events


def _evidence(is_route_case: bool, conflicting: bool = False, fully_explained: bool = False) -> list[dict]:
    completion_time = "09:40" if is_route_case else "09:15"
    evidence = [
        {"id": "E01", "type": "Trip event", "timestamp": "09:02", "source": "Trip service", "summary": "Booking acceptance record.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "09:09", "source": "Driver telemetry", "summary": "Pickup arrival confirmed." if is_route_case else "Arrival within 75 m pickup radius.", "status": "Conflicting" if conflicting else "Verified"},
        {"id": "E03", "type": "GPS", "timestamp": "09:12", "source": "Trip telemetry", "summary": "Trip GPS trace and timestamps.", "status": "Conflicting" if conflicting else "Verified"},
        {"id": "E04", "type": "GPS" if is_route_case else "Trip event", "timestamp": "09:26" if is_route_case else "09:09", "source": "Route comparison" if is_route_case else "Cancellation service", "summary": "Route deviation segment detected." if is_route_case else "Waiting timer started.", "status": "Verified"},
        {"id": "E05", "type": "Fare" if is_route_case else "Policy", "timestamp": completion_time, "source": "Payment ledger" if is_route_case else "Policy engine", "summary": "Fare difference calculated from final trip charge." if is_route_case else "No-show waiting threshold rule.", "status": "Verified"},
        {"id": "E06", "type": "Traffic" if fully_explained else "Chat", "timestamp": "09:31" if is_route_case else "09:11", "source": "City traffic feed" if fully_explained else "In-app messages", "summary": "Verified congestion event covers the alternate route segment." if fully_explained else "Driver message identifies traffic conditions." if is_route_case else "Driver message states waiting has begun.", "status": "Verified"},
        {"id": "E07", "type": "Trip event", "timestamp": completion_time, "source": "Trip service", "summary": "Final trip or cancellation event.", "status": "Conflicting" if conflicting else "Verified"},
        {"id": "E08", "type": "Policy", "timestamp": completion_time, "source": "Policy registry", "summary": "Route deviation refund policy." if is_route_case else "No-show charge policy.", "status": "Verified"},
    ]
    return evidence


def _activity(status: str) -> list[dict]:
    labels = ["Case received", "Evidence collected", "Rider Advocate analysing", "Driver Advocate analysing", "Claims verified", "Policy evaluated", "Judge reviewing", "Resolution generated"]
    result = []
    for index, label in enumerate(labels):
        active = (index == 6 and status == "Investigating") or (index == 7 and status == "Human Review")
        state = "complete" if index < 6 or (index == 7 and status == "Auto Resolved") else "active" if active else "queued"
        detail = "Completed with traceable outputs." if index < 6 else "Awaiting a human reviewer." if active and status == "Human Review" else "Decision workflow status."
        result.append({"id": f"A{index + 1}", "label": label, "state": state, "detail": detail})
    return result


def _analysis_input(dispute_type: str, route_conditions: list[dict], conflicting: bool) -> dict:
    if dispute_type == "route_deviation":
        return {"routeDeviation": {**ROUTE_METRICS, "conditions": route_conditions}}
    return {"noShow": {
        "pickupCoordinates": {"latitude": 1.286, "longitude": 103.827},
        "driverArrivalCoordinates": {"latitude": 1.28636, "longitude": 103.827},
        "driverArrivalTimestamp": "09:09:00",
        "cancellationTimestamp": "09:15:12",
        "riderMessageEvidenceIds": [],
        "driverMessageEvidenceIds": ["E06"],
        "cancellationChargeAmount": 6.0,
    }}


def build_case(case_id: str, dispute_type: str, title: str, status: str, *, route_conditions: list[dict] | None = None, conflicting: bool = False) -> DisputeCase:
    is_route_case = dispute_type == "route_deviation"
    route_conditions = route_conditions or []
    fully_explained = is_route_case and sum(item["explainedDistanceKm"] for item in route_conditions) >= ROUTE_METRICS["actualRouteDistanceKm"] - ROUTE_METRICS["expectedRouteDistanceKm"]
    evidence = _evidence(is_route_case, conflicting, fully_explained)
    route_policy = "P_ROUTE_01" if is_route_case else "P_NOSHOW_01"

    # These legacy display fields are overwritten by deterministic analysis in CaseService.
    resolution = {"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE", "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": ["C-D-02"], "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.", "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."}
    return DisputeCase(
        id=case_id, dispute_type=dispute_type, title=title, status=status,
        description="Rider disputes a fare after a route longer than the estimated journey." if is_route_case else "Rider disputes a no-show cancellation charge.",
        rider={"name": "Amelia Tan", "id": "R-1048"}, driver={"name": "Marcus Lim", "id": "D-2891"},
        trip={"trip_id": f"TRP-{case_id[-4:]}", "pickup": "Tiong Bahru MRT", "destination": "Raffles Place", "booked_at": "19 Sep 2026, 09:00", "distance": "Expected 5.8 km / actual 7.1 km" if is_route_case else "Pickup radius 75 m", "duration": "Expected 18 min / actual 26 min" if is_route_case else "Driver waited 6 min 12 sec"},
        fare={"currency": "SGD", "quoted": 11.2 if is_route_case else 6, "actual": 13.5 if is_route_case else 6, "difference": 2.3 if is_route_case else 0},
        rider_complaint="The driver took a much longer route and I was overcharged." if is_route_case else "I was charged even though the driver did not arrive correctly.",
        driver_response="Traffic near the planned road required an alternate route." if is_route_case else "I arrived at the pickup point and waited beyond the required period.",
        metadata={"submitted_at": "19 Sep 2026, 10:05", "last_updated": "19 Sep 2026, 10:12", "priority": "High" if conflicting else "Standard", "policy_version": "2026.09"},
        timeline=_timeline(is_route_case, conflicting), evidence=evidence,
        rider_case={"party": "Rider", "summary": "Measured route and fare evidence support a partial adjustment." if is_route_case else "Rider questions the arrival and waiting-time record.", "claims": [{"id": "C-R-01", "claim": "The route was significantly longer than expected." if is_route_case else "The no-show charge should not apply.", "evidence_ids": ["E04", "E05"] if is_route_case else ["E02", "E07"], "policy_refs": [route_policy], "status": "Verified"}]},
        driver_case={"party": "Driver", "summary": "Structured route conditions are evaluated deterministically." if is_route_case else "Arrival and wait records are evaluated deterministically.", "claims": [{"id": "C-D-01", "claim": "Traffic conditions justified the alternate route segment." if is_route_case else "The driver arrived within the pickup radius and waited long enough.", "evidence_ids": ["E02", "E06"], "policy_refs": [route_policy], "status": "Verified"}, {"id": "C-D-02", "claim": "Unverified statement should determine the ruling.", "evidence_ids": ["E99"], "policy_refs": [route_policy], "status": "Rejected", "reason": "Evidence ID is not in the case record."}]},
        activity=_activity(status),
        policy_result={"policy_id": route_policy, "name": "Pending deterministic evaluation", "rule_summary": "Prototype policy is evaluated by backend code.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0, "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution=resolution,
        human_review_summary=None,
        analysis_input=_analysis_input(dispute_type, route_conditions, conflicting),
    )


MOCK_CASES = [
    build_case("CASE-2026-1041", "route_deviation", "Route deviation with partially unexplained fare", "Auto Resolved", route_conditions=[{"type": "traffic_diversion", "explainedDistanceKm": 0.5, "evidenceIds": ["E06"]}]),
    build_case("CASE-2026-1042", "route_deviation", "Route deviation fully supported by traffic records", "Auto Resolved", route_conditions=[{"type": "traffic_diversion", "explainedDistanceKm": 1.3, "evidenceIds": ["E06"]}]),
    build_case("CASE-2026-1043", "no_show_charge", "No-show charge supported by arrival and wait evidence", "Auto Resolved"),
    build_case("CASE-2026-1044", "no_show_charge", "No-show charge with conflicting arrival evidence", "Human Review", conflicting=True),
]
