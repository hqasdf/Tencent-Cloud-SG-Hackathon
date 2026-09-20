from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_success() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "ryderesolve-case-api"
    # Agent configuration is reported without ever exposing a credential.
    assert payload["agents"]["mode"] == "mock"
    assert payload["agents"]["configured"] is True
    assert "api_key" not in str(payload["agents"]).lower()


def test_list_cases_returns_dashboard_summaries() -> None:
    response = client.get("/api/cases")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert payload[0]["id"] == "CASE-2026-1041"
    assert "timeline" not in payload[0]
    assert payload[0]["disputeType"] == "route_deviation"


def test_get_valid_case_returns_camel_case_contract_and_canonical_timeline() -> None:
    response = client.get("/api/cases/CASE-2026-1041")
    assert response.status_code == 200
    payload = response.json()
    assert payload["trip"]["tripId"] == "TRP-1041"
    assert [event["timestamp"] for event in payload["timeline"]] == sorted(event["timestamp"] for event in payload["timeline"])
    assert payload["riderCase"]["claims"][0]["evidenceIds"] == ["E04", "E05"]


def test_route_analysis_endpoint_returns_deterministic_partial_refund() -> None:
    response = client.get("/api/cases/CASE-2026-1041/analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["distanceDifferenceKm"] == 1.3
    assert payload["resolutionRecommendation"]["recommendedAction"] == "PARTIAL_REFUND"
    assert payload["resolutionMode"] == "AUTO_RESOLVE"


def test_no_show_analysis_endpoint_returns_upheld_charge() -> None:
    response = client.get("/api/cases/CASE-2026-1043/analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["driverWithinPickupRadius"] is True
    assert payload["analysis"]["waitingDurationSeconds"] == 372
    assert payload["resolutionRecommendation"]["recommendedAction"] == "UPHOLD_CANCELLATION_CHARGE"


def test_human_review_analysis_result_is_deterministic() -> None:
    response = client.get("/api/cases/CASE-2026-1044/analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["resolutionMode"] == "HUMAN_REVIEW"
    assert "CONTRADICTORY_EVIDENCE" in payload["escalationReasons"]


def test_unknown_analysis_case_returns_404() -> None:
    response = client.get("/api/cases/CASE-UNKNOWN/analysis")
    assert response.status_code == 404


def test_unknown_case_returns_404() -> None:
    response = client.get("/api/cases/CASE-UNKNOWN")
    assert response.status_code == 404
    assert response.json()["detail"] == "Case CASE-UNKNOWN was not found"
