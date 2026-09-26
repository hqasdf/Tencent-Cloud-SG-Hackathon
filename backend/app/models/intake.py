from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntakeModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda value: value)


Party = Literal["rider", "driver"]
Sender = Literal["user", "assistant"]
IntakeLifecycle = Literal[
    "RIDER_INTERVIEW",
    "DRIVER_INTERVIEW",
    "READY_FOR_ANALYSIS",
    "ANALYSING",
    "AUTO_RESOLVED",
    "HUMAN_REVIEW",
]
SuggestedDisputeType = Literal["route_deviation", "no_show_charge", "unknown"]


class IntakeMessage(IntakeModel):
    id: str
    intake_case_id: str = Field(serialization_alias="intakeCaseId", validation_alias="intakeCaseId")
    party: Party
    sender: Sender
    content: str
    timestamp: str


class PartyFact(IntakeModel):
    key: str
    value: str
    stated_by: Party = Field(serialization_alias="statedBy", validation_alias="statedBy")


class InterviewState(IntakeModel):
    intake_case_id: str = Field(serialization_alias="intakeCaseId", validation_alias="intakeCaseId")
    party: Party
    facts: list[PartyFact] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    suggested_dispute_type: SuggestedDisputeType = Field(
        default="unknown", serialization_alias="suggestedDisputeType", validation_alias="suggestedDisputeType"
    )
    interview_complete: bool = Field(
        default=False, serialization_alias="interviewComplete", validation_alias="interviewComplete"
    )
    updated_at: str = Field(serialization_alias="updatedAt", validation_alias="updatedAt")


class IntakeCase(IntakeModel):
    id: str
    source_case_id: str = Field(serialization_alias="sourceCaseId", validation_alias="sourceCaseId")
    trip_id: str = Field(serialization_alias="tripId", validation_alias="tripId")
    lifecycle: IntakeLifecycle
    detected_dispute_type: SuggestedDisputeType = Field(
        default="unknown", serialization_alias="detectedDisputeType", validation_alias="detectedDisputeType"
    )
    created_at: str = Field(serialization_alias="createdAt", validation_alias="createdAt")
    updated_at: str = Field(serialization_alias="updatedAt", validation_alias="updatedAt")
    final_analysis: dict | None = Field(
        default=None, serialization_alias="finalAnalysis", validation_alias="finalAnalysis"
    )
    messages: list[IntakeMessage] = Field(default_factory=list)
    rider_state: InterviewState | None = Field(
        default=None, serialization_alias="riderState", validation_alias="riderState"
    )
    driver_state: InterviewState | None = Field(
        default=None, serialization_alias="driverState", validation_alias="driverState"
    )


class CreateIntakeCaseRequest(IntakeModel):
    trip_id: str = Field(serialization_alias="tripId", validation_alias="tripId")


class SendMessageRequest(IntakeModel):
    party: Party
    content: str


class HunyuanStructuredReply(BaseModel):
    """Strict JSON schema that Hunyuan must return for each interview turn."""

    assistant_message: str
    suggested_dispute_type: SuggestedDisputeType = Field(default="unknown")
    party_facts: list[PartyFact] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    interview_complete: bool = Field(default=False)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SendMessageResponse(IntakeModel):
    case: IntakeCase
    assistant_message: IntakeMessage = Field(
        serialization_alias="assistantMessage", validation_alias="assistantMessage"
    )
