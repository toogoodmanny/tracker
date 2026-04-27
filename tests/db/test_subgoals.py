"""tests/db/test_subgoals.py"""

from __future__ import annotations

import datetime

import pytest

from tracker.core.models import Subgoal


class TestSubgoalRepository:
    def test_insert_and_list(self, db) -> None:
        today = datetime.date.today()
        sg = Subgoal(day_date=today, description="ship intro screen", parent_goal="Fullhouse")
        db.subgoals.insert(sg)
        assert sg.id is not None

        rows = db.subgoals.list_for_day(today)
        assert len(rows) == 1
        assert rows[0].description == "ship intro screen"
        assert rows[0].parent_goal == "Fullhouse"
        assert rows[0].done is False

    def test_update_done(self, db) -> None:
        today = datetime.date.today()
        sg = Subgoal(day_date=today, description="x")
        db.subgoals.insert(sg)
        db.subgoals.update(sg.id, done=True)

        rows = db.subgoals.list_for_day(today)
        assert rows[0].done is True

    def test_update_description(self, db) -> None:
        today = datetime.date.today()
        sg = Subgoal(day_date=today, description="old")
        db.subgoals.insert(sg)
        db.subgoals.update(sg.id, description="new")

        rows = db.subgoals.list_for_day(today)
        assert rows[0].description == "new"

    def test_delete(self, db) -> None:
        today = datetime.date.today()
        sg = Subgoal(day_date=today, description="x")
        db.subgoals.insert(sg)
        db.subgoals.delete(sg.id)

        assert db.subgoals.list_for_day(today) == []

    def test_isolated_by_day(self, db) -> None:
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        db.subgoals.insert(Subgoal(day_date=today, description="t"))
        db.subgoals.insert(Subgoal(day_date=yesterday, description="y"))

        assert len(db.subgoals.list_for_day(today)) == 1
        assert db.subgoals.list_for_day(today)[0].description == "t"
