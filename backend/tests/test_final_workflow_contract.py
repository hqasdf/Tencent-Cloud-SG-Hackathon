"""The four-section UI's actual HTTP contracts, with isolated offline storage."""
import pytest
from fastapi.testclient import TestClient

from app.repositories.intake_repository import IntakeCaseRepository
from test_phase4_cases import EXPECTED


@pytest.mark.parametrize("case_id", EXPECTED)
def test_each_case_http_contract_preserves_all_roles_and_code_decision(case_id, tmp_path, monkeypatch):
    from app.main import app
    import app.api.intake as intake_api
    monkeypatch.setattr(intake_api, "_repository", IntakeCaseRepository(tmp_path / "intake.db"))
    monkeypatch.setenv("AGENT_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    with TestClient(app) as client:
        listing = client.get("/api/cases")
        assert listing.status_code == 200
        assert case_id in {c["id"] for c in listing.json()}
        case = client.get(f"/api/cases/{case_id}")
        evidence = client.get(f"/api/cases/{case_id}/analysis")
        assert case.status_code == evidence.status_code == 200
        assert case.json()["id"] == evidence.json()["caseId"] == case_id
        response = client.post(f"/api/cases/{case_id}/resolution/run")
    assert response.status_code == 200
    result = response.json()
    action, refund, mode = EXPECTED[case_id]
    assert result["caseId"] == case_id
    decision = result["deterministicResolution"]
    assert (decision["recommendedAction"], decision["refundAmount"], decision["resolutionMode"]) == (action, refund, mode)
    assert result["intakeContext"] is None  # Historical cases remain supported; no fake completed interview.
    assert result["replayMetadata"]["source"] == "MOCK_DEMO"
    assert all(side["status"] == "COMPLETE" for side in (
        result["rider"], result["driver"], result["rebuttals"]["rider"], result["rebuttals"]["driver"]))
    assert result["judge"]["status"] == ("PENDING_HUMAN_REVIEW" if mode == "HUMAN_REVIEW" else "COMPLETE")
    assert result["judge"]["validationIssues"] == []
    assert result["explanation"] and result["counterfactual"] and result["audit"]
