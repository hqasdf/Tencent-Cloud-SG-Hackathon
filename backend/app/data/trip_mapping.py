from __future__ import annotations

from app.data.cases import MOCK_CASES
from app.models.case import DisputeCase

# Maps trip IDs to their source case IDs.
TRIP_TO_CASE: dict[str, str] = {}
for _case in MOCK_CASES:
    TRIP_TO_CASE[_case.trip.trip_id] = _case.id

KNOWN_TRIP_IDS: list[str] = list(TRIP_TO_CASE.keys())


def get_source_case_id(trip_id: str) -> str | None:
    """Return the source case ID for a known trip, or None if unknown."""
    return TRIP_TO_CASE.get(trip_id)


def get_source_case(trip_id: str) -> DisputeCase | None:
    """Return the full source DisputeCase for a known trip, or None if unknown."""
    case_id = TRIP_TO_CASE.get(trip_id)
    if case_id is None:
        return None
    for case in MOCK_CASES:
        if case.id == case_id:
            return case
    return None
