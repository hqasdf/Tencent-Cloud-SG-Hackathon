from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models.intake import (
    IntakeCase,
    IntakeMessage,
    InterviewState,
    Party,
    PartyFact,
    SuggestedDisputeType,
)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "intake.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


class IntakeCaseRepository:
    """Owns SQLite records for intake cases, messages, and interview states."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS intake_cases (
                    id TEXT PRIMARY KEY,
                    source_case_id TEXT NOT NULL,
                    trip_id TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    detected_dispute_type TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    final_analysis TEXT
                );

                CREATE TABLE IF NOT EXISTS intake_messages (
                    id TEXT PRIMARY KEY,
                    intake_case_id TEXT NOT NULL,
                    party TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (intake_case_id) REFERENCES intake_cases(id)
                );

                CREATE TABLE IF NOT EXISTS interview_states (
                    intake_case_id TEXT NOT NULL,
                    party TEXT NOT NULL,
                    facts TEXT NOT NULL DEFAULT '[]',
                    missing_details TEXT NOT NULL DEFAULT '[]',
                    suggested_dispute_type TEXT NOT NULL DEFAULT 'unknown',
                    interview_complete INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (intake_case_id, party),
                    FOREIGN KEY (intake_case_id) REFERENCES intake_cases(id)
                );
                """
            )

    def create_case(self, *, source_case_id: str, trip_id: str) -> IntakeCase:
        case_id = _new_id("INTAKE")
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO intake_cases (id, source_case_id, trip_id, lifecycle, detected_dispute_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (case_id, source_case_id, trip_id, "RIDER_INTERVIEW", "unknown", now, now),
            )
            for party in ("rider", "driver"):
                conn.execute(
                    "INSERT INTO interview_states (intake_case_id, party, facts, missing_details, suggested_dispute_type, interview_complete, updated_at) VALUES (?, ?, '[]', '[]', 'unknown', 0, ?)",
                    (case_id, party, now),
                )
        result = self.get_case(case_id)
        assert result is not None
        return result

    def get_case(self, intake_case_id: str) -> IntakeCase | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM intake_cases WHERE id = ?", (intake_case_id,)
            ).fetchone()
            if not row:
                return None
            messages = [
                self._row_to_message(r)
                for r in conn.execute(
                    "SELECT * FROM intake_messages WHERE intake_case_id = ? ORDER BY timestamp ASC",
                    (intake_case_id,),
                )
            ]
            rider_state = self._get_state(conn, intake_case_id, "rider")
            driver_state = self._get_state(conn, intake_case_id, "driver")
        return IntakeCase(
            id=row["id"],
            sourceCaseId=row["source_case_id"],
            tripId=row["trip_id"],
            lifecycle=row["lifecycle"],
            detectedDisputeType=row["detected_dispute_type"],
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
            finalAnalysis=json.loads(row["final_analysis"]) if row["final_analysis"] else None,
            messages=messages,
            riderState=rider_state,
            driverState=driver_state,
        )

    def add_message(
        self, *, intake_case_id: str, party: Party, sender: str, content: str
    ) -> IntakeMessage:
        msg_id = _new_id("MSG")
        timestamp = _utc_now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO intake_messages (id, intake_case_id, party, sender, content, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, intake_case_id, party, sender, content, timestamp),
            )
            conn.execute(
                "UPDATE intake_cases SET updated_at = ? WHERE id = ?",
                (timestamp, intake_case_id),
            )
        return IntakeMessage(
            id=msg_id,
            intakeCaseId=intake_case_id,
            party=party,
            sender=sender,  # type: ignore[arg-type]
            content=content,
            timestamp=timestamp,
        )

    def get_messages(self, intake_case_id: str, party: Party | None = None) -> list[IntakeMessage]:
        with self._conn() as conn:
            if party:
                rows = conn.execute(
                    "SELECT * FROM intake_messages WHERE intake_case_id = ? AND party = ? ORDER BY timestamp ASC",
                    (intake_case_id, party),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM intake_messages WHERE intake_case_id = ? ORDER BY timestamp ASC",
                    (intake_case_id,),
                ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def update_state(
        self,
        *,
        intake_case_id: str,
        party: Party,
        facts: list[PartyFact],
        missing_details: list[str],
        suggested_dispute_type: SuggestedDisputeType,
        interview_complete: bool,
    ) -> None:
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE interview_states
                   SET facts = ?, missing_details = ?, suggested_dispute_type = ?, interview_complete = ?, updated_at = ?
                   WHERE intake_case_id = ? AND party = ?""",
                (
                    json.dumps([f.model_dump(by_alias=True) for f in facts]),
                    json.dumps(missing_details),
                    suggested_dispute_type,
                    int(interview_complete),
                    now,
                    intake_case_id,
                    party,
                ),
            )
            conn.execute(
                "UPDATE intake_cases SET updated_at = ? WHERE id = ?",
                (now, intake_case_id),
            )

    def update_lifecycle(
        self,
        *,
        intake_case_id: str,
        lifecycle: str,
        detected_dispute_type: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._conn() as conn:
            if detected_dispute_type is not None:
                conn.execute(
                    "UPDATE intake_cases SET lifecycle = ?, detected_dispute_type = ?, updated_at = ? WHERE id = ?",
                    (lifecycle, detected_dispute_type, now, intake_case_id),
                )
            else:
                conn.execute(
                    "UPDATE intake_cases SET lifecycle = ?, updated_at = ? WHERE id = ?",
                    (lifecycle, now, intake_case_id),
                )

    def save_analysis(self, *, intake_case_id: str, analysis: dict) -> None:
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE intake_cases SET final_analysis = ?, updated_at = ? WHERE id = ?",
                (json.dumps(analysis), now, intake_case_id),
            )

    def _get_state(
        self, conn: sqlite3.Connection, intake_case_id: str, party: Party
    ) -> InterviewState:
        row = conn.execute(
            "SELECT * FROM interview_states WHERE intake_case_id = ? AND party = ?",
            (intake_case_id, party),
        ).fetchone()
        if not row:
            now = _utc_now()
            conn.execute(
                "INSERT INTO interview_states (intake_case_id, party, facts, missing_details, suggested_dispute_type, interview_complete, updated_at) VALUES (?, ?, '[]', '[]', 'unknown', 0, ?)",
                (intake_case_id, party, now),
            )
            return InterviewState(intakeCaseId=intake_case_id, party=party, updatedAt=now)
        return self._row_to_state(row)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> IntakeMessage:
        return IntakeMessage(
            id=row["id"],
            intakeCaseId=row["intake_case_id"],
            party=row["party"],
            sender=row["sender"],
            content=row["content"],
            timestamp=row["timestamp"],
        )

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> InterviewState:
        return InterviewState(
            intakeCaseId=row["intake_case_id"],
            party=row["party"],
            facts=[PartyFact(**f) for f in json.loads(row["facts"])],
            missingDetails=json.loads(row["missing_details"]),
            suggestedDisputeType=row["suggested_dispute_type"],
            interviewComplete=bool(row["interview_complete"]),
            updatedAt=row["updated_at"],
        )
