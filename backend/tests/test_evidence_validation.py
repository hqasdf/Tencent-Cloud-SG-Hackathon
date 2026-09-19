from app.models.case import AdvocateCase, Evidence
from app.services.evidence_validation import EvidenceValidationService


def valid_evidence() -> list[Evidence]:
    return [Evidence(id="E01", type="GPS", timestamp="09:00", source="telemetry", summary="GPS record", status="Verified")]


def test_valid_evidence_references_pass() -> None:
    advocate = AdvocateCase(party="Rider", summary="Evidence-backed case", claims=[{"id": "C01", "claim": "GPS supports the rider case", "evidence_ids": ["E01"], "policy_refs": ["P01"], "status": "Verified"}])
    result = EvidenceValidationService().validate_advocate_references([advocate], valid_evidence())
    assert result.valid is True
    assert result.issues == []


def test_nonexistent_evidence_id_is_detected() -> None:
    advocate = AdvocateCase(party="Rider", summary="Invalid reference", claims=[{"id": "C01", "claim": "Unknown evidence", "evidence_ids": ["E404"], "policy_refs": ["P01"], "status": "Verified"}])
    result = EvidenceValidationService().validate_advocate_references([advocate], valid_evidence())
    assert result.valid is False
    assert result.issues[0].code == "INVALID_CLAIM_EVIDENCE"


def test_duplicate_evidence_ids_are_detected() -> None:
    duplicate = Evidence(id="E01", type="GPS", timestamp="09:01", source="telemetry", summary="Duplicate GPS record", status="Verified")
    result = EvidenceValidationService().validate_evidence(valid_evidence() + [duplicate])
    assert result.valid is False
    assert result.issues[0].code == "DUPLICATE_EVIDENCE_ID"


def test_rejected_mock_claim_is_not_promoted_to_active_validation() -> None:
    advocate = AdvocateCase(party="Driver", summary="Rejected claim", claims=[{"id": "C02", "claim": "Known-invalid claim", "evidence_ids": ["E404"], "policy_refs": ["P01"], "status": "Rejected", "reason": "Unknown evidence ID"}])
    result = EvidenceValidationService().validate_advocate_references([advocate], valid_evidence())
    assert result.valid is True
