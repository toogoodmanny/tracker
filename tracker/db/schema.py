"""
tracker/db/schema.py

All SQL DDL lives here. No SQL anywhere else in the codebase.
Schema version is tracked in the `schema_version` table.
"""

from __future__ import annotations

CURRENT_VERSION = 3

# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    session_type    TEXT NOT NULL DEFAULT 'primary',
    day_date        TEXT NOT NULL,
    goals_json      TEXT,
    report_path     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS snapshots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id              INTEGER NOT NULL REFERENCES sessions(id),
    timestamp               TEXT NOT NULL,
    app_name                TEXT,
    window_title            TEXT,
    url                     TEXT,
    page_title              TEXT,
    text_field_sample       TEXT,
    word_count              INTEGER,
    word_count_delta        INTEGER,
    active_file_path        TEXT,
    screenshot_path         TEXT,
    screenshot_analysed     INTEGER NOT NULL DEFAULT 0,
    screenshot_analysis     TEXT,
    is_locked               INTEGER NOT NULL DEFAULT 0,
    is_afk                  INTEGER NOT NULL DEFAULT 0,
    manually_corrected      INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_GOALS = """
CREATE TABLE IF NOT EXISTS goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    day_date        TEXT NOT NULL UNIQUE,
    raw_input       TEXT NOT NULL,
    parsed_json     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_CORRECTIONS = """
CREATE TABLE IF NOT EXISTS corrections (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    day_date                    TEXT NOT NULL,
    snapshot_id_start           INTEGER,
    snapshot_id_end             INTEGER,
    original_classification     TEXT,
    corrected_classification    TEXT NOT NULL,
    correction_note             TEXT NOT NULL,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL DEFAULT (datetime('now')),
    day_date            TEXT NOT NULL,
    observation_type    TEXT NOT NULL,
    detail              TEXT NOT NULL,
    used_in_weekly      INTEGER NOT NULL DEFAULT 0
);
"""

CREATE_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    note_text   TEXT NOT NULL,
    day_date    TEXT NOT NULL
);
"""

CREATE_SUBGOALS = """
CREATE TABLE IF NOT EXISTS subgoals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day_date    TEXT NOT NULL,
    parent_goal TEXT,
    description TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_DAILY_FEEDBACK = """
CREATE TABLE IF NOT EXISTS daily_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    day_date        TEXT NOT NULL,
    score_override  REAL,
    reasoning       TEXT,
    other_notes     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Index definitions
# ---------------------------------------------------------------------------

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_day ON snapshots(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day_date);",
    "CREATE INDEX IF NOT EXISTS idx_goals_day ON goals(day_date);",
    "CREATE INDEX IF NOT EXISTS idx_corrections_day ON corrections(day_date);",
    "CREATE INDEX IF NOT EXISTS idx_observations_day ON observations(day_date);",
    "CREATE INDEX IF NOT EXISTS idx_observations_weekly ON observations(used_in_weekly);",
    "CREATE INDEX IF NOT EXISTS idx_subgoals_day ON subgoals(day_date);",
]

# Ordered list of all CREATE statements for the migration runner
ALL_CREATE_STATEMENTS: list[str] = [
    CREATE_SCHEMA_VERSION,
    CREATE_SESSIONS,
    CREATE_SNAPSHOTS,
    CREATE_GOALS,
    CREATE_CORRECTIONS,
    CREATE_OBSERVATIONS,
    CREATE_NOTES,
    CREATE_SUBGOALS,
    CREATE_DAILY_FEEDBACK,
    *INDEXES,
]
