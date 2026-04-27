"""tests/analysis/test_status_and_late_session.py"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from tracker.analysis.late_session import LateSessionAppender, _build_late_session_block
from tracker.cli.status import _compute_stats, _elapsed_since_start
from tracker.core.models import Session, SessionType, Snapshot


class TestComputeStats:
    def _make_snap(self, app: str = "Code", url: str = "", locked: bool = False, afk: bool = False) -> Snapshot:
        return Snapshot(
            session_id=1,
            timestamp=datetime.datetime.now(),
            app_name=app,
            url=url,
            is_locked=locked,
            is_afk=afk,
        )

    def test_empty_snapshots(self) -> None:
        stats = _compute_stats([])
        assert stats["deep_work_minutes"] == 0
        assert stats["drift_minutes"] == 0
        assert stats["top_apps"] == []

    def test_deep_work_app_counted(self) -> None:
        snaps = [self._make_snap("Figma") for _ in range(4)]  # 4 * 30s = 2 min
        stats = _compute_stats(snaps)
        assert stats["deep_work_minutes"] == 2

    def test_drift_url_counted(self) -> None:
        snaps = [self._make_snap("Google Chrome", url="https://youtube.com/watch?v=abc") for _ in range(2)]
        stats = _compute_stats(snaps)
        assert stats["drift_minutes"] == 1

    def test_locked_not_counted_as_deep(self) -> None:
        snaps = [self._make_snap("Code", locked=True) for _ in range(4)]
        stats = _compute_stats(snaps)
        assert stats["deep_work_minutes"] == 0
        assert stats["locked_minutes"] == 2

    def test_afk_not_counted_as_deep(self) -> None:
        snaps = [self._make_snap("Code", afk=True) for _ in range(4)]
        stats = _compute_stats(snaps)
        assert stats["deep_work_minutes"] == 0

    def test_top_apps_ordered(self) -> None:
        snaps = (
            [self._make_snap("Code") for _ in range(6)]
            + [self._make_snap("Figma") for _ in range(2)]
        )
        stats = _compute_stats(snaps)
        assert stats["top_apps"][0][0] == "Code"

    def test_longest_streak_calculation(self) -> None:
        # 5 consecutive deep work polls
        snaps = [self._make_snap("Obsidian") for _ in range(5)]
        stats = _compute_stats(snaps)
        # 5 polls × 30s = 150s / 60 = 2.5 → 2 min (integer division)
        assert stats["longest_streak_minutes"] >= 2


class TestElapsedSinceStart:
    def test_minutes_only(self) -> None:
        session = Session(
            day_date=datetime.date.today(),
            start_time=datetime.datetime.now() - datetime.timedelta(minutes=45),
        )
        result = _elapsed_since_start(session)
        assert "45m" in result or "44m" in result  # allow 1 minute tolerance

    def test_hours_and_minutes(self) -> None:
        session = Session(
            day_date=datetime.date.today(),
            start_time=datetime.datetime.now() - datetime.timedelta(hours=2, minutes=30),
        )
        result = _elapsed_since_start(session)
        assert "2h" in result
        assert "30m" in result or "29m" in result


class TestLateSessionAppender:
    def _make_report(self, tmp_path: Path) -> Path:
        p = tmp_path / "2025-04-24.html"
        p.write_text("<html><body><h1>Report</h1></body></html>")
        return p

    def test_appends_block_to_existing_report(self, tmp_path, db, test_config) -> None:
        # Create session + snapshots
        from tracker.core.models import Session
        s = Session(day_date=datetime.date(2025, 4, 24))
        db.sessions.insert(s)
        for i in range(5):
            db.snapshots.insert(Snapshot(
                session_id=s.id,
                timestamp=datetime.datetime(2025, 4, 25, 1, i, 0),
                app_name="Obsidian",
            ))

        report = self._make_report(tmp_path)
        appender = LateSessionAppender(config=test_config, db=db)
        updated = appender.append_to_report(
            late_session_id=s.id,
            day_date=datetime.date(2025, 4, 24),
            existing_report_path=str(report),
        )
        content = Path(updated).read_text()
        assert "Late session" in content
        assert "Obsidian" in content

    def test_raises_if_report_missing(self, tmp_path, db, test_config) -> None:
        from tracker.core.models import Session
        s = Session(day_date=datetime.date.today())
        db.sessions.insert(s)
        appender = LateSessionAppender(config=test_config, db=db)
        with pytest.raises(FileNotFoundError):
            appender.append_to_report(
                late_session_id=s.id,
                day_date=datetime.date.today(),
                existing_report_path="/nonexistent/report.html",
            )

    def test_raises_if_no_snapshots(self, tmp_path, db, test_config) -> None:
        from tracker.core.models import Session
        s = Session(day_date=datetime.date.today())
        db.sessions.insert(s)
        report = self._make_report(tmp_path)
        appender = LateSessionAppender(config=test_config, db=db)
        with pytest.raises(ValueError, match="No snapshots"):
            appender.append_to_report(
                late_session_id=s.id,
                day_date=datetime.date.today(),
                existing_report_path=str(report),
            )

    def test_build_late_session_block_contains_times(self) -> None:
        summary = {
            "start": "01:05",
            "end": "02:48",
            "duration_minutes": 103,
            "active_minutes": 87,
            "top_apps": "Obsidian (45m), Claude (30m)",
        }
        html = _build_late_session_block(summary, datetime.date(2025, 4, 24))
        assert "01:05" in html
        assert "02:48" in html
        assert "Late session" in html
        assert "Obsidian" in html
