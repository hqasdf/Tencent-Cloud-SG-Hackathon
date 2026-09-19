from fastapi import APIRouter, Depends, HTTPException

from app.models.analysis import CaseAnalysisResponse
from app.models.case import CaseSummary, DisputeCase
from app.repositories.case_repository import MockCaseRepository
from app.services.case_replay import CaseReplayValidationError
from app.services.case_service import CaseNotFoundError, CaseService

router = APIRouter(prefix="/api", tags=["cases"])


def get_case_service() -> CaseService:
    return CaseService(MockCaseRepository())


@router.get("/cases", response_model=list[CaseSummary])
def list_cases(service: CaseService = Depends(get_case_service)) -> list[CaseSummary]:
    return service.list_case_summaries()


@router.get("/cases/{case_id}/analysis", response_model=CaseAnalysisResponse)
def get_case_analysis(case_id: str, service: CaseService = Depends(get_case_service)) -> CaseAnalysisResponse:
    try:
        return service.get_analysis(case_id)
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Case {error.args[0]} was not found") from error
    except CaseReplayValidationError as error:
        raise HTTPException(status_code=422, detail={"message": "CaseReplay validation failed", "issues": [issue.__dict__ for issue in error.issues]}) from error


@router.get("/cases/{case_id}", response_model=DisputeCase)
def get_case(case_id: str, service: CaseService = Depends(get_case_service)) -> DisputeCase:
    try:
        return service.get_case(case_id)
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Case {error.args[0]} was not found") from error
    except CaseReplayValidationError as error:
        raise HTTPException(status_code=422, detail={"message": "CaseReplay validation failed", "issues": [issue.__dict__ for issue in error.issues]}) from error
