"""Tests for the AI-guided dispute intake API.

Covers:
- valid/unknown trip selection and source-case mapping
- persistence and chronological retrieval of rider and driver messages
- party isolation in prompt construction
- valid structured Hunyuan reply persistence
- malformed or unavailable Hunyuan response handling
- analysis rejection until both interviews are complete
- hand-off to the DISP-002 source case with expected deterministic outcome
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.llm.hunyuan_client import HunyuanResult
from app.main import app
from app.repositories.intake_repository import IntakeCaseRepository


@pytest.fixture()
def temp_db() -> Path:
    """Return a temporary DB path that is fresh for each test."""
    d = tempfile.mkdtemp(prefix="ryde_test_")
    return Path(d) / "test_intake.db"


@pytest.fixture()
def client(temp_db: Path):
    """Create a test client with a fresh SQLite DB and no real LLM key."""
    old_key = os.environ.pop("TENCENT_TOKENHUB_API_KEY", None)
    old_openai_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        from app.api import intake as intake_module

        repo = IntakeCaseRepository(db_path=temp_db)
        from app.services.conversation_service import ConversationService
        from app.services.intake_analysis_service import IntakeAnalysisService

        intake_module._repository = repo
        intake_module._conversation_service = ConversationService(repo)
        intake_module._analysis_service = IntakeAnalysisService(repo)

        with TestClient(app) as c:
            yield c
    finally:
        if old_key is not None:
            os.environ["TENCENT_TOKENHUB_API_KEY"] = old_key
        if old_openai_key is not None:
            os.environ["OPENAI_API_KEY"] = old_openai_key


# ---------------------------------------------------------------------------
# Trip selection and source-case mapping
# ---------------------------------------------------------------------------


def test_list_known_trips(client: TestClient):
    response = client.get("/api/intake/trips")
    assert response.status_code == 200
    trips = response.json()
    assert "TRIP-2026-09945" in trips
    assert len(trips) == 1


def test_create_intake_case_valid_trip(client: TestClient):
    response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    assert response.status_code == 201
    case = response.json()
    assert case["tripId"] == "TRIP-2026-09945"
    assert case["sourceCaseId"] == "DISP-002"
    assert case["lifecycle"] == "RIDER_INTERVIEW"
    assert case["detectedDisputeType"] == "unknown"
    assert case["riderState"] is not None
    assert case["driverState"] is not None
    assert case["riderState"]["interviewComplete"] is False
    assert case["driverState"]["interviewComplete"] is False
    assert case["messages"] == []


def test_create_intake_case_unknown_trip(client: TestClient):
    response = client.post("/api/intake/cases", json={"tripId": "TRP-9999"})
    assert response.status_code == 400
    assert "Unknown trip ID" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Persistence and chronological retrieval of messages
# ---------------------------------------------------------------------------


def test_get_intake_case_returns_saved_messages(client: TestClient, temp_db: Path):
    repo = IntakeCaseRepository(db_path=temp_db)
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")
    repo.add_message(
        intake_case_id=case.id, party="rider", sender="user", content="First message"
    )
    repo.add_message(
        intake_case_id=case.id, party="driver", sender="user", content="Driver message"
    )
    repo.add_message(
        intake_case_id=case.id, party="rider", sender="assistant", content="Second"
    )

    response = client.get(f"/api/intake/cases/{case.id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 3
    assert data["messages"][0]["content"] == "First message"
    assert data["messages"][1]["content"] == "Driver message"
    assert data["messages"][2]["content"] == "Second"


def test_get_nonexistent_intake_case(client: TestClient):
    response = client.get("/api/intake/cases/INTAKE-DOESNOTEXIST")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# LLM unavailable handling
# ---------------------------------------------------------------------------


def test_send_message_llm_unavailable_returns_error_and_saves_message(
    client: TestClient,
):
    create_response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    case_id = create_response.json()["id"]

    response = client.post(
        f"/api/intake/cases/{case_id}/messages",
        json={"party": "rider", "content": "The driver never showed up."},
    )
    assert response.status_code == 503
    assert "LLM_UNAVAILABLE" in response.json()["detail"]

    get_response = client.get(f"/api/intake/cases/{case_id}")
    messages = get_response.json()["messages"]
    assert any(
        m["content"] == "The driver never showed up." and m["party"] == "rider"
        for m in messages
    )


def test_send_message_blank_content_rejected(client: TestClient):
    create_response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    case_id = create_response.json()["id"]

    response = client.post(
        f"/api/intake/cases/{case_id}/messages",
        json={"party": "rider", "content": "   "},
    )
    assert response.status_code == 400


def test_send_message_to_nonexistent_case(client: TestClient):
    response = client.post(
        "/api/intake/cases/INTAKE-FAKE/messages",
        json={"party": "rider", "content": "Hello"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Mock LLM helpers
# ---------------------------------------------------------------------------


def _mock_reply(
    assistant_message: str = "I understand. Can you tell me more?",
    suggested_dispute_type: str = "no_show_charge",
    party_facts: list | None = None,
    missing_details: list | None = None,
    interview_complete: bool = False,
) -> str:
    return json.dumps(
        {
            "assistant_message": assistant_message,
            "suggested_dispute_type": suggested_dispute_type,
            "party_facts": party_facts or [],
            "missing_details": missing_details or ["exact arrival time"],
            "interview_complete": interview_complete,
        }
    )


def _mock_hunyuan(reply_content: str):
    mock = MagicMock()
    mock.is_available = True
    mock.chat.return_value = HunyuanResult(content=reply_content, ok=True)
    return mock


# ---------------------------------------------------------------------------
# Valid structured Hunyuan reply persistence (mocked LLM)
# ---------------------------------------------------------------------------


def test_valid_structured_reply_persists_facts(client: TestClient):
    create_response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    case_id = create_response.json()["id"]

    from app.api import intake as intake_module

    intake_module._conversation_service._hunyuan = _mock_hunyuan(
        _mock_reply(
            assistant_message="I see. Can you tell me when you arrived at the pickup?",
            party_facts=[
                {"key": "complaint", "value": "Driver never showed up", "stated_by": "rider"}
            ],
        )
    )

    response = client.post(
        f"/api/intake/cases/{case_id}/messages",
        json={"party": "rider", "content": "The driver never showed up."},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["assistantMessage"]["content"] == "I see. Can you tell me when you arrived at the pickup?"
    rider_state = data["case"]["riderState"]
    assert rider_state is not None
    assert len(rider_state["facts"]) == 1
    assert rider_state["facts"][0]["key"] == "complaint"
    assert rider_state["facts"][0]["statedBy"] == "rider"
    assert rider_state["suggestedDisputeType"] == "no_show_charge"
    assert rider_state["interviewComplete"] is False


def test_malformed_hunyuan_reply_gives_retry_and_no_extraction(client: TestClient):
    create_response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    case_id = create_response.json()["id"]

    from app.api import intake as intake_module

    intake_module._conversation_service._hunyuan = _mock_hunyuan(
        "This is not valid JSON at all."
    )

    response = client.post(
        f"/api/intake/cases/{case_id}/messages",
        json={"party": "rider", "content": "Something happened."},
    )

    assert response.status_code == 200
    data = response.json()
    assert "rephrase" in data["assistantMessage"]["content"].lower()
    rider_state = data["case"]["riderState"]
    assert len(rider_state["facts"]) == 0
    assert rider_state["interviewComplete"] is False


# ---------------------------------------------------------------------------
# Party isolation in prompt construction
# ---------------------------------------------------------------------------


def test_party_isolation_rider_messages_not_sent_to_driver(
    client: TestClient,
):
    create_response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    case_id = create_response.json()["id"]

    from app.api import intake as intake_module

    # Send a rider message
    intake_module._conversation_service._hunyuan = _mock_hunyuan(
        _mock_reply(
            assistant_message="Got it. What time did you arrive?",
            party_facts=[
                {"key": "no_show", "value": "Driver never showed", "stated_by": "rider"}
            ],
            interview_complete=True,
        )
    )
    client.post(
        f"/api/intake/cases/{case_id}/messages",
        json={"party": "rider", "content": "The driver never showed up."},
    )

    # Now send a driver message — capture messages sent to the LLM
    captured_messages = []

    def capture_chat(messages):
        captured_messages.extend(messages)
        return HunyuanResult(
            content=_mock_reply(
                assistant_message="I see. What time did you arrive at the pickup?",
                party_facts=[
                    {"key": "arrival", "value": "Arrived at 08:43", "stated_by": "driver"}
                ],
            ),
            ok=True,
        )

    mock_driver = MagicMock()
    mock_driver.is_available = True
    mock_driver.chat.side_effect = capture_chat
    intake_module._conversation_service._hunyuan = mock_driver

    client.post(
        f"/api/intake/cases/{case_id}/messages",
        json={"party": "driver", "content": "I arrived early and waited."},
    )

    # The system prompt should mention "driver"
    system_prompt = captured_messages[0].content if captured_messages else ""
    assert "driver" in system_prompt.lower()

    # Rider's private message content must NOT appear in driver's prompt
    user_messages = [m for m in captured_messages if m.role == "user"]
    for msg in user_messages:
        assert "never showed up" not in msg.content.lower(), (
            f"Rider's private message leaked to driver prompt: {msg.content}"
        )


# ---------------------------------------------------------------------------
# Analysis rejection until both interviews are complete
# ---------------------------------------------------------------------------


def test_analysis_rejected_before_both_interviews_complete(
    client: TestClient, temp_db: Path
):
    create_response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    case_id = create_response.json()["id"]

    repo = IntakeCaseRepository(db_path=temp_db)
    repo.update_state(
        intake_case_id=case_id,
        party="rider",
        facts=[],
        missing_details=[],
        suggested_dispute_type="no_show_charge",
        interview_complete=True,
    )

    response = client.post(f"/api/intake/cases/{case_id}/analyse")
    assert response.status_code == 409
    assert "Both rider and driver interviews must be complete" in response.json()["detail"]


def test_analysis_works_when_both_interviews_complete(
    client: TestClient, temp_db: Path
):
    create_response = client.post("/api/intake/cases", json={"tripId": "TRIP-2026-09945"})
    case_id = create_response.json()["id"]

    repo = IntakeCaseRepository(db_path=temp_db)
    repo.update_state(
        intake_case_id=case_id,
        party="rider",
        facts=[],
        missing_details=[],
        suggested_dispute_type="no_show_charge",
        interview_complete=True,
    )
    repo.update_state(
        intake_case_id=case_id,
        party="driver",
        facts=[],
        missing_details=[],
        suggested_dispute_type="no_show_charge",
        interview_complete=True,
    )

    response = client.post(f"/api/intake/cases/{case_id}/analyse")
    assert response.status_code == 200
    analysis = response.json()
    assert analysis["caseId"] == "DISP-002"
    assert analysis["disputeType"] == "no_show_charge"
    assert analysis["resolutionRecommendation"]["recommendedAction"] == "UPHOLD_CANCELLATION_CHARGE"
    assert analysis["resolutionMode"] == "AUTO_RESOLVE"
