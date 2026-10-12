"""Offline regressions for final resource and redundant-work cleanup."""
import sqlite3

import pytest

from app.models.case import CaseSummary
from app.repositories.case_repository import MockCaseRepository
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.case_service import CaseService


@pytest.mark.parametrize("rollback", [False, True])
def test_intake_connection_closes_after_commit_or_rollback(tmp_path, rollback):
    repository = IntakeCaseRepository(tmp_path / "intake.db")
    case = repository.create_case(source_case_id="DISP-005", trip_id="SYNTHETIC")
    class AbortTransaction(Exception):
        pass
    try:
        with repository._conn() as connection:
            connection.execute("UPDATE intake_cases SET lifecycle = ? WHERE id = ?",
                               ("DRIVER_INTERVIEW", case.id))
            if rollback:
                raise AbortTransaction
    except AbortTransaction:
        pass
    assert repository.get_case(case.id).lifecycle == ("RIDER_INTERVIEW" if rollback else "DRIVER_INTERVIEW")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_case_summary_validates_each_case_once_without_changing_display(monkeypatch):
    repository = MockCaseRepository()
    service = CaseService(repository)
    validated_ids = []
    original = service._validated_case
    def counted(case):
        validated_ids.append(case.id)
        return original(case)
    monkeypatch.setattr(service, "_validated_case", counted)
    summaries = service.list_case_summaries()
    assert validated_ids == [case.id for case in repository.list_cases()]
    assert summaries == [CaseSummary.model_validate(service.get_case(case.id).model_dump())
                         for case in repository.list_cases()]
