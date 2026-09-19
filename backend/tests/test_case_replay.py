import pytest

from app.models.case import Evidence, TimelineEvent
from app.services.case_replay import CaseReplayService, CaseReplayValidationError


def evidence() -> list[Evidence]:
    return [Evidence(id="E01", type="GPS", timestamp="09:00", source="telemetry", summary="GPS record", status="Verified")]


def test_case_replay_orders_events_chronologically() -> None:
    events = [
        TimelineEvent(id="T02", timestamp="09:20", type="Completed", description="Trip completed", evidence_ids=["E01"]),
        TimelineEvent(id="T01", timestamp="09:02", type="Started", description="Trip started", evidence_ids=["E01"]),
    ]
    timeline = CaseReplayService().canonical_timeline(events, evidence())
    assert [event.id for event in timeline] == ["T01", "T02"]


def test_case_replay_rejects_invalid_timestamp() -> None:
    events = [TimelineEvent(id="T01", timestamp="9am", type="Started", description="Trip started", evidence_ids=["E01"])]
    with pytest.raises(CaseReplayValidationError) as error:
        CaseReplayService().canonical_timeline(events, evidence())
    assert error.value.issues[0].code == "INVALID_TIMELINE_TIMESTAMP"


def test_case_replay_rejects_missing_evidence_reference() -> None:
    events = [TimelineEvent(id="T01", timestamp="09:01", type="Started", description="Trip started", evidence_ids=["E404"])]
    with pytest.raises(CaseReplayValidationError) as error:
        CaseReplayService().canonical_timeline(events, evidence())
    assert error.value.issues[0].code == "MISSING_TIMELINE_EVIDENCE"
