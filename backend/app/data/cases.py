from __future__ import annotations

from app.models.case import DisputeCase
from app.models.analysis import AnalysisInput, NoShowInput, RouteDeviationInput, GeoPoint, RouteConditionInput


def _activity(status: str) -> list[dict]:
    labels = [
        "Case received", "Evidence collected", "Rider Advocate analysing",
        "Driver Advocate analysing", "Claims verified", "Policy evaluated",
        "Judge reviewing", "Resolution generated",
    ]
    result = []
    for index, label in enumerate(labels):
        active = (index == 6 and status == "Investigating") or (index == 7 and status == "Human Review")
        state = "complete" if index < 6 or (index == 7 and status == "Auto Resolved") else "active" if active else "queued"
        detail = "Completed with traceable outputs." if index < 6 else "Awaiting a human reviewer." if active and status == "Human Review" else "Decision workflow status."
        result.append({"id": f"A{index + 1}", "label": label, "state": state, "detail": detail})
    return result


# ─────────────────────────────────────────────────────────────────────
# Case 1 — No-show charge: Tiong Bahru Plaza (original case)
# ─────────────────────────────────────────────────────────────────────
def _case1_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "08:30", "type": "Booking confirmed", "description": "Rider R-7823 booked trip TRIP-2026-09945 from Tiong Bahru Plaza to VivoCity. Scheduled pickup 08:45.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "08:43", "type": "Driver arrived", "description": "Driver GPS within 10m of pickup point. Speed 0 km/h. Auto-arrival confirmed.", "evidence_ids": ["E02", "E03"]},
        {"id": "E03", "timestamp": "08:43", "type": "Wait timer started", "description": "Free wait timer started. 5 min free wait period ends at 08:48.", "evidence_ids": ["E03"]},
        {"id": "E04", "timestamp": "08:47", "type": "Driver called rider", "description": "Driver initiated in-app call to rider. Call rang 22s, no answer.", "evidence_ids": ["E04"]},
        {"id": "E05", "timestamp": "08:48", "type": "Wait timer expired", "description": "Free 5-min wait period expired. Rider had not boarded. Cancellation fee now applicable per policy.", "evidence_ids": ["E05"]},
        {"id": "E06", "timestamp": "08:51", "type": "Cancellation fee applied", "description": "No-show threshold (8 min) reached. $5.00 cancellation fee charged to rider payment method.", "evidence_ids": ["E06"], "severity": "conflict"},
    ]


def _case1_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "08:30", "source": "Trip service", "summary": "Booking confirmed. Rider R-7823 booked trip TRIP-2026-09945.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "08:43", "source": "Driver telemetry", "summary": "Driver GPS within 10m of pickup point (Tiong Bahru Plaza). Speed 0 km/h.", "status": "Verified"},
        {"id": "E03", "type": "Trip event", "timestamp": "08:43", "source": "App events", "summary": "driver_arrived event. Wait timer started. Push notification sent to rider.", "status": "Verified"},
        {"id": "E04", "type": "Chat", "timestamp": "08:43–08:50", "source": "In-app messages", "summary": "Driver sent 4 messages + 1 call; rider sent 0. Driver attempted contact, rider did not respond.", "status": "Verified"},
        {"id": "E05", "type": "Policy", "timestamp": "08:48", "source": "Policy engine", "summary": "Free 5-min wait expired at 08:48. Cancellation fee now applicable per no-show policy.", "status": "Verified"},
        {"id": "E06", "type": "Trip event", "timestamp": "08:51", "source": "Cancellation service", "summary": "No-show threshold (8 min) reached. $5.00 cancellation fee applied to rider e-wallet.", "status": "Verified"},
    ]


def _build_case1() -> DisputeCase:
    return DisputeCase(
        id="DISP-002", dispute_type="no_show_charge",
        title="No-show charge dispute — Tiong Bahru Plaza",
        status="Auto Resolved",
        description="Rider disputes a no-show cancellation charge after the driver waited beyond the free wait period.",
        rider={"name": "Michael Wong", "id": "R-7823"},
        driver={"name": "Lim Wei Ming", "id": "D-2398"},
        trip={"trip_id": "TRIP-2026-09945", "pickup": "Tiong Bahru Plaza", "destination": "VivoCity",
              "booked_at": "13 Sep 2026, 08:45", "distance": "Pickup radius 100 m (driver within 10m)",
              "duration": "Driver waited 8 min (free 5 min + 3 min over)"},
        fare={"currency": "SGD", "quoted": 0, "actual": 5.0, "difference": 5.0},
        rider_complaint="I was at the pickup point at Tiong Bahru Plaza on time but the driver never showed up. I waited 10 minutes at the lobby and couldn't find the car. The app charged me a $5.00 cancellation fee for a 'no-show' which is completely unfair — I was there, the driver was not.",
        driver_response="I arrived at the pickup point at 08:43, 2 minutes before the scheduled time. I waited at the lobby, sent 4 messages and called the rider, but got no response. After the free 5-minute wait and the no-show threshold, the system correctly applied the cancellation fee.",
        metadata={"submitted_at": "13 Sep 2026, 09:20", "last_updated": "13 Sep 2026, 09:20",
                  "priority": "Standard", "policy_version": "2026.09"},
        timeline=_case1_timeline(), evidence=_case1_evidence(),
        rider_case={"party": "Rider", "summary": "Rider claims they were at the pickup point on time and the driver did not show up.",
                    "claims": [{"id": "C-R-01", "claim": "The no-show charge should not apply because I was present at the pickup location.",
                                "evidence_ids": [], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver arrived early, waited the full free-wait + no-show window, and attempted contact.",
                     "claims": [
                         {"id": "C-D-01", "claim": "The driver arrived within the pickup radius and waited beyond the minimum wait period.",
                          "evidence_ids": ["E02", "E03", "E05"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Verified"},
                         {"id": "C-D-02", "claim": "The driver attempted contact via 4 messages and 1 call; the rider did not respond.",
                          "evidence_ids": ["E04"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Verified"},
                     ]},
        activity=_activity("Auto Resolved"),
        policy_result={"policy_id": "NO_SHOW_POLICY_V1", "name": "Pending deterministic evaluation",
                       "rule_summary": "Prototype policy is evaluated by backend code.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(no_show=NoShowInput(
            pickup_coordinates=GeoPoint(latitude=1.2847, longitude=103.8382),
            driver_arrival_coordinates=GeoPoint(latitude=1.2847, longitude=103.8382),
            driver_arrival_timestamp="08:43:00",
            cancellation_timestamp="08:51:00",
            rider_message_evidence_ids=[],
            driver_message_evidence_ids=["E04"],
            cancellation_charge_amount=5.0,
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Case 2 — Route deviation: Driver took longer route, overcharged
# ─────────────────────────────────────────────────────────────────────
def _case2_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "14:02", "type": "Booking confirmed", "description": "Rider R-3421 booked trip TRIP-2026-09951 from Jurong East MRT to Changi Airport T3. Quoted fare $22.50.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "14:10", "type": "Trip started", "description": "Trip started. Expected route via PIE, 18.2 km, estimated 22 min.", "evidence_ids": ["E02"]},
        {"id": "E03", "timestamp": "14:35", "type": "Route deviation detected", "description": "GPS shows driver took TPE instead of PIE. Actual distance 26.8 km, 35 min.", "evidence_ids": ["E03"], "severity": "conflict"},
        {"id": "E04", "timestamp": "14:45", "type": "Trip completed", "description": "Trip ended at Changi Airport T3. Final fare $34.20 — $11.70 above quote.", "evidence_ids": ["E04"], "severity": "conflict"},
        {"id": "E05", "timestamp": "14:46", "type": "Rider disputed fare", "description": "Rider filed dispute claiming driver took unnecessary detour.", "evidence_ids": ["E05"]},
    ]


def _case2_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "14:02", "source": "Trip service", "summary": "Booking confirmed. Quoted fare $22.50 for Jurong East MRT → Changi Airport T3.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "14:10", "source": "Route engine", "summary": "Expected route: PIE, 18.2 km, 22 min. GPS tracking active.", "status": "Verified"},
        {"id": "E03", "type": "GPS", "timestamp": "14:35", "source": "Driver telemetry", "summary": "Route deviation: driver took TPE. Actual 26.8 km, 35 min. No traffic justification.", "status": "Verified"},
        {"id": "E04", "type": "Fare", "timestamp": "14:45", "source": "Fare engine", "summary": "Final fare $34.20 (distance + time charges). Quote was $22.50. Difference $11.70.", "status": "Verified"},
        {"id": "E05", "type": "Chat", "timestamp": "14:42", "source": "In-app messages", "summary": "Rider messaged driver asking why route changed. Driver replied 'GPS told me to'. No traffic alert on TPE.", "status": "Conflicting"},
    ]


def _build_case2() -> DisputeCase:
    return DisputeCase(
        id="DISP-003", dispute_type="route_deviation",
        title="Route deviation overcharge — Jurong East to Changi Airport",
        status="Investigating",
        description="Rider claims driver intentionally took a longer route to inflate the fare.",
        rider={"name": "Priya Kumar", "id": "R-3421"},
        driver={"name": "Tan Ah Seng", "id": "D-4102"},
        trip={"trip_id": "TRIP-2026-09951", "pickup": "Jurong East MRT", "destination": "Changi Airport T3",
              "booked_at": "13 Sep 2026, 14:02", "distance": "Expected 18.2 km / Actual 26.8 km",
              "duration": "Expected 22 min / Actual 35 min"},
        fare={"currency": "SGD", "quoted": 22.5, "actual": 34.2, "difference": 11.7},
        rider_complaint="I take this route every week and it's always $22.50 via PIE. This driver took the TPE which added 8 km and $12 to my fare. He said 'GPS told me' but my own Google Maps showed PIE was clear. I think he did it on purpose to charge more.",
        driver_response="The GPS navigation rerouted me to TPE due to a traffic alert. I always follow the GPS. The longer route was not my choice. The fare is calculated by the system, not me.",
        metadata={"submitted_at": "13 Sep 2026, 14:50", "last_updated": "13 Sep 2026, 16:00",
                  "priority": "High", "policy_version": "2026.09"},
        timeline=_case2_timeline(), evidence=_case2_evidence(),
        rider_case={"party": "Rider", "summary": "Rider alleges the driver deliberately took a longer route to inflate the fare.",
                    "claims": [{"id": "C-R-01", "claim": "Driver deviated from the expected route without justification.",
                                "evidence_ids": ["E03", "E05"], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver claims GPS rerouted due to traffic, no intentional deviation.",
                     "claims": [{"id": "C-D-01", "claim": "GPS navigation suggested TPE due to traffic on PIE.",
                                 "evidence_ids": [], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Pending"}]},
        activity=_activity("Investigating"),
        policy_result={"policy_id": "ROUTE_POLICY_V2", "name": "Route deviation policy",
                       "rule_summary": "Deviations must be justified by traffic evidence; unexplained distance is refunded.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(route_deviation=RouteDeviationInput(
            expected_route_distance_km=18.2,
            actual_route_distance_km=26.8,
            expected_duration_minutes=22.0,
            actual_duration_minutes=35.0,
            conditions=[
                RouteConditionInput(type="traffic_diversion", explained_distance_km=0.0, evidence_ids=[]),
            ],
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Case 3 — No-show charge: Rider was at wrong pickup spot
# ─────────────────────────────────────────────────────────────────────
def _case3_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "19:15", "type": "Booking confirmed", "description": "Rider R-9912 booked trip TRIP-2026-09952 from Bugis Junction to Punggol. Scheduled pickup 19:30.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "19:28", "type": "Driver arrived", "description": "Driver arrived at Bugis Junction pickup point. GPS within 15m.", "evidence_ids": ["E02", "E03"]},
        {"id": "E03", "timestamp": "19:28", "type": "Wait timer started", "description": "Free 5-min wait started. Ends 19:33.", "evidence_ids": ["E03"]},
        {"id": "E04", "timestamp": "19:31", "type": "Rider messaged", "description": "Rider sent in-app message: 'I'm at the taxi stand, where are you?' Driver replied with pickup point details.", "evidence_ids": ["E04"]},
        {"id": "E05", "timestamp": "19:35", "type": "Wait timer expired", "description": "Free wait expired. Rider still not at correct pickup point.", "evidence_ids": ["E05"]},
        {"id": "E06", "timestamp": "19:37", "type": "Cancellation fee applied", "description": "$5.00 cancellation fee charged.", "evidence_ids": ["E06"], "severity": "conflict"},
    ]


def _case3_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "19:15", "source": "Trip service", "summary": "Booking confirmed. Pickup at Bugis Junction designated pickup point.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "19:28", "source": "Driver telemetry", "summary": "Driver GPS at Bugis Junction pickup zone. Within 15m of pin.", "status": "Verified"},
        {"id": "E03", "type": "Trip event", "timestamp": "19:28", "source": "App events", "summary": "driver_arrived. Wait timer started.", "status": "Verified"},
        {"id": "E04", "type": "Chat", "timestamp": "19:31", "source": "In-app messages", "summary": "Rider: 'I'm at the taxi stand.' Driver: 'I'm at the pickup zone near the entrance.' Rider was 200m away at taxi stand, not the designated pickup point.", "status": "Verified"},
        {"id": "E05", "type": "Policy", "timestamp": "19:35", "source": "Policy engine", "summary": "Free 5-min wait expired. Cancellation fee applicable.", "status": "Verified"},
        {"id": "E06", "type": "Trip event", "timestamp": "19:37", "source": "Cancellation service", "summary": "$5.00 cancellation fee applied to rider.", "status": "Verified"},
    ]


def _build_case3() -> DisputeCase:
    return DisputeCase(
        id="DISP-004", dispute_type="no_show_charge",
        title="No-show dispute — wrong pickup spot at Bugis Junction",
        status="Human Review",
        description="Rider waited at the taxi stand instead of the designated pickup zone and was charged a no-show fee.",
        rider={"name": "Sarah Chen", "id": "R-9912"},
        driver={"name": "Raj Kumar", "id": "D-5567"},
        trip={"trip_id": "TRIP-2026-09952", "pickup": "Bugis Junction", "destination": "Punggol MRT",
              "booked_at": "13 Sep 2026, 19:30", "distance": "Pickup radius 100 m (driver within 15m)",
              "duration": "Driver waited 9 min (free 5 min + 4 min over)"},
        fare={"currency": "SGD", "quoted": 0, "actual": 5.0, "difference": 5.0},
        rider_complaint="I was at Bugis Junction at the taxi stand waiting. The driver never came to find me. I messaged him and he said he was at some 'pickup zone' but there were no signs. I shouldn't be charged $5 for something that wasn't my fault — the app should've told me exactly where to go.",
        driver_response="I arrived at the designated pickup zone which is clearly marked near the entrance. The rider was at the taxi stand which is 200m away. I waited 9 minutes and messaged the rider with directions, but they didn't come. The no-show fee is correct per policy.",
        metadata={"submitted_at": "13 Sep 2026, 19:45", "last_updated": "13 Sep 2026, 21:00",
                  "priority": "Standard", "policy_version": "2026.09"},
        timeline=_case3_timeline(), evidence=_case3_evidence(),
        rider_case={"party": "Rider", "summary": "Rider was at the wrong location but believes the app didn't clearly indicate the pickup zone.",
                    "claims": [{"id": "C-R-01", "claim": "The pickup point was not clearly communicated and the no-show charge is unfair.",
                                "evidence_ids": ["E04"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver was at the correct designated pickup zone and waited beyond the free period.",
                     "claims": [
                         {"id": "C-D-01", "claim": "Driver arrived at the designated pickup zone within the pickup radius.",
                          "evidence_ids": ["E02", "E03"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Verified"},
                         {"id": "C-D-02", "claim": "Rider was 200m away at the taxi stand, not the pickup point.",
                          "evidence_ids": ["E04"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Verified"},
                     ]},
        activity=_activity("Human Review"),
        policy_result={"policy_id": "NO_SHOW_POLICY_V1", "name": "No-show policy",
                       "rule_summary": "Driver must be within pickup radius; rider must board within free wait + no-show window.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(no_show=NoShowInput(
            pickup_coordinates=GeoPoint(latitude=1.3014, longitude=103.8554),
            driver_arrival_coordinates=GeoPoint(latitude=1.3014, longitude=103.8554),
            driver_arrival_timestamp="19:28:00",
            cancellation_timestamp="19:37:00",
            rider_message_evidence_ids=["E04"],
            driver_message_evidence_ids=["E04"],
            cancellation_charge_amount=5.0,
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Case 4 — Route deviation: Detour for road closure (justified)
# ─────────────────────────────────────────────────────────────────────
def _case4_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "07:45", "type": "Booking confirmed", "description": "Rider R-4456 booked trip TRIP-2026-09953 from Ang Mo Kio Ave 6 to Raffles Place MRT. Quoted fare $15.80.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "07:50", "type": "Trip started", "description": "Trip started. Expected route via CTE, 11.5 km, 18 min.", "evidence_ids": ["E02"]},
        {"id": "E03", "timestamp": "07:58", "type": "Road closure detected", "description": "CTE exit to Bras Basah closed due to accident. GPS rerouted via MCE. +3.2 km detour.", "evidence_ids": ["E03", "E06"]},
        {"id": "E04", "timestamp": "08:12", "type": "Trip completed", "description": "Trip ended at Raffles Place. Final fare $20.10 — $4.30 above quote.", "evidence_ids": ["E04"]},
        {"id": "E05", "timestamp": "08:15", "type": "Rider disputed fare", "description": "Rider filed dispute claiming unnecessary detour.", "evidence_ids": ["E05"]},
        {"id": "E06", "timestamp": "07:58", "type": "Traffic alert", "description": "Traffic system confirms CTE accident causing closure. Rerouting was system-generated.", "evidence_ids": ["E06"]},
    ]


def _case4_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "07:45", "source": "Trip service", "summary": "Booking confirmed. Quoted $15.80 for Ang Mo Kio → Raffles Place.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "07:50", "source": "Route engine", "summary": "Expected route: CTE, 11.5 km, 18 min.", "status": "Verified"},
        {"id": "E03", "type": "GPS", "timestamp": "07:58", "source": "Driver telemetry", "summary": "GPS rerouted to MCE due to CTE closure. Actual 14.7 km, 22 min.", "status": "Verified"},
        {"id": "E04", "type": "Fare", "timestamp": "08:12", "source": "Fare engine", "summary": "Final fare $20.10. Quote $15.80. Difference $4.30.", "status": "Verified"},
        {"id": "E05", "type": "Chat", "timestamp": "08:10", "source": "In-app messages", "summary": "Rider asked driver why route changed. Driver: 'CTE got accident, GPS reroute me.'", "status": "Verified"},
        {"id": "E06", "type": "Traffic", "timestamp": "07:58", "source": "LTA traffic feed", "summary": "CTE accident confirmed. Lane closure between Exit 7A and Bras Basah. Duration: 07:55–08:30.", "status": "Verified"},
    ]


def _build_case4() -> DisputeCase:
    return DisputeCase(
        id="DISP-005", dispute_type="route_deviation",
        title="Route deviation with road closure — Ang Mo Kio to Raffles Place",
        status="Auto Resolved",
        description="Driver detoured due to a verified road closure; rider disputes the fare difference.",
        rider={"name": "Daniel Tan", "id": "R-4456"},
        driver={"name": "Wong Kah Lok", "id": "D-7733"},
        trip={"trip_id": "TRIP-2026-09953", "pickup": "Ang Mo Kio Ave 6", "destination": "Raffles Place MRT",
              "booked_at": "13 Sep 2026, 07:45", "distance": "Expected 11.5 km / Actual 14.7 km",
              "duration": "Expected 18 min / Actual 22 min"},
        fare={"currency": "SGD", "quoted": 15.8, "actual": 20.1, "difference": 4.3},
        rider_complaint="The driver took a longer route and I ended up paying $4.30 more than quoted. I don't care about some accident — the app should have stuck to the original route or at least warned me before charging extra.",
        driver_response="The GPS automatically rerouted me when CTE had an accident. I didn't choose this route. The traffic system confirmed the road closure. The extra distance was only 3.2 km and it was unavoidable.",
        metadata={"submitted_at": "13 Sep 2026, 08:20", "last_updated": "13 Sep 2026, 10:00",
                  "priority": "Standard", "policy_version": "2026.09"},
        timeline=_case4_timeline(), evidence=_case4_evidence(),
        rider_case={"party": "Rider", "summary": "Rider disputes the fare difference caused by a detour.",
                    "claims": [{"id": "C-R-01", "claim": "The fare exceeded the quoted amount without rider consent.",
                                "evidence_ids": ["E04", "E05"], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver followed GPS rerouting due to verified road closure.",
                     "claims": [{"id": "C-D-01", "claim": "CTE was closed due to an accident; GPS rerouted automatically.",
                                 "evidence_ids": ["E03", "E06"], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Verified"}]},
        activity=_activity("Auto Resolved"),
        policy_result={"policy_id": "ROUTE_POLICY_V2", "name": "Route deviation policy",
                       "rule_summary": "Justified deviations (road closures with traffic evidence) are not refunded.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(route_deviation=RouteDeviationInput(
            expected_route_distance_km=11.5,
            actual_route_distance_km=14.7,
            expected_duration_minutes=18.0,
            actual_duration_minutes=22.0,
            conditions=[
                RouteConditionInput(type="road_closure", explained_distance_km=3.2, evidence_ids=["E06"]),
            ],
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Case 5 — No-show: Driver was late, rider left
# ─────────────────────────────────────────────────────────────────────
def _case5_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "12:00", "type": "Booking confirmed", "description": "Rider R-6721 booked trip TRIP-2026-09954 from Clementi Mall to NTU. Scheduled pickup 12:15.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "12:22", "type": "Driver arriving", "description": "Driver GPS shows 800m from pickup, moving at 30 km/h. Driver 7 min late.", "evidence_ids": ["E02"], "severity": "attention"},
        {"id": "E03", "timestamp": "12:20", "type": "Rider messaged", "description": "Rider: 'Where are you? I've been waiting 5 min past pickup time.' No driver response for 3 min.", "evidence_ids": ["E03"]},
        {"id": "E04", "timestamp": "12:25", "type": "Driver arrived", "description": "Driver arrived 10 min after scheduled pickup. Wait timer NOT started (driver was late).", "evidence_ids": ["E04"]},
        {"id": "E05", "timestamp": "12:25", "type": "Rider cancelled", "description": "Rider cancelled the trip and booked another ride. Cancellation fee applied to rider.", "evidence_ids": ["E05"], "severity": "conflict"},
        {"id": "E06", "timestamp": "12:26", "type": "Cancellation fee applied", "description": "$5.00 cancellation fee charged to rider. Rider disputes — driver was late, not rider.", "evidence_ids": ["E06"], "severity": "conflict"},
    ]


def _case5_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "12:00", "source": "Trip service", "summary": "Booking confirmed. Pickup scheduled 12:15 at Clementi Mall.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "12:22", "source": "Driver telemetry", "summary": "Driver 800m from pickup at 12:22, 7 min after scheduled pickup. Speed 30 km/h.", "status": "Verified"},
        {"id": "E03", "type": "Chat", "timestamp": "12:20", "source": "In-app messages", "summary": "Rider messaged at 12:20 asking where driver was. Driver responded at 12:23: 'Coming, traffic.'", "status": "Verified"},
        {"id": "E04", "type": "GPS", "timestamp": "12:25", "source": "Driver telemetry", "summary": "Driver arrived at 12:25, 10 min late. No wait timer started because driver was past scheduled time.", "status": "Verified"},
        {"id": "E05", "type": "Trip event", "timestamp": "12:25", "source": "Cancellation service", "summary": "Rider cancelled trip at 12:25. System applied $5.00 cancellation fee.", "status": "Conflicting"},
        {"id": "E06", "type": "Fare", "timestamp": "12:26", "source": "Payment service", "summary": "$5.00 cancellation fee charged to rider e-wallet.", "status": "Verified"},
    ]


def _build_case5() -> DisputeCase:
    return DisputeCase(
        id="DISP-006", dispute_type="no_show_charge",
        title="No-show dispute — driver was late, rider cancelled — Clementi Mall",
        status="Investigating",
        description="Rider cancelled after the driver arrived 10 minutes late and was still charged a cancellation fee.",
        rider={"name": "Ahmad Faisal", "id": "R-6721"},
        driver={"name": "Lee Hock Seng", "id": "D-8841"},
        trip={"trip_id": "TRIP-2026-09954", "pickup": "Clementi Mall", "destination": "NTU North Spine",
              "booked_at": "13 Sep 2026, 12:15", "distance": "Pickup radius 100 m (driver arrived 800m away at 12:22)",
              "duration": "Driver arrived 10 min late at 12:25"},
        fare={"currency": "SGD", "quoted": 0, "actual": 5.0, "difference": 5.0},
        rider_complaint="The driver was 10 minutes late! I waited from 12:15 to 12:25 and he was still 800m away when I cancelled. Then they charged ME a $5 cancellation fee? That's ridiculous — HE was the no-show, not me. I had to book another ride and was late for class.",
        driver_response="There was heavy traffic on the AYE. I messaged the rider to say I was coming. I arrived at 12:25, only 10 min late. The rider cancelled right as I arrived. The system applied the fee automatically, I didn't request it.",
        metadata={"submitted_at": "13 Sep 2026, 12:30", "last_updated": "13 Sep 2026, 14:00",
                  "priority": "High", "policy_version": "2026.09"},
        timeline=_case5_timeline(), evidence=_case5_evidence(),
        rider_case={"party": "Rider", "summary": "Rider cancelled after driver was 10 min late; cancellation fee is unfair.",
                    "claims": [{"id": "C-R-01", "claim": "The driver arrived well past the scheduled pickup time; the no-show charge should not apply.",
                                "evidence_ids": ["E02", "E04"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver was delayed by traffic but arrived before rider cancelled.",
                     "claims": [{"id": "C-D-01", "claim": "Traffic caused delay; driver notified rider and arrived at 12:25.",
                                 "evidence_ids": ["E02", "E03", "E04"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Pending"}]},
        activity=_activity("Investigating"),
        policy_result={"policy_id": "NO_SHOW_POLICY_V1", "name": "No-show policy",
                       "rule_summary": "If driver is late, cancellation fee may not apply to rider.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(no_show=NoShowInput(
            pickup_coordinates=GeoPoint(latitude=1.3151, longitude=103.7642),
            driver_arrival_coordinates=GeoPoint(latitude=1.3151, longitude=103.7642),
            driver_arrival_timestamp="12:25:00",
            cancellation_timestamp="12:25:00",
            rider_message_evidence_ids=["E03"],
            driver_message_evidence_ids=["E03"],
            cancellation_charge_amount=5.0,
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Case 6 — Route deviation: Driver made unscheduled stop
# ─────────────────────────────────────────────────────────────────────
def _case6_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "22:30", "type": "Booking confirmed", "description": "Rider R-8834 booked trip TRIP-2026-09955 from Clarke Quay to Woodlands. Quoted fare $19.00.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "22:35", "type": "Trip started", "description": "Trip started. Expected route via CTE, 20.1 km, 25 min.", "evidence_ids": ["E02"]},
        {"id": "E03", "timestamp": "22:40", "type": "Unscheduled stop detected", "description": "GPS shows vehicle stopped at Yio Chu Kang Rd petrol station for 6 min.", "evidence_ids": ["E03"], "severity": "conflict"},
        {"id": "E04", "timestamp": "22:52", "type": "Trip resumed", "description": "Vehicle resumed route after 6 min stop. Final distance 21.8 km, 33 min.", "evidence_ids": ["E04"]},
        {"id": "E05", "timestamp": "22:55", "type": "Trip completed", "description": "Trip ended at Woodlands. Final fare $27.50 — $8.50 above quote.", "evidence_ids": ["E05"], "severity": "conflict"},
        {"id": "E06", "timestamp": "22:56", "type": "Rider disputed fare", "description": "Rider filed dispute: driver made personal stop during trip.", "evidence_ids": ["E06"]},
    ]


def _case6_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "22:30", "source": "Trip service", "summary": "Booking confirmed. Quoted $19.00 for Clarke Quay → Woodlands.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "22:35", "source": "Route engine", "summary": "Expected route: CTE, 20.1 km, 25 min.", "status": "Verified"},
        {"id": "E03", "type": "GPS", "timestamp": "22:40", "source": "Driver telemetry", "summary": "Vehicle stopped at Yio Chu Kang Rd petrol station for 6 minutes. Engine off. No rider-requested stop.", "status": "Verified"},
        {"id": "E04", "type": "GPS", "timestamp": "22:52", "source": "Driver telemetry", "summary": "Vehicle resumed at 22:46 after 6-min stop. Total actual: 21.8 km, 33 min.", "status": "Verified"},
        {"id": "E05", "type": "Fare", "timestamp": "22:55", "source": "Fare engine", "summary": "Final fare $27.50. Quote $19.00. Difference $8.50 (distance + idle time).", "status": "Verified"},
        {"id": "E06", "type": "Chat", "timestamp": "22:42", "source": "In-app messages", "summary": "Rider: 'Why are we stopping?' Driver: 'Just need to check something, 5 min.' No rider consent given.", "status": "Verified"},
    ]


def _build_case6() -> DisputeCase:
    return DisputeCase(
        id="DISP-007", dispute_type="route_deviation",
        title="Unscheduled stop overcharge — Clarke Quay to Woodlands",
        status="Investigating",
        description="Driver made a 6-minute personal stop at a petrol station during the trip, inflating the fare.",
        rider={"name": "Jasmine Lee", "id": "R-8834"},
        driver={"name": "Goh Chee Keong", "id": "D-2298"},
        trip={"trip_id": "TRIP-2026-09955", "pickup": "Clarke Quay", "destination": "Woodlands Causeway Point",
              "booked_at": "13 Sep 2026, 22:30", "distance": "Expected 20.1 km / Actual 21.8 km",
              "duration": "Expected 25 min / Actual 33 min (incl 6 min stop)"},
        fare={"currency": "SGD", "quoted": 19.0, "actual": 27.5, "difference": 8.5},
        rider_complaint="The driver literally stopped at a petrol station for 6 minutes in the middle of my ride! He said 'just need to check something' and I was too uncomfortable to argue. My fare went from $19 to $27.50 because of HIS personal stop. I want a refund for the extra time and distance.",
        driver_response="I needed to check my tyre pressure for safety. It only took a few minutes. I didn't think it would affect the fare much. The rider didn't object at the time.",
        metadata={"submitted_at": "13 Sep 2026, 23:00", "last_updated": "14 Sep 2026, 08:30",
                  "priority": "High", "policy_version": "2026.09"},
        timeline=_case6_timeline(), evidence=_case6_evidence(),
        rider_case={"party": "Rider", "summary": "Driver made an unscheduled personal stop without rider consent.",
                    "claims": [{"id": "C-R-01", "claim": "Driver stopped for personal reasons, inflating fare by $8.50.",
                                "evidence_ids": ["E03", "E04", "E05", "E06"], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver claims safety check was necessary.",
                     "claims": [{"id": "C-D-01", "claim": "Tyre pressure check was a safety necessity.",
                                 "evidence_ids": [], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Pending"}]},
        activity=_activity("Investigating"),
        policy_result={"policy_id": "ROUTE_POLICY_V2", "name": "Route deviation policy",
                       "rule_summary": "Unscheduled stops without rider consent must be refunded; safety checks require evidence.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(route_deviation=RouteDeviationInput(
            expected_route_distance_km=20.1,
            actual_route_distance_km=21.8,
            expected_duration_minutes=25.0,
            actual_duration_minutes=33.0,
            conditions=[
                RouteConditionInput(type="rider_requested_stop", explained_distance_km=0.0, evidence_ids=[]),
            ],
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Case 7 — Route deviation: Rider requested detour, driver complied
# ─────────────────────────────────────────────────────────────────────
def _case7_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "10:00", "type": "Booking confirmed", "description": "Rider R-2298 booked trip TRIP-2026-09956 from Bedok North to Gardens by the Bay. Quoted fare $14.20.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "10:05", "type": "Trip started", "description": "Trip started. Expected route via ECP, 9.8 km, 15 min.", "evidence_ids": ["E02"]},
        {"id": "E03", "timestamp": "10:08", "type": "Rider requested stop", "description": "Rider asked driver to stop at Kallang Wave Mall to pick up food. Driver complied.", "evidence_ids": ["E03"]},
        {"id": "E04", "timestamp": "10:18", "type": "Stop completed", "description": "Rider returned after 10 min. Trip resumed. Actual 13.5 km, 28 min.", "evidence_ids": ["E04"]},
        {"id": "E05", "timestamp": "10:25", "type": "Trip completed", "description": "Trip ended. Final fare $21.40 — $7.20 above quote.", "evidence_ids": ["E05"]},
        {"id": "E06", "timestamp": "10:30", "type": "Rider disputed fare", "description": "Rider claims the extra charge for their own requested stop is unfair.", "evidence_ids": ["E06"]},
    ]


def _case7_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "10:00", "source": "Trip service", "summary": "Booking confirmed. Quoted $14.20 for Bedok North → Gardens by the Bay.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "10:05", "source": "Route engine", "summary": "Expected route: ECP, 9.8 km, 15 min.", "status": "Verified"},
        {"id": "E03", "type": "Chat", "timestamp": "10:08", "source": "In-app messages", "summary": "Rider: 'Can we stop at Kallang Wave Mall? I need to pick up food.' Driver: 'Sure.'", "status": "Verified"},
        {"id": "E04", "type": "GPS", "timestamp": "10:18", "source": "Driver telemetry", "summary": "Vehicle stationary at Kallang Wave Mall for 10 min. Rider returned at 10:18.", "status": "Verified"},
        {"id": "E05", "type": "Fare", "timestamp": "10:25", "source": "Fare engine", "summary": "Final fare $21.40. Quote $14.20. Difference $7.20 (extra distance + 10 min idle).", "status": "Verified"},
        {"id": "E06", "type": "Chat", "timestamp": "10:30", "source": "In-app messages", "summary": "Rider filed dispute: 'I know I asked to stop but $7 extra is too much for a 10-min stop.'", "status": "Verified"},
    ]


def _build_case7() -> DisputeCase:
    return DisputeCase(
        id="DISP-008", dispute_type="route_deviation",
        title="Rider-requested stop dispute — Bedok to Gardens by the Bay",
        status="Auto Resolved",
        description="Rider requested a detour but disputes the fare increase caused by their own request.",
        rider={"name": "Nurul Aida", "id": "R-2298"},
        driver={"name": "Steven Wong", "id": "D-3340"},
        trip={"trip_id": "TRIP-2026-09956", "pickup": "Bedok North Ave 2", "destination": "Gardens by the Bay",
              "booked_at": "13 Sep 2026, 10:00", "distance": "Expected 9.8 km / Actual 13.5 km",
              "duration": "Expected 15 min / Actual 28 min (incl 10 min stop)"},
        fare={"currency": "SGD", "quoted": 14.2, "actual": 21.4, "difference": 7.2},
        rider_complaint="I asked the driver to make a quick stop but I didn't expect the fare to go up by $7.20. That's almost a 50% increase for a 10-minute stop. The fare should be capped or the app should have warned me.",
        driver_response="The rider explicitly asked me to stop at Kallang Wave Mall. I waited 10 minutes. The extra distance and idle time are directly caused by the rider's request. The fare is calculated by the system based on actual distance and time.",
        metadata={"submitted_at": "13 Sep 2026, 10:35", "last_updated": "13 Sep 2026, 12:00",
                  "priority": "Standard", "policy_version": "2026.09"},
        timeline=_case7_timeline(), evidence=_case7_evidence(),
        rider_case={"party": "Rider", "summary": "Rider admits requesting stop but disputes the fare increase.",
                    "claims": [{"id": "C-R-01", "claim": "The fare increase for a 10-min stop is excessive.",
                                "evidence_ids": ["E05", "E06"], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver complied with rider's explicit request; fare is system-calculated.",
                     "claims": [{"id": "C-D-01", "claim": "Rider requested the stop; extra fare reflects actual distance + time.",
                                 "evidence_ids": ["E03", "E04", "E05"], "policy_refs": ["ROUTE_POLICY_V2"], "status": "Verified"}]},
        activity=_activity("Auto Resolved"),
        policy_result={"policy_id": "ROUTE_POLICY_V2", "name": "Route deviation policy",
                       "rule_summary": "Rider-requested stops are charged to the rider; no refund applicable.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(route_deviation=RouteDeviationInput(
            expected_route_distance_km=9.8,
            actual_route_distance_km=13.5,
            expected_duration_minutes=15.0,
            actual_duration_minutes=28.0,
            conditions=[
                RouteConditionInput(type="rider_requested_stop", explained_distance_km=3.7, evidence_ids=["E03", "E04"]),
            ],
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Case 8 — No-show: Driver never showed, rider charged (edge case)
# ─────────────────────────────────────────────────────────────────────
def _case8_timeline() -> list[dict]:
    return [
        {"id": "E01", "timestamp": "06:00", "type": "Booking confirmed", "description": "Rider R-5567 booked trip TRIP-2026-09957 from Punggol to Changi Airport. Scheduled pickup 06:15. Flight at 07:30.", "evidence_ids": ["E01"]},
        {"id": "E02", "timestamp": "06:15", "type": "Pickup time reached", "description": "Scheduled pickup time. Driver GPS shows 3.2 km away, moving at 15 km/h.", "evidence_ids": ["E02"], "severity": "attention"},
        {"id": "E03", "timestamp": "06:18", "type": "Rider messaged", "description": "Rider: 'Are you coming? My flight is at 7:30.' Driver: 'Yes, 5 more min.'", "evidence_ids": ["E03"]},
        {"id": "E04", "timestamp": "06:25", "type": "Driver still en route", "description": "Driver GPS 1.8 km away, 10 min after scheduled pickup. Speed 20 km/h.", "evidence_ids": ["E04"], "severity": "attention"},
        {"id": "E05", "timestamp": "06:30", "type": "Rider cancelled", "description": "Rider cancelled and took a taxi instead. Cancellation fee applied.", "evidence_ids": ["E05"], "severity": "conflict"},
        {"id": "E06", "timestamp": "06:31", "type": "Cancellation fee applied", "description": "$5.00 cancellation fee charged to rider.", "evidence_ids": ["E06"], "severity": "conflict"},
    ]


def _case8_evidence() -> list[dict]:
    return [
        {"id": "E01", "type": "Trip event", "timestamp": "06:00", "source": "Trip service", "summary": "Booking confirmed. Pickup at 06:15. Flight at 07:30.", "status": "Verified"},
        {"id": "E02", "type": "GPS", "timestamp": "06:15", "source": "Driver telemetry", "summary": "At scheduled pickup time, driver was 3.2 km away, moving at 15 km/h. ETA 06:25.", "status": "Verified"},
        {"id": "E03", "type": "Chat", "timestamp": "06:18", "source": "In-app messages", "summary": "Rider asked about delay. Driver replied '5 more min' but was still 2 km away.", "status": "Verified"},
        {"id": "E04", "type": "GPS", "timestamp": "06:25", "source": "Driver telemetry", "summary": "Driver 1.8 km from pickup, 10 min past scheduled time. Speed 20 km/h.", "status": "Verified"},
        {"id": "E05", "type": "Trip event", "timestamp": "06:30", "source": "Cancellation service", "summary": "Rider cancelled at 06:30 (15 min after scheduled pickup). $5.00 fee applied.", "status": "Conflicting"},
        {"id": "E06", "type": "Fare", "timestamp": "06:31", "source": "Payment service", "summary": "$5.00 cancellation fee charged to rider.", "status": "Verified"},
    ]


def _build_case8() -> DisputeCase:
    return DisputeCase(
        id="DISP-009", dispute_type="no_show_charge",
        title="Driver no-show — Punggol to Changi Airport (morning flight)",
        status="Investigating",
        description="Driver was 3+ km away at scheduled pickup and never arrived. Rider cancelled and was still charged.",
        rider={"name": "Kevin Goh", "id": "R-5567"},
        driver={"name": "Hassan Ibrahim", "id": "D-6677"},
        trip={"trip_id": "TRIP-2026-09957", "pickup": "Punggol Waterway Terraces", "destination": "Changi Airport T2",
              "booked_at": "13 Sep 2026, 06:15", "distance": "Driver was 3.2 km away at pickup time",
              "duration": "Rider waited 15 min, driver never arrived"},
        fare={"currency": "SGD", "quoted": 0, "actual": 5.0, "difference": 5.0},
        rider_complaint="I booked a 6:15 AM pickup for my 7:30 flight. At 6:15 the driver was 3 km away! He kept saying '5 more minutes' but never showed. I had to cancel at 6:30 and grab a taxi. I almost missed my flight. AND they charged me $5 for cancelling? The driver was the no-show, not me!",
        driver_response="I was coming from Sengkang and there was morning traffic. I communicated with the rider and was on my way. The rider cancelled before I arrived. I lost a fare too. The cancellation fee is automatic.",
        metadata={"submitted_at": "13 Sep 2026, 06:35", "last_updated": "13 Sep 2026, 09:00",
                  "priority": "High", "policy_version": "2026.09"},
        timeline=_case8_timeline(), evidence=_case8_evidence(),
        rider_case={"party": "Rider", "summary": "Driver was far away at pickup time and never arrived; fee is unjust.",
                    "claims": [{"id": "C-R-01", "claim": "The driver was not at the pickup location at the scheduled time; the no-show charge should be reversed.",
                                "evidence_ids": ["E02", "E04", "E05"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Pending"}]},
        driver_case={"party": "Driver", "summary": "Driver was en route and communicating; cancellation was rider's choice.",
                     "claims": [{"id": "C-D-01", "claim": "Driver was on the way and informed rider of delay.",
                                 "evidence_ids": ["E03"], "policy_refs": ["NO_SHOW_POLICY_V1"], "status": "Pending"}]},
        activity=_activity("Investigating"),
        policy_result={"policy_id": "NO_SHOW_POLICY_V1", "name": "No-show policy",
                       "rule_summary": "Driver must be within pickup radius at scheduled time or no-show fee does not apply to rider.", "outcome": "PENDING"},
        confidence={"evidence_completeness": 0, "contradictory_evidence": 0, "policy_clarity": 0,
                    "missing_information": 0, "advocate_disagreement": 0, "overall": 0},
        resolution={"ruling": "Pending deterministic analysis", "recommended_action": "ANALYSE_CASE",
                    "refund_amount": 0, "currency": "SGD", "accepted_claim_ids": [], "rejected_claim_ids": [],
                    "mode": "HUMAN_REVIEW", "explanation": "Awaiting deterministic analysis.",
                    "counterfactual_explanation": "Deterministic policy evaluation will provide the recommendation."},
        human_review_summary=None,
        analysis_input=AnalysisInput(no_show=NoShowInput(
            pickup_coordinates=GeoPoint(latitude=1.3976, longitude=103.8225),
            driver_arrival_coordinates=GeoPoint(latitude=1.3976, longitude=103.8225),
            driver_arrival_timestamp="06:30:00",
            cancellation_timestamp="06:30:00",
            rider_message_evidence_ids=["E03"],
            driver_message_evidence_ids=["E03"],
            cancellation_charge_amount=5.0,
        )),
    )


# ─────────────────────────────────────────────────────────────────────
# Build the full list
# ─────────────────────────────────────────────────────────────────────
MOCK_CASES: list[DisputeCase] = [
    _build_case1(),
    _build_case2(),
    _build_case3(),
    _build_case4(),
    _build_case5(),
    _build_case6(),
    _build_case7(),
    _build_case8(),
]
