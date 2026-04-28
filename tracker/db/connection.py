"""
tracker/db/connection.py

Database connection management and migration runner.
Returns a sqlite3.Connection; never exposes raw SQL outside this package.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from tracker.db.schema import ALL_CREATE_STATEMENTS, CURRENT_VERSION

logger = logging.getLogger(__name__)


def open_database(db_path: Path) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database at db_path.
    Runs schema migrations before returning.
    Enables WAL mode and foreign key enforcement.
    Raises OSError if the parent directory cannot be created.
    Raises sqlite3.DatabaseError on schema migration failure.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _run_migrations(conn)
    except sqlite3.DatabaseError as exc:
        conn.close()
        logger.error("Failed to initialise database at %s: %s", db_path, exc)
        raise

    logger.debug("Database opened at %s (version %d)", db_path, CURRENT_VERSION)
    return conn


def close_database(conn: sqlite3.Connection) -> None:
    """Close the database connection cleanly."""
    try:
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("Error closing database connection: %s", exc)


def _run_migrations(conn: sqlite3.Connection) -> None:
    """
    Apply schema if not already present.
    Uses a simple version table; extend with numbered migration files later.
    """
    with conn:
        for statement in ALL_CREATE_STATEMENTS:
            conn.execute(statement)

        current = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()

        current_version: int = current["v"] if current["v"] is not None else 0

        if current_version < CURRENT_VERSION:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (CURRENT_VERSION,),
            )
            logger.info(
                "Schema migrated from version %d to %d",
                current_version,
                CURRENT_VERSION,
            )
