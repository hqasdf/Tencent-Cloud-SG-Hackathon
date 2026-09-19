from __future__ import annotations

from datetime import datetime

from app.models.case import Evidence, TimelineEvent
from app.services.evidence_validation import EvidenceValidationResult, EvidenceValidationService, ValidationIssue


class CaseReplayService:
    def __init__(self, evidence_validation: EvidenceValidationService | None = None) -> None:
        self._evidence_validation = evidence_validation or EvidenceValidationService()

    def canonical_timeline(self, events: list[TimelineEvent], evidence: list[Evidence]) -> list[TimelineEvent]:
        timestamp_issues: list[ValidationIssue] = []
        parsed_events: list[tuple[datetime, TimelineEvent]] = []
        for event in events:
            try:
                parsed_events.append((datetime.strptime(event.timestamp, "%H:%M"), event))
            except ValueError:
                timestamp_issues.append(ValidationIssue("INVALID_TIMELINE_TIMESTAMP", f"Timeline event {event.id} has invalid timestamp {event.timestamp!r}; expected HH:MM.", event.id))
        reference_result = self._evidence_validation.validate_timeline_references(events, evidence)
        issues = timestamp_issues + reference_result.issues
        if issues:
            raise CaseReplayValidationError(issues)
        return [event for _, event in sorted(parsed_events, key=lambda pair: pair[0])]


class CaseReplayValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__("CaseReplay validation failed")
        self.issues = issues
