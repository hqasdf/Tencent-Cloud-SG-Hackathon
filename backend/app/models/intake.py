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
    "RIDER_REBUTTAL",
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
    id: str | None = None
    key: str
    value: str
    stated_by: Party = Field(serialization_alias="statedBy", validation_alias="statedBy")
    source_message_id: str | None = Field(
        default=None, serialization_alias="sourceMessageId", validation_alias="sourceMessageId"
    )
    verification_status: Literal["ALLEGED"] = Field(
        default="ALLEGED",
        serialization_alias="verificationStatus",
        validation_alias="verificationStatus",
    )
    supersedes_fact_id: str | None = Field(
        default=None, serialization_alias="supersedesFactId", validation_alias="supersedesFactId"
    )
    active: bool = True


class IntakeFactCandidate(BaseModel):
    """Untrusted model extraction; identity and provenance are added by the server."""

    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=1000)
    operation: Literal["ADD", "CORRECT"] = "ADD"
    corrects_fact_id: str | None = None

    model_config = ConfigDict(extra="forbid")


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
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    trip_id: str = Field(min_length=1, max_length=100, serialization_alias="tripId", validation_alias="tripId")


class SendMessageRequest(IntakeModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    party: Party
    content: str = Field(min_length=1, max_length=4000)


class IntakeStructuredReply(BaseModel):
    """Strict, provider-neutral output contract for each intake turn."""

    assistant_message: str
    suggested_dispute_type: SuggestedDisputeType = Field(default="unknown")
    party_facts: list[IntakeFactCandidate] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    interview_complete: bool = Field(default=False)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class IntakeExecutionMetadata(IntakeModel):
    provider: str
    model: str | None = None
    duration_ms: int = Field(serialization_alias="durationMs", validation_alias="durationMs")
    input_tokens: int | None = Field(
        default=None, serialization_alias="inputTokens", validation_alias="inputTokens"
    )
    output_tokens: int | None = Field(
        default=None, serialization_alias="outputTokens", validation_alias="outputTokens"
    )
    total_tokens: int | None = Field(
        default=None, serialization_alias="totalTokens", validation_alias="totalTokens"
    )
    attempt_count: int = Field(
        default=1, serialization_alias="attemptCount", validation_alias="attemptCount"
    )


class SendMessageResponse(IntakeModel):
    case: IntakeCase
    assistant_message: IntakeMessage = Field(
        serialization_alias="assistantMessage", validation_alias="assistantMessage"
    )
    execution: IntakeExecutionMetadata | None = None
