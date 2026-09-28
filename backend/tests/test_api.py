from fastapi.testclient import TestClient

from app.data.cases import MOCK_CASES
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

    # The dashboard must expose exactly the registered fixtures. Comparing against
    # the fixture registry rather than a hardcoded count keeps this test honest
    # when a case is added or removed.
    assert {item["id"] for item in payload} == {case.id for case in MOCK_CASES}

    # Summaries are trimmed: the list view must not carry a full timeline, and
    # every row must declare a dispute type the UI knows how to render.
    for item in payload:
        assert "timeline" not in item
        assert item["disputeType"] in ("route_deviation", "no_show_charge")

    # A representative case carries the fields the dashboard renders.
    summary = next(item for item in payload if item["id"] == "DISP-002")
    assert summary["disputeType"] == "no_show_charge"
    assert summary["rider"]["name"]
    assert summary["driver"]["name"]


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
