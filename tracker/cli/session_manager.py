"""
tracker/cli/session_manager.py

Manages session lifecycle: creating, closing, pausing sessions.
Reads/writes a small state file (~/.tracker/session.json) to survive
CLI process boundaries (daemon runs in background).
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from tracker.core.models import Session, SessionType

logger = logging.getLogger(__name__)

_STATE_FILENAME = "session.json"


@dataclass
class ActiveSessionState:
    """Persisted across CLI calls via session.json."""
    session_id: int
    day_date: str          # ISO format
    daemon_pid: int | None
    session_type: str


class SessionManager:
    """
    Reads and writes the active session state file.
    This allows `track end` to know which session to close even though
    the daemon runs in a separate process.
    """

    def __init__(self, data_dir: Path) -> None:
        self._state_path = data_dir / _STATE_FILENAME
        data_dir.mkdir(parents=True, exist_ok=True)

    def save_active_session(
        self,
        session_id: int,
        day_date: datetime.date,
        daemon_pid: int | None,
        session_type: SessionType = SessionType.PRIMARY,
    ) -> None:
        """
        Persist the active session state to disk.
        Raises OSError if the file cannot be written.
        """
        state = ActiveSessionState(
            session_id=session_id,
            day_date=day_date.isoformat(),
            daemon_pid=daemon_pid,
            session_type=session_type.value,
        )
        with self._state_path.open("w") as fh:
            json.dump(asdict(state), fh, indent=2)
        logger.debug("Saved session state: session_id=%d", session_id)

    def load_active_session(self) -> ActiveSessionState | None:
        """
        Load the active session state.
        Returns None if no session is active (file missing).
        Raises ValueError if the file is malformed.
        """
        if not self._state_path.exists():
            return None

        try:
            with self._state_path.open() as fh:
                raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed session state file: {exc}") from exc

        return ActiveSessionState(
            session_id=raw["session_id"],
            day_date=raw["day_date"],
            daemon_pid=raw.get("daemon_pid"),
            session_type=raw.get("session_type", "primary"),
        )

    def clear_active_session(self) -> None:
        """
        Remove the session state file.
        Silent if the file doesn't exist.
        Raises OSError if the file exists but cannot be deleted.
        """
        if self._state_path.exists():
            self._state_path.unlink()
            logger.debug("Cleared session state")

    def has_active_session(self) -> bool:
        return self._state_path.exists()
