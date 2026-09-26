from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_success() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ryderesolve-case-api"}


def test_list_cases_returns_dashboard_summaries() -> None:
    response = client.get("/api/cases")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "DISP-002"
    assert "timeline" not in payload[0]
    assert payload[0]["disputeType"] == "no_show_charge"


def test_get_valid_case_returns_camel_case_contract_and_canonical_timeline() -> None:
    response = client.get("/api/cases/DISP-002")
    assert response.status_code == 200
    payload = response.json()
    assert payload["trip"]["tripId"] == "TRIP-2026-09945"
    assert [event["timestamp"] for event in payload["timeline"]] == sorted(event["timestamp"] for event in payload["timeline"])
    assert payload["rider"]["name"] == "Michael Wong"
    assert payload["driver"]["name"] == "Lim Wei Ming"


def test_no_show_analysis_endpoint_returns_upheld_charge() -> None:
    response = client.get("/api/cases/DISP-002/analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["driverWithinPickupRadius"] is True
    assert payload["analysis"]["waitingDurationSeconds"] == 480
    assert payload["analysis"]["cancellationChargeAmount"] == 5.0
    assert payload["resolutionRecommendation"]["recommendedAction"] == "UPHOLD_CANCELLATION_CHARGE"
    assert payload["resolutionMode"] == "AUTO_RESOLVE"


def test_unknown_analysis_case_returns_404() -> None:
    response = client.get("/api/cases/CASE-UNKNOWN/analysis")
    assert response.status_code == 404


def test_unknown_case_returns_404() -> None:
    response = client.get("/api/cases/CASE-UNKNOWN")
    assert response.status_code == 404
    assert response.json()["detail"] == "Case CASE-UNKNOWN was not found"
