from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.llm.hunyuan_client import (
    HunyuanChatMessage,
    HunyuanClient,
    HunyuanUnavailableError,
)
from app.llm.openai_client import OpenAIClient, load_openai_config
from app.llm.gemini_client import GeminiClient, load_gemini_config
from app.models.intake import (
    HunyuanStructuredReply,
    IntakeCase,
    IntakeMessage,
    Party,
    PartyFact,
)
from app.repositories.intake_repository import IntakeCaseRepository


# ---------------------------------------------------------------------------
# System prompt – establishes Hunyuan as an intake interviewer, not an
# arbitrator.  It enforces the safety rules from the design spec.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are RydeResolve Intake, an AI assistant that conducts dispute intake interviews for a ride-hailing platform. You are an interviewer, NOT an arbitrator. You do NOT decide outcomes, promise refunds, or make final rulings.

Your role is to:
1. Ask one concise, relevant follow-up question when material details are missing.
2. Attribute facts ONLY to the party you are currently interviewing.
3. Use "unknown" for any fact the party has not mentioned — never infer or guess.
4. NEVER claim you checked GPS, fare, company policy, chat logs, or the other party's private account. You only know what this party tells you.
5. NEVER promise a refund or make a final ruling.
6. Return STRICT JSON matching the schema below.

PARTY CONTEXT:
- Current party: {party}
- Trip ID: {trip_id}
- Dispute topic: {dispute_topic}

You must return a JSON object with EXACTLY these fields:
{{
  "assistant_message": "your visible reply to the user (1-3 sentences, conversational)",
  "suggested_dispute_type": "route_deviation" | "no_show_charge" | "unknown",
  "party_facts": [
    {{"key": "short_fact_label", "value": "fact value as stated by this party", "stated_by": "{party}"}}
  ],
  "missing_details": ["important detail not yet provided"],
  "interview_complete": true | false
}}

Rules for party_facts:
- Only include facts THIS party has explicitly stated.
- Each fact must have stated_by set to "{party}".
- If the party has not stated any facts yet, return an empty list.
- Do NOT include facts from the other party or from system evidence.

Rules for interview_complete:
- Set to true only when you have enough information about this party's account to proceed (typically after 2-3 exchanges with substantive details).
- Set to false if important details are still missing.

Return ONLY the JSON object. No markdown, no code fences, no explanation outside the JSON."""


_OPENING_QUESTIONS = {
    "rider": "Hello, I'm here to understand your side of this dispute. Can you describe what happened during your trip?",
    "driver": "Hello, I'm here to understand your side of this dispute. Can you describe what happened during this trip from your perspective?",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(content: str) -> dict | None:
    """Try to extract a JSON object from the LLM response content."""
    # Try direct JSON parse first
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find a JSON block in the content
    patterns = [
        r"```json\s*(.*?)\s*```",
        r"```\s*(.*?)\s*```",
        r"\{.*\}",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            try:
                candidate = match.group(1) if match.lastindex else match.group(0)
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError, IndexError):
                continue
    return None


class ConversationService:
    """Saves an incoming party message, builds the bounded prompt, calls Hunyuan,
    validates the structured reply, saves the assistant response and updated
    interview state, and returns the refreshed intake case."""

    def __init__(
        self,
        repository: IntakeCaseRepository,
        hunyuan_client: HunyuanClient | None = None,
    ) -> None:
        self._repository = repository
        if hunyuan_client is not None:
            self._hunyuan = hunyuan_client
        else:
            gemini_cfg = load_gemini_config()
            if gemini_cfg.is_configured:
                # Gemini free tier — highest priority.
                self._hunyuan = GeminiClient(config=gemini_cfg)
            else:
                # OpenAI if key set, otherwise Hunyuan/TokenHub.
                openai_cfg = load_openai_config()
                self._hunyuan = OpenAIClient(config=openai_cfg) if openai_cfg.is_configured else HunyuanClient()

    def send_message(
        self,
        *,
        intake_case_id: str,
        party: Party,
        content: str,
    ) -> tuple[IntakeCase, IntakeMessage]:
        """Process one party message through the LLM interview turn."""
        if not content or not content.strip():
            raise ValueError("Message content cannot be blank")

        case = self._repository.get_case(intake_case_id)
        if case is None:
            raise LookupError(f"Intake case {intake_case_id} not found")

        # Save the user's message
        self._repository.add_message(
            intake_case_id=intake_case_id,
            party=party,
            sender="user",
            content=content,
        )

        # If this is the first message for this party, prepend an opening question
        party_messages = [
            m for m in case.messages if m.party == party and m.sender == "assistant"
        ]
        if not party_messages:
            opening = _OPENING_QUESTIONS[party]
            self._repository.add_message(
                intake_case_id=intake_case_id,
                party=party,
                sender="assistant",
                content=opening,
            )

        # Build the conversation history for this party only (party isolation)
        all_messages = self._repository.get_messages(intake_case_id, party=party)

        # Build the system prompt
        dispute_topic = case.detected_dispute_type
        if dispute_topic == "unknown":
            # Check if either interview state has a suggestion
            if case.rider_state and case.rider_state.suggested_dispute_type != "unknown":
                dispute_topic = case.rider_state.suggested_dispute_type
            elif case.driver_state and case.driver_state.suggested_dispute_type != "unknown":
                dispute_topic = case.driver_state.suggested_dispute_type

        system_prompt = _SYSTEM_PROMPT.format(
            party=party,
            trip_id=case.trip_id,
            dispute_topic=dispute_topic,
        )

        # Build chat messages
        chat_messages: list[HunyuanChatMessage] = [
            HunyuanChatMessage(role="system", content=system_prompt)
        ]
        for msg in all_messages:
            chat_messages.append(
                HunyuanChatMessage(
                    role="user" if msg.sender == "user" else "assistant",
                    content=msg.content,
                )
            )

        # Call Hunyuan
        try:
            result = self._hunyuan.chat(chat_messages)
        except HunyuanUnavailableError as error:
            # Save a safe retry message but don't persist any extraction
            retry_msg = self._repository.add_message(
                intake_case_id=intake_case_id,
                party=party,
                sender="assistant",
                content="I'm having trouble connecting to the interview service right now. Your message has been saved. Please try sending it again in a moment.",
            )
            # Update lifecycle if needed
            self._update_lifecycle(intake_case_id)
            refreshed = self._repository.get_case(intake_case_id)
            assert refreshed is not None
            raise HunyuanUnavailableError(
                code=error.code, message=error.args[0] if error.args else str(error)
            ) from error

        # Parse and validate the structured reply
        raw_json = _extract_json(result.content)
        if raw_json is None:
            # Malformed reply — save a retry message, no extraction
            retry_msg = self._repository.add_message(
                intake_case_id=intake_case_id,
                party=party,
                sender="assistant",
                content="I apologise, I had trouble processing that response. Could you please rephrase your last message?",
            )
            self._update_lifecycle(intake_case_id)
            refreshed = self._repository.get_case(intake_case_id)
            assert refreshed is not None
            return refreshed, retry_msg

        try:
            reply = HunyuanStructuredReply.model_validate(raw_json)
        except Exception:
            retry_msg = self._repository.add_message(
                intake_case_id=intake_case_id,
                party=party,
                sender="assistant",
                content="I apologise, I had trouble structuring the information. Could you please rephrase your last message?",
            )
            self._update_lifecycle(intake_case_id)
            refreshed = self._repository.get_case(intake_case_id)
            assert refreshed is not None
            return refreshed, retry_msg

        # Save the assistant's visible message
        assistant_msg = self._repository.add_message(
            intake_case_id=intake_case_id,
            party=party,
            sender="assistant",
            content=reply.assistant_message,
        )

        # Update the interview state
        self._repository.update_state(
            intake_case_id=intake_case_id,
            party=party,
            facts=reply.party_facts,
            missing_details=reply.missing_details,
            suggested_dispute_type=reply.suggested_dispute_type,
            interview_complete=reply.interview_complete,
        )

        # Update case lifecycle
        self._update_lifecycle(intake_case_id)

        refreshed = self._repository.get_case(intake_case_id)
        assert refreshed is not None
        return refreshed, assistant_msg

    def _update_lifecycle(self, intake_case_id: str) -> None:
        """Update the case lifecycle based on interview completion states."""
        case = self._repository.get_case(intake_case_id)
        if case is None:
            return

        rider_done = case.rider_state is not None and case.rider_state.interview_complete
        driver_done = case.driver_state is not None and case.driver_state.interview_complete

        # Determine detected dispute type
        detected = case.detected_dispute_type
        if detected == "unknown":
            if case.rider_state and case.rider_state.suggested_dispute_type != "unknown":
                detected = case.rider_state.suggested_dispute_type
            elif case.driver_state and case.driver_state.suggested_dispute_type != "unknown":
                detected = case.driver_state.suggested_dispute_type

        if rider_done and driver_done:
            self._repository.update_lifecycle(
                intake_case_id=intake_case_id,
                lifecycle="READY_FOR_ANALYSIS",
                detected_dispute_type=detected,
            )
        elif rider_done:
            self._repository.update_lifecycle(
                intake_case_id=intake_case_id,
                lifecycle="DRIVER_INTERVIEW",
                detected_dispute_type=detected,
            )
        else:
            self._repository.update_lifecycle(
                intake_case_id=intake_case_id,
                lifecycle="RIDER_INTERVIEW",
                detected_dispute_type=detected,
            )
