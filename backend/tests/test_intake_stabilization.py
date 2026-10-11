from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from app.agents.config import AgentSettings
from app.agents.provider import AgentCompletionRequest, LlmCompletion
from app.models.intake import IntakeExecutionMetadata, IntakeStructuredReply
from app.repositories.intake_repository import IntakeCaseRepository
from app.services.conversation_service import (
    MAX_PARTY_USER_MESSAGES,
    ConversationService,
    IntakeTransitionError,
)
from app.services.intake_provider import IntakeTurnResult, RegistryIntakeProvider


@dataclass
class QueueIntakeProvider:
    replies: list[IntakeStructuredReply]
    calls: list[dict] = field(default_factory=list)

    def complete_turn(self, **kwargs) -> IntakeTurnResult:
        self.calls.append(kwargs)
        return IntakeTurnResult(
            reply=self.replies.pop(0),
            execution=IntakeExecutionMetadata(
                provider="controlled", model="offline", durationMs=1, attemptCount=1
            ),
        )


@dataclass
class CapturingLlmProvider:
    raw_text: str
    name: str = "capturing"
    requests: list[AgentCompletionRequest] = field(default_factory=list)

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.requests.append(request)
        return LlmCompletion(
            raw_text=self.raw_text,
            provider_name=self.name,
            model_name="agent-model",
            duration_ms=2,
        )


def _reply(*, complete: bool = False, facts: list[dict] | None = None) -> IntakeStructuredReply:
    return IntakeStructuredReply.model_validate(
        {
            "assistant_message": "Please continue.",
            "suggested_dispute_type": "unknown",
            "party_facts": facts or [],
            "missing_details": [],
            "interview_complete": complete,
        }
    )


@pytest.fixture()
def repo(tmp_path) -> IntakeCaseRepository:
    return IntakeCaseRepository(tmp_path / "intake.db")


def test_wrong_party_is_rejected_before_message_or_provider_call(repo: IntakeCaseRepository):
    provider = QueueIntakeProvider([_reply()])
    service = ConversationService(repo, provider)
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")

    with pytest.raises(IntakeTransitionError) as raised:
        service.send_message(intake_case_id=case.id, party="driver", content="I waited.")

    assert raised.value.code == "INTAKE_WRONG_PARTY"
    assert provider.calls == []
    assert repo.get_messages(case.id) == []


def test_lifecycle_allows_only_rider_driver_then_rider_rebuttal(repo: IntakeCaseRepository):
    provider = QueueIntakeProvider([_reply(complete=True), _reply(complete=True), _reply()])
    service = ConversationService(repo, provider)
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")

    after_rider, _, _ = service.send_message(
        intake_case_id=case.id, party="rider", content="My account."
    )
    assert after_rider.lifecycle == "DRIVER_INTERVIEW"

    after_driver, _, _ = service.send_message(
        intake_case_id=case.id, party="driver", content="My account."
    )
    assert after_driver.lifecycle == "RIDER_REBUTTAL"
    assert after_driver.rider_state and after_driver.rider_state.interview_complete

    with pytest.raises(IntakeTransitionError):
        service.send_message(intake_case_id=case.id, party="driver", content="Again.")
    assert len(provider.calls) == 2

    ready, _, _ = service.send_message(
        intake_case_id=case.id, party="rider", content="One clarification."
    )
    assert ready.lifecycle == "READY_FOR_ANALYSIS"
    assert ready.rider_state and ready.rider_state.interview_complete


@pytest.mark.parametrize("lifecycle", ["READY_FOR_ANALYSIS", "ANALYSING", "AUTO_RESOLVED", "HUMAN_REVIEW"])
def test_closed_phases_cannot_be_reopened(repo: IntakeCaseRepository, lifecycle: str):
    provider = QueueIntakeProvider([_reply()])
    service = ConversationService(repo, provider)
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")
    repo.update_lifecycle(intake_case_id=case.id, lifecycle=lifecycle)

    with pytest.raises(IntakeTransitionError):
        service.send_message(intake_case_id=case.id, party="rider", content="Reopen.")

    assert provider.calls == []


def test_conversation_limit_is_enforced_before_provider_call(repo: IntakeCaseRepository):
    provider = QueueIntakeProvider([_reply()])
    service = ConversationService(repo, provider)
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")
    for index in range(MAX_PARTY_USER_MESSAGES):
        repo.add_message(
            intake_case_id=case.id,
            party="rider",
            sender="user",
            content=f"Prior turn {index}",
        )

    with pytest.raises(IntakeTransitionError) as raised:
        service.send_message(intake_case_id=case.id, party="rider", content="One too many")

    assert raised.value.code == "INTAKE_CONVERSATION_LIMIT"
    assert provider.calls == []


def test_server_owns_attribution_provenance_and_append_only_correction(repo: IntakeCaseRepository):
    provider = QueueIntakeProvider(
        [_reply(facts=[{"key": "wait", "value": "five minutes"}])]
    )
    service = ConversationService(repo, provider)
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")

    first, _, _ = service.send_message(
        intake_case_id=case.id, party="rider", content="I waited five minutes."
    )
    original = first.rider_state.facts[0]
    user_message = next(m for m in first.messages if m.sender == "user")
    assert original.stated_by == "rider"
    assert original.verification_status == "ALLEGED"
    assert original.source_message_id == user_message.id
    assert original.active is True

    provider.replies.append(
        _reply(
            facts=[
                {
                    "key": "wait",
                    "value": "ten minutes",
                    "operation": "CORRECT",
                    "corrects_fact_id": original.id,
                }
            ]
        )
    )
    corrected, _, _ = service.send_message(
        intake_case_id=case.id, party="rider", content="Correction: it was ten minutes."
    )
    assert len(corrected.rider_state.facts) == 2
    assert corrected.rider_state.facts[0].active is False
    assert corrected.rider_state.facts[1].supersedes_fact_id == original.id
    assert corrected.rider_state.facts[1].stated_by == "rider"


def test_prompts_exclude_internal_and_cross_party_material(repo: IntakeCaseRepository):
    provider = QueueIntakeProvider([_reply(complete=True), _reply()])
    service = ConversationService(repo, provider)
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")
    rider_secret = "private rider allegation 8675309"
    service.send_message(intake_case_id=case.id, party="rider", content=rider_secret)
    service.send_message(intake_case_id=case.id, party="driver", content="I have my own account.")

    driver_call = provider.calls[1]
    combined = driver_call["system_prompt"] + driver_call["user_prompt"]
    assert rider_secret not in combined
    assert "TRIP-2026-09945" not in combined
    assert "quoted" not in combined.lower()
    assert "evidence:" not in combined.lower()


def test_legacy_fact_json_is_read_without_database_reset(repo: IntakeCaseRepository):
    case = repo.create_case(source_case_id="DISP-002", trip_id="TRIP-2026-09945")
    with repo._conn() as conn:  # verify the on-disk compatibility boundary directly
        conn.execute(
            "UPDATE interview_states SET facts = ? WHERE intake_case_id = ? AND party = 'rider'",
            (json.dumps([{"key": "legacy", "value": "kept", "statedBy": "rider"}]), case.id),
        )

    first_read = repo.get_case(case.id).rider_state.facts[0]
    second_read = repo.get_case(case.id).rider_state.facts[0]
    assert first_read.id and first_read.id.startswith("LEGACY-")
    assert first_read.id == second_read.id
    assert first_read.active is True
    assert first_read.source_message_id is None


def test_intake_adapter_uses_agent_settings_and_native_schema(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "gemini_native")
    monkeypatch.setenv("AGENT_MODEL", "agent-model")
    monkeypatch.setenv("AGENT_API_KEY", "offline-test-placeholder")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "low")
    monkeypatch.setenv("GEMINI_MODEL", "must-not-be-used")
    settings = AgentSettings.from_environment()
    llm = CapturingLlmProvider(_reply().model_dump_json())
    adapter = RegistryIntakeProvider(settings=settings, provider=llm)

    result = adapter.complete_turn(
        system_prompt="safe system",
        user_prompt="safe user",
        party="rider",
        lifecycle="RIDER_INTERVIEW",
    )

    request = llm.requests[0]
    assert settings.model == "agent-model"
    assert request.response_schema is not None
    assert request.metadata["role"] == "INTAKE_RIDER"
    assert request.extra_body["reasoning_effort"] == "low"
    assert result.execution.provider == "capturing"
    assert result.execution.model == "agent-model"
