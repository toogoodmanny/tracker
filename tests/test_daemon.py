"""tests/test_daemon.py"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from tracker.aw_client import AWWindowEvent
from tracker.core.models import CollectionResult, Session, SessionType
from tracker.daemon import Daemon


class TestDaemonPollOnce:
    def _make_daemon(self, db, test_config, mock_aw) -> tuple[Daemon, int]:
        session = Session(day_date=datetime.date.today())
        session_id = db.sessions.insert(session)
        daemon = Daemon(
            config=test_config,
            db=db,
            aw=mock_aw,
            session_id=session_id,
        )
        return daemon, session_id

    def test_poll_once_returns_success_with_aw_data(
        self, db, test_config, mock_aw_reachable
    ) -> None:
        daemon, _ = self._make_daemon(db, test_config, mock_aw_reachable)
        result = daemon._poll_once()
        assert result.success is True
        assert result.snapshot is not None
        assert result.snapshot.app_name == "Code"
        assert result.snapshot.window_title == "daemon.py — tracker"

    def test_poll_once_returns_failure_when_aw_down(
        self, db, test_config, mock_aw_unreachable
    ) -> None:
        daemon, _ = self._make_daemon(db, test_config, mock_aw_unreachable)
        result = daemon._poll_once()
        # AW down returns a snapshot with no window data, not a failure
        # (AW being down is expected during startup)
        assert result.snapshot is not None
        assert result.snapshot.app_name is None

    def test_poll_once_snapshot_has_correct_session_id(
        self, db, test_config, mock_aw_reachable
    ) -> None:
        daemon, session_id = self._make_daemon(db, test_config, mock_aw_reachable)
        result = daemon._poll_once()
        assert result.snapshot is not None
        assert result.snapshot.session_id == session_id

    def test_poll_once_snapshot_has_timestamp(
        self, db, test_config, mock_aw_reachable
    ) -> None:
        daemon, _ = self._make_daemon(db, test_config, mock_aw_reachable)
        result = daemon._poll_once()
        assert result.snapshot is not None
        assert isinstance(result.snapshot.timestamp, datetime.datetime)

    def test_poll_once_captures_afk_status(
        self, db, test_config, mock_aw_reachable
    ) -> None:
        mock_aw_reachable.get_afk_status.return_value = True
        daemon, _ = self._make_daemon(db, test_config, mock_aw_reachable)
        result = daemon._poll_once()
        assert result.snapshot is not None
        assert result.snapshot.is_afk is True


class TestDaemonObservations:
    def _make_daemon(self, db, test_config, mock_aw) -> tuple[Daemon, int]:
        session = Session(day_date=datetime.date.today())
        session_id = db.sessions.insert(session)
        daemon = Daemon(
            config=test_config,
            db=db,
            aw=mock_aw,
            session_id=session_id,
        )
        daemon._poll_count = 10  # past startup threshold
        return daemon, session_id

    def test_new_app_observation_filed(
        self, db, test_config, mock_aw_reachable
    ) -> None:
        from tracker.core.models import Snapshot
        daemon, _ = self._make_daemon(db, test_config, mock_aw_reachable)
        snap = Snapshot(
            session_id=daemon._session_id,
            timestamp=datetime.datetime.now(),
            app_name="NewUnknownApp",
        )
        daemon._check_for_observations(snap)
        today = datetime.date.today()
        obs = db.observations.get_unused_for_weekly(today)
        assert any("NewUnknownApp" in o.detail for o in obs)

    def test_known_app_no_observation(
        self, db, test_config, mock_aw_reachable
    ) -> None:
        from tracker.core.models import Snapshot
        daemon, _ = self._make_daemon(db, test_config, mock_aw_reachable)
        daemon._seen_apps.add("Code")  # mark as already seen
        snap = Snapshot(
            session_id=daemon._session_id,
            timestamp=datetime.datetime.now(),
            app_name="Code",
        )
        daemon._check_for_observations(snap)
        today = datetime.date.today()
        obs = db.observations.get_unused_for_weekly(today)
        assert not any("Code" in o.detail for o in obs)

    def test_observation_check_never_raises(
        self, db, test_config, mock_aw_reachable
    ) -> None:
        from tracker.core.models import Snapshot
        daemon, _ = self._make_daemon(db, test_config, mock_aw_reachable)
        # Poison the observations repo to trigger internal error
        db.observations.insert = MagicMock(side_effect=RuntimeError("db error"))
        snap = Snapshot(
            session_id=daemon._session_id,
            timestamp=datetime.datetime.now(),
            app_name="SomeApp",
        )
        # Must not raise — observation errors must be swallowed
        daemon._check_for_observations(snap)


class TestCollectionResult:
    def test_ok_result(self) -> None:
        from tracker.core.models import Snapshot
        snap = Snapshot(
            session_id=1, timestamp=datetime.datetime.now()
        )
        result = CollectionResult.ok(snap)
        assert result.success is True
        assert result.snapshot is snap
        assert result.error is None

    def test_failed_result(self) -> None:
        result = CollectionResult.failed("AW unreachable")
        assert result.success is False
        assert result.snapshot is None
        assert result.error == "AW unreachable"
