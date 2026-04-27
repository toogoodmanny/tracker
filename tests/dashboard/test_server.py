"""tests/dashboard/test_server.py"""

from __future__ import annotations

import datetime

import pytest

from tracker.core.models import Goal, Session, SessionType, Snapshot, Subgoal
from tracker.dashboard.server import (
    _build_timeline_blocks,
    _serialise_state,
    _split_goal_lines,
)


class TestSerialiseState:
    def test_no_session_returns_empty_state(self, db) -> None:
        state = _serialise_state(db, datetime.date.today())
        assert state["session_active"] is False
        assert state["timeline_blocks"] == []
        assert state["goals_with_subs"] == []

    def test_active_session_with_snapshots(self, db) -> None:
        today = datetime.date.today()
        session = Session(
            day_date=today,
            session_type=SessionType.PRIMARY,
            start_time=datetime.datetime.now(),
        )
        sid = db.sessions.insert(session)
        db.snapshots.insert(Snapshot(
            session_id=sid,
            timestamp=datetime.datetime.now(),
            app_name="Figma",
            window_title="Fullhouse",
        ))
        db.goals.upsert(Goal(day_date=today, raw_input="ship Fullhouse intro"))
        db.subgoals.insert(Subgoal(
            day_date=today,
            description="intro screen",
            parent_goal="ship Fullhouse intro",
        ))

        state = _serialise_state(db, today)
        assert state["session_active"] is True
        assert state["session_id"] == sid
        assert len(state["timeline_blocks"]) == 1
        assert state["timeline_blocks"][0]["app"] == "Figma"
        assert state["main_goals"] == ["ship Fullhouse intro"]
        assert state["goals_with_subs"][0]["goal"] == "ship Fullhouse intro"
        assert state["goals_with_subs"][0]["subgoals"][0]["description"] == "intro screen"

    def test_orphan_subgoal_bucketed_under_other(self, db) -> None:
        today = datetime.date.today()
        db.goals.upsert(Goal(day_date=today, raw_input="goal A"))
        db.subgoals.insert(Subgoal(day_date=today, description="floating", parent_goal=None))
        state = _serialise_state(db, today)
        # Last entry should be the orphan bucket (goal == "")
        last = state["goals_with_subs"][-1]
        assert last["goal"] == ""
        assert last["subgoals"][0]["description"] == "floating"


class TestTimelineBlocks:
    def test_consecutive_same_app_is_one_block(self) -> None:
        snaps = [
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 0),
                     app_name="Figma", window_title="A"),
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 5),
                     app_name="Figma", window_title="B"),
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 10),
                     app_name="Figma", window_title="A"),
        ]
        blocks = _build_timeline_blocks(snaps)
        assert len(blocks) == 1
        assert blocks[0]["app"] == "Figma"
        assert blocks[0]["start"] == "10:00"
        assert blocks[0]["end"] == "10:10"
        assert blocks[0]["duration_minutes"] == 10
        assert blocks[0]["titles"] == ["A", "B"]

    def test_app_change_creates_new_block(self) -> None:
        snaps = [
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 0),
                     app_name="Figma", window_title="x"),
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 5),
                     app_name="Claude", window_title="y"),
        ]
        blocks = _build_timeline_blocks(snaps)
        assert len(blocks) == 2
        assert blocks[0]["app"] == "Figma"
        assert blocks[1]["app"] == "Claude"

    def test_locked_creates_separate_block(self) -> None:
        snaps = [
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 0),
                     app_name="Figma"),
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 5),
                     app_name="Figma", is_locked=True),
            Snapshot(session_id=1, timestamp=datetime.datetime(2026, 4, 27, 10, 10),
                     app_name="Figma"),
        ]
        blocks = _build_timeline_blocks(snaps)
        assert [b["app"] for b in blocks] == ["Figma", "Locked", "Figma"]


class TestSplitGoalLines:
    def test_splits_lines(self) -> None:
        assert _split_goal_lines("a\nb\nc") == ["a", "b", "c"]

    def test_strips_bullets(self) -> None:
        assert _split_goal_lines("- one\n* two\n• three") == ["one", "two", "three"]

    def test_skips_blanks(self) -> None:
        assert _split_goal_lines("\nfoo\n\n\nbar\n") == ["foo", "bar"]
