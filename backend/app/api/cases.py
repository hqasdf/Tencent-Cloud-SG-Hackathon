from fastapi import APIRouter, Depends, HTTPException

from app.agents.config import AgentConfigurationError
from app.models.advocate import AdvocateRunResponse
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


@router.post("/cases/{case_id}/advocates/run", response_model=AdvocateRunResponse)
def run_case_advocates(
    case_id: str,
    service: CaseService = Depends(get_case_service),
) -> AdvocateRunResponse:
    """Run the Rider and Driver advocates for a case.

    POST rather than GET because running advocates may trigger external model
    calls, incur token cost and latency, and is intended to become a persisted,
    operator-triggered action.

    A provider failure degrades gracefully: the affected side reports FAILED and
    the deterministic case analysis is unaffected. It never returns canned mock
    output pretending to be a real model.
    """
    try:
        return service.get_advocate_run(case_id)
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Case {error.args[0]} was not found") from error
    except CaseReplayValidationError as error:
        raise HTTPException(status_code=422, detail={"message": "CaseReplay validation failed", "issues": [issue.__dict__ for issue in error.issues]}) from error
    except AgentConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/cases/{case_id}", response_model=DisputeCase)
def get_case(case_id: str, service: CaseService = Depends(get_case_service)) -> DisputeCase:
    try:
        return service.get_case(case_id)
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Case {error.args[0]} was not found") from error
    except CaseReplayValidationError as error:
        raise HTTPException(status_code=422, detail={"message": "CaseReplay validation failed", "issues": [issue.__dict__ for issue in error.issues]}) from error
