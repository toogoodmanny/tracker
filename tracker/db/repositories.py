"""
tracker/db/repositories.py

All database access goes through these repository classes.
No other module may contain SQL strings.
Each method is typed and raises specific exceptions — no bare catch-alls.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from typing import Any

from tracker.core.models import (
    Correction,
    Goal,
    Note,
    Observation,
    ObservationType,
    ParsedGoal,
    Session,
    SessionType,
    Snapshot,
    Subgoal,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _parse_dt(value: str | None) -> datetime.datetime | None:
    if value is None:
        return None
    return datetime.datetime.fromisoformat(value)


def _parse_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value)


def _fmt_dt(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _fmt_date(value: datetime.date) -> str:
    return value.isoformat()


# ---------------------------------------------------------------------------
# SnapshotRepository
# ---------------------------------------------------------------------------

class SnapshotRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, snapshot: Snapshot) -> int:
        """
        Insert a snapshot row.
        Returns the new row ID.
        Raises sqlite3.IntegrityError if session_id is invalid.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO snapshots (
                session_id, timestamp, app_name, window_title, url, page_title,
                text_field_sample, word_count, word_count_delta, active_file_path,
                screenshot_path, screenshot_analysed, screenshot_analysis,
                is_locked, is_afk, manually_corrected
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot.session_id,
                snapshot.timestamp.isoformat(),
                snapshot.app_name,
                snapshot.window_title,
                snapshot.url,
                snapshot.page_title,
                snapshot.text_field_sample,
                snapshot.word_count,
                snapshot.word_count_delta,
                snapshot.active_file_path,
                snapshot.screenshot_path,
                int(snapshot.screenshot_analysed),
                snapshot.screenshot_analysis,
                int(snapshot.is_locked),
                int(snapshot.is_afk),
                int(snapshot.manually_corrected),
            ),
        )
        self._conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        snapshot.id = row_id
        return row_id

    def get_by_session(self, session_id: int) -> list[Snapshot]:
        """Return all snapshots for a session, ordered by timestamp."""
        rows = self._conn.execute(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def get_by_day(self, day_date: datetime.date) -> list[Snapshot]:
        """Return all snapshots for a calendar day, ordered by timestamp."""
        day_str = _fmt_date(day_date)
        rows = self._conn.execute(
            "SELECT * FROM snapshots WHERE date(timestamp) = ? ORDER BY timestamp",
            (day_str,),
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def get_pending_screenshot_analysis(self) -> list[Snapshot]:
        """Return snapshots that have a screenshot but haven't been analysed."""
        rows = self._conn.execute(
            """
            SELECT * FROM snapshots
            WHERE screenshot_path IS NOT NULL AND screenshot_analysed = 0
            ORDER BY timestamp
            """,
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def mark_screenshot_analysed(
        self, snapshot_id: int, analysis: str
    ) -> None:
        """Record LLM analysis result for a screenshot."""
        self._conn.execute(
            """
            UPDATE snapshots
            SET screenshot_analysed = 1, screenshot_analysis = ?
            WHERE id = ?
            """,
            (analysis, snapshot_id),
        )
        self._conn.commit()

    def mark_corrected(self, snapshot_id: int) -> None:
        self._conn.execute(
            "UPDATE snapshots SET manually_corrected = 1 WHERE id = ?",
            (snapshot_id,),
        )
        self._conn.commit()

    def _row_to_snapshot(self, row: sqlite3.Row) -> Snapshot:
        d = _row_to_dict(row)
        s = Snapshot(
            session_id=d["session_id"],
            timestamp=datetime.datetime.fromisoformat(d["timestamp"]),
            app_name=d["app_name"],
            window_title=d["window_title"],
            url=d["url"],
            page_title=d["page_title"],
            text_field_sample=d["text_field_sample"],
            word_count=d["word_count"],
            word_count_delta=d["word_count_delta"],
            active_file_path=d["active_file_path"],
            screenshot_path=d["screenshot_path"],
            screenshot_analysed=bool(d["screenshot_analysed"]),
            screenshot_analysis=d["screenshot_analysis"],
            is_locked=bool(d["is_locked"]),
            is_afk=bool(d["is_afk"]),
            manually_corrected=bool(d["manually_corrected"]),
        )
        s.id = d["id"]
        return s


# ---------------------------------------------------------------------------
# SessionRepository
# ---------------------------------------------------------------------------

class SessionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, session: Session) -> int:
        """
        Insert a new session.
        Raises sqlite3.IntegrityError on constraint violation.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO sessions (start_time, session_type, day_date, goals_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                session.start_time.isoformat(),
                session.session_type.value,
                _fmt_date(session.day_date),
                session.goals_json,
            ),
        )
        self._conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        session.id = row_id
        return row_id

    def close_session(
        self,
        session_id: int,
        end_time: datetime.datetime,
        report_path: str | None = None,
    ) -> None:
        """Mark a session as ended."""
        self._conn.execute(
            "UPDATE sessions SET end_time = ?, report_path = ? WHERE id = ?",
            (end_time.isoformat(), report_path, session_id),
        )
        self._conn.commit()

    def get_active_session(self) -> Session | None:
        """Return the most recent session that has no end_time."""
        row = self._conn.execute(
            """
            SELECT * FROM sessions
            WHERE end_time IS NULL
            ORDER BY start_time DESC
            LIMIT 1
            """,
        ).fetchone()
        return self._row_to_session(row) if row else None

    def get_by_day(self, day_date: datetime.date) -> list[Session]:
        """Return all sessions for a calendar day."""
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE day_date = ? ORDER BY start_time",
            (_fmt_date(day_date),),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_recent_days(self, n_days: int) -> list[datetime.date]:
        """Return dates of the N most recent days that have sessions."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT day_date FROM sessions
            ORDER BY day_date DESC
            LIMIT ?
            """,
            (n_days,),
        ).fetchall()
        return [_parse_date(r["day_date"]) for r in rows]

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        d = _row_to_dict(row)
        s = Session(
            day_date=_parse_date(d["day_date"]),
            session_type=SessionType(d["session_type"]),
            start_time=datetime.datetime.fromisoformat(d["start_time"]),
            end_time=_parse_dt(d["end_time"]),
            goals_json=d["goals_json"],
            report_path=d["report_path"],
        )
        s.id = d["id"]
        return s


# ---------------------------------------------------------------------------
# GoalRepository
# ---------------------------------------------------------------------------

class GoalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, goal: Goal) -> int:
        """
        Insert or replace goals for a day.
        Returns row ID.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO goals (day_date, raw_input, parsed_json)
            VALUES (?, ?, ?)
            ON CONFLICT(day_date) DO UPDATE SET
                raw_input = excluded.raw_input,
                parsed_json = excluded.parsed_json
            """,
            (
                _fmt_date(goal.day_date),
                goal.raw_input,
                goal.parsed_json,
            ),
        )
        self._conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        goal.id = row_id
        return row_id

    def get_for_day(self, day_date: datetime.date) -> Goal | None:
        row = self._conn.execute(
            "SELECT * FROM goals WHERE day_date = ?",
            (_fmt_date(day_date),),
        ).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        g = Goal(
            day_date=_parse_date(d["day_date"]),
            raw_input=d["raw_input"],
            parsed_json=d["parsed_json"],
        )
        g.id = d["id"]
        return g

    def get_parsed_goals(self, day_date: datetime.date) -> list[ParsedGoal]:
        """Return structured goals for a day, or empty list if none/unparsed."""
        goal = self.get_for_day(day_date)
        if goal is None or goal.parsed_json is None:
            return []
        try:
            raw_list: list[dict] = json.loads(goal.parsed_json)
            return [
                ParsedGoal(
                    description=g["description"],
                    project=g.get("project"),
                    estimated_minutes=g.get("estimated_minutes"),
                    target_start_time=_parse_time(g.get("target_start_time")),
                )
                for g in raw_list
            ]
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Could not parse goals JSON for %s: %s", day_date, exc)
            return []


def _parse_time(value: str | None) -> datetime.time | None:
    if value is None:
        return None
    try:
        return datetime.time.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# CorrectionRepository
# ---------------------------------------------------------------------------

class CorrectionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, correction: Correction) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO corrections (
                day_date, snapshot_id_start, snapshot_id_end,
                original_classification, corrected_classification, correction_note
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _fmt_date(correction.day_date),
                correction.snapshot_id_start,
                correction.snapshot_id_end,
                correction.original_classification,
                correction.corrected_classification,
                correction.correction_note,
            ),
        )
        self._conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        correction.id = row_id
        return row_id

    def get_by_day(self, day_date: datetime.date) -> list[Correction]:
        rows = self._conn.execute(
            "SELECT * FROM corrections WHERE day_date = ? ORDER BY created_at",
            (_fmt_date(day_date),),
        ).fetchall()
        return [self._row_to_correction(r) for r in rows]

    def get_unused_weekly(self, since_date: datetime.date) -> list[Correction]:
        rows = self._conn.execute(
            "SELECT * FROM corrections WHERE day_date >= ? ORDER BY created_at",
            (_fmt_date(since_date),),
        ).fetchall()
        return [self._row_to_correction(r) for r in rows]

    def _row_to_correction(self, row: sqlite3.Row) -> Correction:
        d = _row_to_dict(row)
        c = Correction(
            day_date=_parse_date(d["day_date"]),
            correction_note=d["correction_note"],
            corrected_classification=d["corrected_classification"],
            snapshot_id_start=d["snapshot_id_start"],
            snapshot_id_end=d["snapshot_id_end"],
            original_classification=d["original_classification"],
        )
        c.id = d["id"]
        return c


# ---------------------------------------------------------------------------
# ObservationRepository
# ---------------------------------------------------------------------------

class ObservationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, observation: Observation) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO observations (
                timestamp, day_date, observation_type, detail, used_in_weekly
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                observation.timestamp.isoformat(),
                _fmt_date(observation.day_date),
                observation.observation_type.value,
                observation.detail,
                int(observation.used_in_weekly),
            ),
        )
        self._conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        observation.id = row_id
        return row_id

    def get_unused_for_weekly(self, since_date: datetime.date) -> list[Observation]:
        rows = self._conn.execute(
            """
            SELECT * FROM observations
            WHERE used_in_weekly = 0 AND day_date >= ?
            ORDER BY timestamp
            """,
            (_fmt_date(since_date),),
        ).fetchall()
        return [self._row_to_observation(r) for r in rows]

    def mark_used_in_weekly(self, observation_ids: list[int]) -> None:
        if not observation_ids:
            return
        placeholders = ",".join("?" * len(observation_ids))
        self._conn.execute(
            f"UPDATE observations SET used_in_weekly = 1 WHERE id IN ({placeholders})",
            observation_ids,
        )
        self._conn.commit()

    def _row_to_observation(self, row: sqlite3.Row) -> Observation:
        d = _row_to_dict(row)
        o = Observation(
            day_date=_parse_date(d["day_date"]),
            observation_type=ObservationType(d["observation_type"]),
            detail=d["detail"],
            timestamp=datetime.datetime.fromisoformat(d["timestamp"]),
            used_in_weekly=bool(d["used_in_weekly"]),
        )
        o.id = d["id"]
        return o


# ---------------------------------------------------------------------------
# NoteRepository
# ---------------------------------------------------------------------------

class NoteRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, note: Note) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO notes (session_id, timestamp, note_text, day_date)
            VALUES (?, ?, ?, ?)
            """,
            (
                note.session_id,
                note.timestamp.isoformat(),
                note.note_text,
                _fmt_date(note.day_date),
            ),
        )
        self._conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        note.id = row_id
        return row_id

    def get_by_day(self, day_date: datetime.date) -> list[Note]:
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE day_date = ? ORDER BY timestamp",
            (_fmt_date(day_date),),
        ).fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            n = Note(
                session_id=d["session_id"],
                note_text=d["note_text"],
                day_date=_parse_date(d["day_date"]),
                timestamp=datetime.datetime.fromisoformat(d["timestamp"]),
            )
            n.id = d["id"]
            result.append(n)
        return result


# ---------------------------------------------------------------------------
# SubgoalRepository
# ---------------------------------------------------------------------------

class SubgoalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, subgoal: Subgoal) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO subgoals (day_date, parent_goal, description, done)
            VALUES (?, ?, ?, ?)
            """,
            (
                _fmt_date(subgoal.day_date),
                subgoal.parent_goal,
                subgoal.description,
                int(subgoal.done),
            ),
        )
        self._conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        subgoal.id = row_id
        return row_id

    def list_for_day(self, day_date: datetime.date) -> list[Subgoal]:
        rows = self._conn.execute(
            "SELECT * FROM subgoals WHERE day_date = ? ORDER BY id",
            (_fmt_date(day_date),),
        ).fetchall()
        return [self._row_to_subgoal(r) for r in rows]

    def update(
        self,
        subgoal_id: int,
        description: str | None = None,
        done: bool | None = None,
    ) -> None:
        sets: list[str] = []
        values: list[object] = []
        if description is not None:
            sets.append("description = ?")
            values.append(description)
        if done is not None:
            sets.append("done = ?")
            values.append(int(done))
        if not sets:
            return
        values.append(subgoal_id)
        self._conn.execute(
            f"UPDATE subgoals SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        self._conn.commit()

    def delete(self, subgoal_id: int) -> None:
        self._conn.execute("DELETE FROM subgoals WHERE id = ?", (subgoal_id,))
        self._conn.commit()

    def _row_to_subgoal(self, row: sqlite3.Row) -> Subgoal:
        d = _row_to_dict(row)
        sg = Subgoal(
            day_date=_parse_date(d["day_date"]),
            description=d["description"],
            parent_goal=d["parent_goal"],
            done=bool(d["done"]),
        )
        sg.id = d["id"]
        return sg


# ---------------------------------------------------------------------------
# Facade — convenient access to all repos from one object
# ---------------------------------------------------------------------------

class Database:
    """
    Single access point for all repositories.
    Pass this object around instead of the raw connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.snapshots = SnapshotRepository(conn)
        self.sessions = SessionRepository(conn)
        self.goals = GoalRepository(conn)
        self.corrections = CorrectionRepository(conn)
        self.observations = ObservationRepository(conn)
        self.notes = NoteRepository(conn)
        self.subgoals = SubgoalRepository(conn)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            logger.warning("Error closing database: %s", exc)
