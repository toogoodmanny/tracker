"""tests/db/test_repositories.py"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from tracker.core.models import (
    Correction,
    Goal,
    Note,
    Observation,
    ObservationType,
    Session,
    SessionType,
    Snapshot,
)
from tracker.db.repositories import Database


# ---------------------------------------------------------------------------
# Schema / connection
# ---------------------------------------------------------------------------

class TestDatabaseConnection:
    def test_opens_in_memory(self, db: Database) -> None:
        assert db is not None

    def test_opens_on_disk(self, tmp_path: Path) -> None:
        from tracker.db.connection import open_database
        conn = open_database(tmp_path / "test.db")
        d = Database(conn)
        assert d is not None
        d.close()

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        from tracker.db.connection import open_database
        nested = tmp_path / "a" / "b" / "tracker.db"
        conn = open_database(nested)
        assert nested.exists()
        conn.close()

    def test_schema_version_recorded(self, in_memory_conn) -> None:
        from tracker.db.schema import CURRENT_VERSION
        row = in_memory_conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        assert row["v"] == CURRENT_VERSION

    def test_foreign_keys_enabled(self, in_memory_conn) -> None:
        result = in_memory_conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1


# ---------------------------------------------------------------------------
# SessionRepository
# ---------------------------------------------------------------------------

class TestSessionRepository:
    def test_insert_and_retrieve(self, db: Database, today: datetime.date) -> None:
        session = Session(day_date=today)
        session_id = db.sessions.insert(session)
        assert session_id > 0
        assert session.id == session_id

    def test_get_active_session_returns_none_when_empty(self, db: Database) -> None:
        assert db.sessions.get_active_session() is None

    def test_get_active_session_returns_open_session(
        self, db: Database, today: datetime.date
    ) -> None:
        s = Session(day_date=today)
        db.sessions.insert(s)
        active = db.sessions.get_active_session()
        assert active is not None
        assert active.id == s.id

    def test_close_session(self, db: Database, today: datetime.date) -> None:
        s = Session(day_date=today)
        db.sessions.insert(s)
        end_time = datetime.datetime.now()
        db.sessions.close_session(s.id, end_time)
        assert db.sessions.get_active_session() is None

    def test_close_session_stores_report_path(
        self, db: Database, today: datetime.date
    ) -> None:
        s = Session(day_date=today)
        db.sessions.insert(s)
        db.sessions.close_session(s.id, datetime.datetime.now(), report_path="/tmp/report.html")
        sessions = db.sessions.get_by_day(today)
        assert sessions[0].report_path == "/tmp/report.html"

    def test_get_by_day(self, db: Database, today: datetime.date) -> None:
        s1 = Session(day_date=today)
        s2 = Session(day_date=today, session_type=SessionType.LATE)
        db.sessions.insert(s1)
        db.sessions.insert(s2)
        sessions = db.sessions.get_by_day(today)
        assert len(sessions) == 2

    def test_get_recent_days(
        self, db: Database, today: datetime.date, yesterday: datetime.date
    ) -> None:
        db.sessions.insert(Session(day_date=today))
        db.sessions.insert(Session(day_date=yesterday))
        days = db.sessions.get_recent_days(7)
        assert today in days
        assert yesterday in days

    def test_session_type_persisted(self, db: Database, today: datetime.date) -> None:
        s = Session(day_date=today, session_type=SessionType.LATE)
        db.sessions.insert(s)
        active = db.sessions.get_active_session()
        assert active is not None
        assert active.session_type == SessionType.LATE


# ---------------------------------------------------------------------------
# SnapshotRepository
# ---------------------------------------------------------------------------

class TestSnapshotRepository:
    def _make_session(self, db: Database, today: datetime.date) -> int:
        s = Session(day_date=today)
        return db.sessions.insert(s)

    def test_insert_minimal_snapshot(
        self, db: Database, today: datetime.date
    ) -> None:
        sid = self._make_session(db, today)
        snap = Snapshot(session_id=sid, timestamp=datetime.datetime.now())
        snap_id = db.snapshots.insert(snap)
        assert snap_id > 0
        assert snap.id == snap_id

    def test_insert_full_snapshot(
        self, db: Database, today: datetime.date
    ) -> None:
        sid = self._make_session(db, today)
        snap = Snapshot(
            session_id=sid,
            timestamp=datetime.datetime.now(),
            app_name="Code",
            window_title="daemon.py",
            url="https://claude.ai",
            page_title="Claude",
            text_field_sample="help me build a tracker",
            word_count=1500,
            word_count_delta=42,
            active_file_path="/Users/user/doc.md",
            is_locked=False,
            is_afk=False,
        )
        snap_id = db.snapshots.insert(snap)
        assert snap_id > 0

    def test_get_by_session(self, db: Database, today: datetime.date) -> None:
        sid = self._make_session(db, today)
        for _ in range(5):
            db.snapshots.insert(
                Snapshot(session_id=sid, timestamp=datetime.datetime.now())
            )
        snaps = db.snapshots.get_by_session(sid)
        assert len(snaps) == 5

    def test_get_by_session_empty(self, db: Database, today: datetime.date) -> None:
        sid = self._make_session(db, today)
        assert db.snapshots.get_by_session(sid) == []

    def test_get_by_day(self, db: Database, today: datetime.date) -> None:
        sid = self._make_session(db, today)
        db.snapshots.insert(Snapshot(session_id=sid, timestamp=datetime.datetime.now()))
        snaps = db.snapshots.get_by_day(today)
        assert len(snaps) == 1

    def test_mark_screenshot_analysed(
        self, db: Database, today: datetime.date
    ) -> None:
        sid = self._make_session(db, today)
        snap = Snapshot(
            session_id=sid,
            timestamp=datetime.datetime.now(),
            screenshot_path="/tmp/screen.jpg",
        )
        db.snapshots.insert(snap)
        assert snap.id is not None
        db.snapshots.mark_screenshot_analysed(snap.id, "user was on YouTube")
        pending = db.snapshots.get_pending_screenshot_analysis()
        assert len(pending) == 0

    def test_is_locked_persisted(
        self, db: Database, today: datetime.date
    ) -> None:
        sid = self._make_session(db, today)
        snap = Snapshot(
            session_id=sid, timestamp=datetime.datetime.now(), is_locked=True
        )
        db.snapshots.insert(snap)
        results = db.snapshots.get_by_session(sid)
        assert results[0].is_locked is True


# ---------------------------------------------------------------------------
# GoalRepository
# ---------------------------------------------------------------------------

class TestGoalRepository:
    def test_upsert_and_retrieve(
        self, db: Database, today: datetime.date
    ) -> None:
        goal = Goal(day_date=today, raw_input="Focus on Fullhouse intro screen")
        db.goals.upsert(goal)
        retrieved = db.goals.get_for_day(today)
        assert retrieved is not None
        assert retrieved.raw_input == "Focus on Fullhouse intro screen"

    def test_upsert_replaces_existing(
        self, db: Database, today: datetime.date
    ) -> None:
        db.goals.upsert(Goal(day_date=today, raw_input="First plan"))
        db.goals.upsert(Goal(day_date=today, raw_input="Updated plan"))
        retrieved = db.goals.get_for_day(today)
        assert retrieved is not None
        assert retrieved.raw_input == "Updated plan"

    def test_get_for_day_returns_none_when_missing(
        self, db: Database, today: datetime.date
    ) -> None:
        assert db.goals.get_for_day(today) is None

    def test_parsed_goals_empty_when_no_json(
        self, db: Database, today: datetime.date
    ) -> None:
        db.goals.upsert(Goal(day_date=today, raw_input="some goals"))
        assert db.goals.get_parsed_goals(today) == []

    def test_parsed_goals_with_valid_json(
        self, db: Database, today: datetime.date
    ) -> None:
        parsed = json.dumps([
            {
                "description": "Work on Fullhouse intro screen",
                "project": "Fullhouse",
                "estimated_minutes": 60,
                "target_start_time": "10:30:00",
            }
        ])
        goal = Goal(day_date=today, raw_input="raw text", parsed_json=parsed)
        db.goals.upsert(goal)
        goals = db.goals.get_parsed_goals(today)
        assert len(goals) == 1
        assert goals[0].description == "Work on Fullhouse intro screen"
        assert goals[0].project == "Fullhouse"
        assert goals[0].estimated_minutes == 60


# ---------------------------------------------------------------------------
# CorrectionRepository
# ---------------------------------------------------------------------------

class TestCorrectionRepository:
    def test_insert_and_retrieve(
        self, db: Database, today: datetime.date
    ) -> None:
        c = Correction(
            day_date=today,
            correction_note="the figma at 2pm was reviewing Saad's work",
            corrected_classification="light_work",
            original_classification="deep_work",
        )
        db.corrections.insert(c)
        corrections = db.corrections.get_by_day(today)
        assert len(corrections) == 1
        assert corrections[0].corrected_classification == "light_work"

    def test_get_by_day_empty(
        self, db: Database, today: datetime.date
    ) -> None:
        assert db.corrections.get_by_day(today) == []

    def test_get_unused_weekly(
        self,
        db: Database,
        today: datetime.date,
        yesterday: datetime.date,
    ) -> None:
        db.corrections.insert(
            Correction(
                day_date=yesterday,
                correction_note="yesterday correction",
                corrected_classification="drift",
            )
        )
        results = db.corrections.get_unused_weekly(
            since_date=yesterday - datetime.timedelta(days=1)
        )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# ObservationRepository
# ---------------------------------------------------------------------------

class TestObservationRepository:
    def test_insert_and_retrieve_unused(
        self, db: Database, today: datetime.date
    ) -> None:
        obs = Observation(
            day_date=today,
            observation_type=ObservationType.NEW_APP,
            detail="First time seeing: Notion",
        )
        db.observations.insert(obs)
        unused = db.observations.get_unused_for_weekly(today)
        assert len(unused) == 1
        assert unused[0].detail == "First time seeing: Notion"

    def test_mark_used_in_weekly(
        self, db: Database, today: datetime.date
    ) -> None:
        obs = Observation(
            day_date=today,
            observation_type=ObservationType.PATTERN,
            detail="Post-lunch drift pattern",
        )
        db.observations.insert(obs)
        assert obs.id is not None
        db.observations.mark_used_in_weekly([obs.id])
        unused = db.observations.get_unused_for_weekly(today)
        assert len(unused) == 0

    def test_mark_used_empty_list_is_safe(
        self, db: Database, today: datetime.date
    ) -> None:
        db.observations.mark_used_in_weekly([])  # must not raise


# ---------------------------------------------------------------------------
# NoteRepository
# ---------------------------------------------------------------------------

class TestNoteRepository:
    def test_insert_and_retrieve(
        self, db: Database, today: datetime.date
    ) -> None:
        s = Session(day_date=today)
        db.sessions.insert(s)
        assert s.id is not None
        note = Note(
            session_id=s.id,
            note_text="Quick note about Fullhouse analytics",
            day_date=today,
        )
        db.notes.insert(note)
        notes = db.notes.get_by_day(today)
        assert len(notes) == 1
        assert notes[0].note_text == "Quick note about Fullhouse analytics"

    def test_get_by_day_empty(
        self, db: Database, today: datetime.date
    ) -> None:
        assert db.notes.get_by_day(today) == []
