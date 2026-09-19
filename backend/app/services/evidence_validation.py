from __future__ import annotations

from dataclasses import dataclass

from app.models.case import AdvocateCase, Evidence, TimelineEvent


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    reference: str


@dataclass(frozen=True)
class EvidenceValidationResult:
    valid: bool
    issues: list[ValidationIssue]


class EvidenceValidationService:
    def validate_evidence(self, evidence: list[Evidence]) -> EvidenceValidationResult:
        seen: set[str] = set()
        issues: list[ValidationIssue] = []
        for item in evidence:
            if item.id in seen:
                issues.append(ValidationIssue("DUPLICATE_EVIDENCE_ID", f"Evidence ID {item.id} appears more than once.", item.id))
            seen.add(item.id)
        return EvidenceValidationResult(not issues, issues)

    def validate_timeline_references(self, events: list[TimelineEvent], evidence: list[Evidence]) -> EvidenceValidationResult:
        evidence_ids = {item.id for item in evidence}
        issues = [
            ValidationIssue("MISSING_TIMELINE_EVIDENCE", f"Timeline event {event.id} references unknown evidence ID {evidence_id}.", event.id)
            for event in events for evidence_id in event.evidence_ids if evidence_id not in evidence_ids
        ]
        return EvidenceValidationResult(not issues, issues)

    def validate_advocate_references(self, advocate_cases: list[AdvocateCase], evidence: list[Evidence]) -> EvidenceValidationResult:
        evidence_ids = {item.id for item in evidence}
        issues = [
            ValidationIssue("INVALID_CLAIM_EVIDENCE", f"Claim {claim.id} references unknown evidence ID {evidence_id}.", claim.id)
            for case in advocate_cases for claim in case.claims if claim.status != "Rejected"
            for evidence_id in claim.evidence_ids if evidence_id not in evidence_ids
        ]
        return EvidenceValidationResult(not issues, issues)
