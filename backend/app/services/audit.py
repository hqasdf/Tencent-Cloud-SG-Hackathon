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

# Stage 6. The rebuttal events are per-side rather than a single pair, because
# the two rebuttals fail independently: one side failing while the other succeeds
# is a normal outcome that the trail has to be able to express.
REBUTTAL_CONTEXT_BUILT = "REBUTTAL_CONTEXT_BUILT"
RIDER_REBUTTAL_STARTED = "RIDER_REBUTTAL_STARTED"
RIDER_REBUTTAL_COMPLETED = "RIDER_REBUTTAL_COMPLETED"
RIDER_REBUTTAL_FAILED = "RIDER_REBUTTAL_FAILED"
DRIVER_REBUTTAL_STARTED = "DRIVER_REBUTTAL_STARTED"
DRIVER_REBUTTAL_COMPLETED = "DRIVER_REBUTTAL_COMPLETED"
DRIVER_REBUTTAL_FAILED = "DRIVER_REBUTTAL_FAILED"
REBUTTAL_VERIFIED = "REBUTTAL_VERIFIED"
REBUTTAL_REJECTED = "REBUTTAL_REJECTED"
REBUTTALS_NOT_RUN = "REBUTTALS_NOT_RUN"

# Which start/complete/fail events belong to which side, so the orchestrator can
# look them up rather than branching on the side at every call site.
SIDE_EVENTS: dict[str, tuple[str, str, str]] = {
    "RIDER": (RIDER_REBUTTAL_STARTED, RIDER_REBUTTAL_COMPLETED, RIDER_REBUTTAL_FAILED),
    "DRIVER": (DRIVER_REBUTTAL_STARTED, DRIVER_REBUTTAL_COMPLETED, DRIVER_REBUTTAL_FAILED),
}

# Stage 6B. Replay events are recorded for both directions — a capture and a
# replayed load — because the trail has to be able to answer "was this run
# served from storage, and was it written to storage?" after the fact.
#
# REPLAY_FALLBACK_BLOCKED is the one that matters most. It is recorded when a
# requested replay is refused, and it exists to make the absence of a provider
# call explicit rather than merely true. A future change that quietly added a
# fallback would leave this event missing, which is a far easier thing to notice
# than a call count that is one higher than expected.
REPLAY_REQUESTED = "REPLAY_REQUESTED"
REPLAY_ARTIFACT_LOADED = "REPLAY_ARTIFACT_LOADED"
REPLAY_ARTIFACT_WRITTEN = "REPLAY_ARTIFACT_WRITTEN"
REPLAY_VALIDATION_STARTED = "REPLAY_VALIDATION_STARTED"
REPLAY_VALID = "REPLAY_VALID"
REPLAY_INVALID = "REPLAY_INVALID"
ADVOCATES_REPLAYED = "ADVOCATES_REPLAYED"
REBUTTALS_REPLAYED = "REBUTTALS_REPLAYED"
JUDGE_REPLAYED = "JUDGE_REPLAYED"
REPLAY_FALLBACK_BLOCKED = "REPLAY_FALLBACK_BLOCKED"

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
    "ADVOCATES_REPLAYED",
    "AuditTrail",
    "DRIVER_REBUTTAL_COMPLETED",
    "DRIVER_REBUTTAL_FAILED",
    "DRIVER_REBUTTAL_STARTED",
    "JUDGE_COMPLETED",
    "JUDGE_CONTEXT_BUILT",
    "JUDGE_FAILED",
    "JUDGE_NOT_RUN",
    "JUDGE_OUTPUT_REJECTED",
    "JUDGE_OUTPUT_VALIDATED",
    "JUDGE_REPLAYED",
    "JUDGE_STARTED",
    "PENDING_HUMAN_REVIEW",
    "REBUTTALS_NOT_RUN",
    "REBUTTALS_REPLAYED",
    "REBUTTAL_CONTEXT_BUILT",
    "REBUTTAL_REJECTED",
    "REBUTTAL_VERIFIED",
    "REPLAY_ARTIFACT_LOADED",
    "REPLAY_ARTIFACT_WRITTEN",
    "REPLAY_FALLBACK_BLOCKED",
    "REPLAY_INVALID",
    "REPLAY_REQUESTED",
    "REPLAY_VALID",
    "REPLAY_VALIDATION_STARTED",
    "RIDER_REBUTTAL_COMPLETED",
    "RIDER_REBUTTAL_FAILED",
    "RIDER_REBUTTAL_STARTED",
    "SIDE_EVENTS",
]
