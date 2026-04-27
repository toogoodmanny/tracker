"""tests/cli/test_session_manager.py"""

from __future__ import annotations

import datetime

import pytest

from tracker.cli.session_manager import ActiveSessionState, SessionManager
from tracker.core.models import SessionType


class TestSessionManager:
    def test_save_and_load_active_session(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        sm.save_active_session(
            session_id=42,
            day_date=datetime.date(2025, 4, 24),
            daemon_pid=12345,
        )
        state = sm.load_active_session()
        assert state is not None
        assert state.session_id == 42
        assert state.day_date == "2025-04-24"
        assert state.daemon_pid == 12345

    def test_load_returns_none_when_no_file(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        assert sm.load_active_session() is None

    def test_has_active_session_false_when_empty(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        assert sm.has_active_session() is False

    def test_has_active_session_true_after_save(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        sm.save_active_session(1, datetime.date.today(), None)
        assert sm.has_active_session() is True

    def test_clear_removes_file(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        sm.save_active_session(1, datetime.date.today(), None)
        sm.clear_active_session()
        assert sm.has_active_session() is False

    def test_clear_is_idempotent(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        sm.clear_active_session()  # no file — should not raise
        sm.clear_active_session()

    def test_save_with_session_type(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        sm.save_active_session(
            session_id=5,
            day_date=datetime.date.today(),
            daemon_pid=None,
            session_type=SessionType.LATE,
        )
        state = sm.load_active_session()
        assert state is not None
        assert state.session_type == "late"

    def test_save_with_none_pid(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        sm.save_active_session(1, datetime.date.today(), daemon_pid=None)
        state = sm.load_active_session()
        assert state is not None
        assert state.daemon_pid is None

    def test_malformed_state_file_raises_value_error(self, tmp_path) -> None:
        sm = SessionManager(tmp_path)
        state_path = tmp_path / "session.json"
        state_path.write_text("{not valid json}")
        with pytest.raises(ValueError, match="Malformed"):
            sm.load_active_session()

    def test_creates_data_dir_if_missing(self, tmp_path) -> None:
        nested = tmp_path / "a" / "b" / "tracker"
        sm = SessionManager(nested)
        sm.save_active_session(1, datetime.date.today(), None)
        assert (nested / "session.json").exists()
