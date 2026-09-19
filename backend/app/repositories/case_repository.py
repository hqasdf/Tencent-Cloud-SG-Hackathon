from __future__ import annotations

from abc import ABC, abstractmethod

from app.data.cases import MOCK_CASES
from app.models.case import DisputeCase


class CaseRepository(ABC):
    @abstractmethod
    def list_cases(self) -> list[DisputeCase]: ...

    @abstractmethod
    def get_case(self, case_id: str) -> DisputeCase | None: ...


class MockCaseRepository(CaseRepository):
    def __init__(self, cases: list[DisputeCase] | None = None) -> None:
        self._cases = cases or MOCK_CASES

    def list_cases(self) -> list[DisputeCase]:
        return list(self._cases)

    def get_case(self, case_id: str) -> DisputeCase | None:
        return next((case for case in self._cases if case.id == case_id), None)
