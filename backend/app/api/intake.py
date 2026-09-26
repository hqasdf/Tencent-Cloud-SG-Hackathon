from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from app.data.trip_mapping import KNOWN_TRIP_IDS, get_source_case_id
from app.llm.hunyuan_client import HunyuanUnavailableError
from app.models.analysis import CaseAnalysisResponse
from app.models.intake import (
    CreateIntakeCaseRequest,
    IntakeCase,
    SendMessageRequest,
    SendMessageResponse,
)
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.conversation_service import ConversationService
from app.services.intake_analysis_service import IntakeAnalysisError, IntakeAnalysisService

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
        case, assistant_msg = _conversation_service.send_message(
            intake_case_id=intake_case_id,
            party=request.party,
            content=request.content,
        )
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except HunyuanUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM_UNAVAILABLE: {error.args[0] if error.args else str(error)}",
        ) from error
    return SendMessageResponse(case=case, assistant_message=assistant_msg)


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
