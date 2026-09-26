"""End-to-end tests for the advocate API endpoint.

The endpoint is POST rather than GET because running advocates may trigger
external model calls. These tests exercise the real FastAPI app through
TestClient, so they cover routing, CORS-independent serialisation, and error
mapping together.
"""

import json

from fastapi.testclient import TestClient

from app.agents.config import AgentSettings
from app.agents.provider import AgentCompletionRequest, LlmCompletion
from app.agents.providers.mock import MockLlmProvider
from app.data.cases import MOCK_CASES
from app.main import app
from app.services.advocate_orchestrator import AdvocateOrchestratorService
from app.services.dispute_analysis import DisputeAnalysisService

client = TestClient(app)

ALL_CASES = ("CASE-2026-1041", "CASE-2026-1042", "CASE-2026-1043", "CASE-2026-1044")
ENDPOINT = "/api/cases/{case_id}/advocates/run"


def run(case_id: str):
    return client.post(ENDPOINT.format(case_id=case_id))


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_all_four_cases_run_both_advocates() -> None:
    for case_id in ALL_CASES:
        response = run(case_id)
        assert response.status_code == 200, f"{case_id} failed: {response.text}"
        payload = response.json()
        assert payload["caseId"] == case_id
        assert payload["rider"]["status"] == "COMPLETE"
        assert payload["driver"]["status"] == "COMPLETE"
        assert payload["rider"]["verifiedClaims"]
        assert payload["driver"]["verifiedClaims"]


def test_get_is_not_allowed() -> None:
    """POST is the declared verb; GET must not silently work."""
    response = client.get(ENDPOINT.format(case_id="CASE-2026-1041"))
    assert response.status_code == 405


def test_response_shape_matches_the_frontend_contract() -> None:
    payload = run("CASE-2026-1041").json()

    assert set(payload.keys()) == {
        "caseId",
        "disputeType",
        "rider",
        "driver",
        "agentRun",
        "verificationSummary",
        "pipeline",
    }

    for side in ("rider", "driver"):
        assert set(payload[side].keys()) == {
            "side",
            "status",
            "summary",
            "requestedOutcome",
            "contextAcknowledged",
            "verifiedClaims",
            "rejectedClaims",
            "warnings",
            "failureReason",
        }

    assert set(payload["agentRun"].keys()) == {
        "mode",
        "provider",
        "model",
        "promptVersion",
        "durationMs",
    }
    assert set(payload["verificationSummary"].keys()) == {
        "verifiedCount",
        "rejectedCount",
        "rejectionReasons",
    }


def test_judge_stage_is_reported_as_not_run() -> None:
    payload = run("CASE-2026-1041").json()
    stages = {item["stage"]: item["status"] for item in payload["pipeline"]}
    assert stages["JUDGE"] == "NOT_RUN"


def test_mock_mode_is_reported_honestly() -> None:
    payload = run("CASE-2026-1041").json()
    assert payload["agentRun"]["mode"] == "mock"
    assert payload["agentRun"]["provider"] == "mock"
    assert payload["agentRun"]["model"] is None


def test_verified_claims_only_reference_real_evidence() -> None:
    for case_id in ALL_CASES:
        payload = run(case_id).json()
        case = client.get(f"/api/cases/{case_id}").json()
        real_ids = {item["id"] for item in case["evidence"]}
        for side in ("rider", "driver"):
            for claim in payload[side]["verifiedClaims"]:
                assert set(claim["evidenceIds"]) <= real_ids, f"{case_id} verified a foreign ID"


def test_rejected_claim_ids_never_appear_in_verified_claims() -> None:
    """The core audit guarantee, asserted through the wire format."""
    for case_id in ALL_CASES:
        payload = run(case_id).json()
        for side in ("rider", "driver"):
            rejected_ids = {item["claimId"] for item in payload[side]["rejectedClaims"]}
            verified_ids = {item["claimId"] for item in payload[side]["verifiedClaims"]}
            assert not (rejected_ids & verified_ids)


def test_no_answer_fields_are_present_in_the_response() -> None:
    """Advocates argue; they do not decide. No judge verdict may appear."""
    payload = run("CASE-2026-1041").json()
    serialized = str(payload)
    for forbidden in (
        "resolutionRecommendation",
        "refundAmount",
        "escalationReasons",
        "confidenceScore",
        "verdict",
        "judgeDecision",
    ):
        assert forbidden not in serialized, f"response leaked {forbidden}"


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_unknown_case_returns_404() -> None:
    response = run("CASE-9999-0000")
    assert response.status_code == 404
    assert "CASE-9999-0000" in response.text


def test_real_mode_without_credentials_returns_503(monkeypatch) -> None:
    """Misconfiguration must be loud, not a silent fall back to mock."""
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "openai_compatible")
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("AGENT_BASE_URL", raising=False)

    response = run("CASE-2026-1041")
    assert response.status_code == 503
    # The error must not echo credentials (there are none) but must be actionable.
    assert "AGENT" in response.text or "provider" in response.text.lower()


# ---------------------------------------------------------------------------
# Existing endpoints still work
# ---------------------------------------------------------------------------

def test_existing_endpoints_are_unaffected() -> None:
    assert client.get("/api/cases").status_code == 200
    assert client.get("/api/cases/CASE-2026-1041").status_code == 200
    assert client.get("/api/cases/CASE-2026-1041/analysis").status_code == 200


def test_deterministic_analysis_is_identical_before_and_after_advocates_run() -> None:
    """The strongest regression guard: advocates must not touch the analysis."""
    before = client.get("/api/cases/CASE-2026-1041/analysis").json()
    run("CASE-2026-1041")
    after = client.get("/api/cases/CASE-2026-1041/analysis").json()
    assert before == after


def test_running_advocates_twice_is_idempotent_in_mock_mode() -> None:
    first = run("CASE-2026-1043").json()
    second = run("CASE-2026-1043").json()
    first["agentRun"]["durationMs"] = 0
    second["agentRun"]["durationMs"] = 0
    assert first == second


# ---------------------------------------------------------------------------
# The deliberate hallucination test, end to end
# ---------------------------------------------------------------------------

class HallucinatingProvider:
    """An advocate that cites evidence which does not exist.

    Injected at the orchestrator level so the full pipeline runs: the mock
    provider returns a poisoned claim, and the deterministic verifier must catch
    it before it can ever reach ``verifiedClaims``.
    """

    name = "hallucinating"

    def __init__(self) -> None:
        self._inner = MockLlmProvider()

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        payload = json.loads(self._inner.complete(request).raw_text)
        payload["claims"].append(
            {
                "claimId": "X1",
                "claim": "The driver took a deliberate detour past Marina Bay Sands.",
                "evidenceIds": ["E_DOES_NOT_EXIST"],
                "policyRefs": ["ROUTE_UNEXPLAINED_DEVIATION"],
                "reasoningSummary": "Fabricated.",
                "importance": "HIGH",
                "assertedFacts": [
                    {"fact": "UNEXPLAINED_DEVIATION_KM", "value": 0.8}
                ],
                "disputedEvidenceIds": [],
            }
        )
        return LlmCompletion(
            raw_text=json.dumps(payload),
            provider_name=self.name,
            model_name=None,
            duration_ms=0,
        )


def test_deliberate_hallucination_is_rejected_and_audited() -> None:
    """CODE catches the AI. The claim is rejected, visible, and never verified."""
    case = next(item for item in MOCK_CASES if item.id == "CASE-2026-1041")
    analysis = DisputeAnalysisService().analyze(case)

    result = AdvocateOrchestratorService(
        settings=AgentSettings(mode="mock", provider="mock"),
        provider=HallucinatingProvider(),
    ).run(case, analysis)

    for side_result in (result.rider, result.driver):
        # 1. The fabricated claim is NOT verified.
        assert all(
            claim.claim_id != "X1" for claim in side_result.verified_claims
        ), "a hallucinated claim reached verifiedClaims"

        # 2. It IS present in the rejection audit, exactly as produced.
        rejected = [item for item in side_result.rejected_claims if item.claim_id == "X1"]
        assert len(rejected) == 1
        rejection = rejected[0]
        assert rejection.reason == "EVIDENCE_ID_NOT_FOUND"
        assert "E_DOES_NOT_EXIST" in rejection.evidence_ids
        assert rejection.claim == "The driver took a deliberate detour past Marina Bay Sands."

    # 3. The summary reports the rejection reason so an operator can see it.
    assert "EVIDENCE_ID_NOT_FOUND" in result.verification_summary.rejection_reasons

    # 4. The honest claims the mock generated alongside the fabricated one
    #    are still verified — one bad claim does not poison the output.
    assert result.rider.verified_claims
