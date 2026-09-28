"""Observable pipeline events.

An audit trail exists so that a decision can be reconstructed after the fact
without trusting anyone's summary of it. Two constraints shape this module:

1. **Events carry metadata, never payloads.** A Judge event records that the
   context was built and how large it was, not the context. Recording the prompt
   would put case narrative and model input into the audit log, which is a
   different (and much larger) disclosure than "the Judge ran".

2. **No secrets and no reasoning.** Nothing here may contain a credential, an
   Authorization header, or a model's internal deliberation. The Judge's
   ``reasoningSummary`` is a deliberate human-facing artefact and is surfaced in
   the response body; it is not copied into the audit trail.

The trail is in-memory and per-request. Persistence is a later concern; the
event vocabulary is what needs to be stable now.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.judge import AuditEvent

# Event names. Stable strings, because the frontend, the audit trail and the
# tests all need to refer to the same vocabulary.
JUDGE_CONTEXT_BUILT = "JUDGE_CONTEXT_BUILT"
JUDGE_STARTED = "JUDGE_STARTED"
JUDGE_COMPLETED = "JUDGE_COMPLETED"
JUDGE_FAILED = "JUDGE_FAILED"
JUDGE_OUTPUT_VALIDATED = "JUDGE_OUTPUT_VALIDATED"
JUDGE_OUTPUT_REJECTED = "JUDGE_OUTPUT_REJECTED"
JUDGE_NOT_RUN = "JUDGE_NOT_RUN"
PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"

# Keys that must never reach the trail. Checked rather than assumed, because
# "we did not log the key" is exactly the kind of claim that decays silently.
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "headers",
        "prompt",
        "system_prompt",
        "user_prompt",
        "raw_response",
        "chain_of_thought",
        "reasoning",
    }
)


class AuditTrail:
    """Accumulates ordered pipeline events for one request."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, event: str, **metadata: object) -> AuditEvent:
        safe = {
            key: value
            for key, value in metadata.items()
            if key.lower() not in _FORBIDDEN_METADATA_KEYS
        }
        entry = AuditEvent(
            event=event,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=safe,
        )
        self._events.append(entry)
        return entry

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def names(self) -> list[str]:
        return [event.event for event in self._events]

    def __len__(self) -> int:
        return len(self._events)


__all__ = [
    "AuditTrail",
    "JUDGE_COMPLETED",
    "JUDGE_CONTEXT_BUILT",
    "JUDGE_FAILED",
    "JUDGE_NOT_RUN",
    "JUDGE_OUTPUT_REJECTED",
    "JUDGE_OUTPUT_VALIDATED",
    "JUDGE_STARTED",
    "PENDING_HUMAN_REVIEW",
]
