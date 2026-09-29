from fastapi import APIRouter, Depends, HTTPException

from app.agents.config import AgentConfigurationError
from app.models.advocate import AdvocateRunResponse
from app.models.analysis import CaseAnalysisResponse
from app.models.case import CaseSummary, DisputeCase
from app.models.judge import CaseResolutionResponse
from app.models.replay import NO_REPLAY, ResolutionRunRequest
from app.replay.access import REPLAY_API_ENV, replay_api_enabled
from app.replay.service import ReplayRefused
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


@router.post("/cases/{case_id}/resolution/run", response_model=CaseResolutionResponse)
def run_case_resolution(
    case_id: str,
    request: ResolutionRunRequest | None = None,
    service: CaseService = Depends(get_case_service),
) -> CaseResolutionResponse:
    """Run the full Stage 6 pipeline for a case.

    Sequence: deterministic analysis -> Rider advocate -> Driver advocate ->
    claim verification -> one bounded rebuttal round -> rebuttal verification ->
    Judge -> Judge output validation -> deterministic remedy -> deterministic
    explanation and counterfactual.

    POST for the same reason as the advocate endpoint: it may trigger external
    model calls and incurs token cost. A complete live run costs five calls:
    Rider advocate, Driver advocate, Rider rebuttal, Driver rebuttal, Judge.

    The Judge is advisory. ``deterministicResolution`` in the response is the
    authoritative half and is computed without reference to the Judge, as are
    ``explanation`` and ``counterfactual``. If the Judge fails, the advocates,
    their verified claims, the verified rebuttals and the deterministic analysis
    are still returned intact.

    ``/advocates/run`` is deliberately left unchanged: it is the Stage 4
    contract, and each later stage extends the workflow rather than replacing it.

    Replay (Stage 6B) is opt-in and doubly gated: the body must name a mode other
    than ``NONE`` **and** the server must have ``REPLAY_API_ENABLED`` set.
    Without a body the behaviour is byte-for-byte what it was before replay
    existed. Capture is deliberately not available over HTTP — writing artefacts
    is a local, developer-side action, and the CLI is the way to do it.
    """
    mode = request.replay_mode if request is not None else NO_REPLAY
    if mode != NO_REPLAY and not replay_api_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                f"Replay mode {mode} is a developer facility and is disabled on this "
                f"server. Set {REPLAY_API_ENV}=1 to enable it, or use "
                "`python -m app.replay.cli replay`."
            ),
        )
    try:
        return service.get_resolution_run(case_id, replay_mode=mode)
    except ReplayRefused as error:
        # A refused replay is reported, never silently replaced by a live run.
        raise HTTPException(
            status_code=409,
            detail={
                "status": "REPLAY_INVALID",
                "reasonCodes": error.reason_codes,
                "message": (
                    "The stored artefact could not be trusted for the current case "
                    "state. No provider call was made. Re-capture the artefact and "
                    "try again."
                ),
            },
        ) from error
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
