from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from app.data.trip_mapping import KNOWN_TRIP_IDS, get_source_case, get_source_case_by_case_id, get_source_case_id
from app.models.analysis import CaseAnalysisResponse
from app.models.intake import (
    CreateIntakeCaseRequest,
    IntakeCase,
    SendMessageRequest,
    SendMessageResponse,
)
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.conversation_service import ConversationService, IntakeTransitionError
from app.services.intake_analysis_service import IntakeAnalysisError, IntakeAnalysisService
from app.services.intake_provider import IntakeProviderError

router = APIRouter(prefix="/api/intake", tags=["intake"])

# Shared repository instance — SQLite DB persists across requests
_repository = IntakeCaseRepository()
_conversation_service = ConversationService(_repository)
_analysis_service = IntakeAnalysisService(_repository)


@router.get("/trips", response_model=list[str])
def list_known_trips() -> list[str]:
    """Return the list of known trip IDs available for intake."""
    return KNOWN_TRIP_IDS


@router.post("/cases", response_model=IntakeCase, status_code=status.HTTP_201_CREATED)
def create_intake_case(request: CreateIntakeCaseRequest) -> IntakeCase:
    """Create an intake case from a selected known trip."""
    trip_id = request.trip_id
    source_case_id = get_source_case_id(trip_id)
    if source_case_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown trip ID: {trip_id}. Known trips: {', '.join(KNOWN_TRIP_IDS)}",
        )
    return _repository.create_case(source_case_id=source_case_id, trip_id=trip_id)


@router.post("/cases/{intake_case_id}/messages", response_model=SendMessageResponse)
def send_message(intake_case_id: str, request: SendMessageRequest) -> SendMessageResponse:
    """Add one party message and run one LLM interview turn."""
    try:
        case, assistant_msg, execution = _conversation_service.send_message(
            intake_case_id=intake_case_id,
            party=request.party,
            content=request.content,
        )
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except IntakeTransitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INTAKE_INVALID_REQUEST", "message": str(error)},
        ) from error
    except IntakeProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error
    return SendMessageResponse(case=case, assistant_message=assistant_msg, execution=execution)


@router.get("/cases/{intake_case_id}", response_model=IntakeCase)
def get_intake_case(intake_case_id: str) -> IntakeCase:
    """Return the saved case and both chat histories."""
    case = _repository.get_case(intake_case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Intake case {intake_case_id} not found",
        )
    return case


@router.get("/by-source/{source_case_id}", response_model=IntakeCase)
def get_intake_by_source(source_case_id: str) -> IntakeCase:
    """Return the most recent intake case for a source dispute case ID.

    Returns 404 if no intake case has been started for this source case.
    """
    case = _repository.get_case_by_source(source_case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No intake case found for source case {source_case_id}",
        )
    return case


@router.post("/by-source/{source_case_id}", response_model=IntakeCase, status_code=status.HTTP_201_CREATED)
def create_intake_by_source(source_case_id: str) -> IntakeCase:
    """Create (or return existing) intake case for a source dispute case ID.

    This is used by the case workspace to start AI intake interviews directly
    from a dispute case, without needing a trip ID.
    """
    # Look up the trip ID from the source case
    source = get_source_case_by_case_id(source_case_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source case {source_case_id} not found",
        )

    # Return existing intake case if one already exists
    existing = _repository.get_case_by_source(source_case_id)
    if existing is not None:
        return existing

    trip_id = source.trip.trip_id
    return _repository.create_case(source_case_id=source_case_id, trip_id=trip_id)


@router.post("/cases/{intake_case_id}/skip-rebuttal", response_model=IntakeCase)
def skip_rebuttal(intake_case_id: str) -> IntakeCase:
    """Allow the rider to skip the rebuttal phase and proceed to analysis."""
    try:
        return _conversation_service.skip_rebuttal(intake_case_id)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except IntakeTransitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": str(error)},
        ) from error


@router.post("/cases/{intake_case_id}/analyse", response_model=CaseAnalysisResponse)
def analyse_intake_case(intake_case_id: str) -> CaseAnalysisResponse:
    """Run deterministic analysis against the selected source case.

    Only works once both party states are complete.
    """
    try:
        return _analysis_service.analyse(intake_case_id)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except IntakeAnalysisError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
