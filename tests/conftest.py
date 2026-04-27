"""
tests/conftest.py

Shared fixtures for all tests.
All mocks live here — never inline in test files.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tracker.aw_client import AWStatus, AWWindowEvent, ActivityWatchClient
from tracker.config import (
    ApiConfig,
    Config,
    DaemonConfig,
    DistractionConfig,
    PathsConfig,
    PersonConfig,
    ProjectConfig,
    ScheduleConfig,
)
from tracker.db.connection import open_database
from tracker.db.repositories import Database


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def in_memory_conn() -> sqlite3.Connection:
    """Fresh in-memory SQLite connection with full schema applied."""
    conn = open_database(Path(":memory:"))
    yield conn
    conn.close()


@pytest.fixture
def db(in_memory_conn: sqlite3.Connection) -> Database:
    """Database facade backed by in-memory SQLite."""
    return Database(in_memory_conn)


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary directory for tracker data."""
    data_dir = tmp_path / "tracker_data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def test_config(tmp_data_dir: Path) -> Config:
    """Minimal Config wired to tmp directories."""
    prompts_dir = tmp_data_dir / "prompts"
    prompts_dir.mkdir()

    return Config(
        projects=[
            ProjectConfig(
                name="Fullhouse",
                description="Test project",
                keywords=["fullhouse"],
            )
        ],
        people=[
            PersonConfig(name="Saad", role="developer", project="Fullhouse"),
        ],
        distractions=DistractionConfig(
            keywords=["arsenal", "cricket"],
            domains=["cricbuzz.com"],
        ),
        schedule=ScheduleConfig(
            work_start_hour=10,
            work_end_hour=19,
            late_session_start_hour=22,
            late_session_end_hour=25,
        ),
        daemon=DaemonConfig(
            poll_interval_seconds=30,
            screenshot_interval_seconds=90,
            text_field_sample_chars=300,
            doc_poll_interval_seconds=60,
            websocket_port=27182,
        ),
        paths=PathsConfig(
            data_dir=tmp_data_dir,
            db_path=tmp_data_dir / "tracker.db",
            screenshots_dir=tmp_data_dir / "screenshots",
            reports_dir=tmp_data_dir / "reports",
            goals_dir=tmp_data_dir / "goals",
            claude_md_path=tmp_data_dir / "CLAUDE.md",
            prompts_dir=prompts_dir,
        ),
        api=ApiConfig(
            anthropic_api_key="test-key",
            anthropic_model="claude-sonnet-4-20250514",
            aw_base_url="http://localhost:5600",
        ),
    )


# ---------------------------------------------------------------------------
# AW client mock fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_aw_reachable() -> MagicMock:
    """Mock AW client that is reachable and returns a window event."""
    client = MagicMock(spec=ActivityWatchClient)
    client.check_connectivity.return_value = AWStatus(
        reachable=True, version="0.13.0", error=None
    )
    client.get_current_window.return_value = AWWindowEvent(
        timestamp=datetime.datetime.now(),
        duration_seconds=30.0,
        app="Code",
        title="daemon.py — tracker",
    )
    client.get_afk_status.return_value = False
    client.list_buckets.return_value = [
        "aw-watcher-window_testhost",
        "aw-watcher-afk_testhost",
    ]
    return client


@pytest.fixture
def mock_aw_unreachable() -> MagicMock:
    """Mock AW client that is not reachable."""
    client = MagicMock(spec=ActivityWatchClient)
    client.check_connectivity.return_value = AWStatus(
        reachable=False, version=None, error="connection refused"
    )
    client.get_current_window.return_value = None
    client.get_afk_status.return_value = False
    client.list_buckets.return_value = []
    return client


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def today() -> datetime.date:
    return datetime.date.today()


@pytest.fixture
def yesterday() -> datetime.date:
    return datetime.date.today() - datetime.timedelta(days=1)
