"""
tracker/core/models.py

All domain model dataclasses. Pure data — no I/O, no imports from other
tracker modules. The single source of truth for what a Snapshot, Session, etc. looks like.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum


class SessionType(str, Enum):
    PRIMARY = "primary"
    LATE = "late"
    BREAK = "break"


class ObservationType(str, Enum):
    MISCLASSIFICATION = "misclassification"
    NEW_APP = "new_app"
    PATTERN = "pattern"
    GOAL_MISS = "goal_miss"
    SYSTEM_ISSUE = "system_issue"


@dataclass
class Snapshot:
    session_id: int
    timestamp: datetime.datetime
    app_name: str | None = None
    window_title: str | None = None
    url: str | None = None
    page_title: str | None = None
    text_field_sample: str | None = None
    word_count: int | None = None
    word_count_delta: int | None = None
    active_file_path: str | None = None
    screenshot_path: str | None = None
    screenshot_analysed: bool = False
    screenshot_analysis: str | None = None
    is_locked: bool = False
    is_afk: bool = False
    manually_corrected: bool = False
    # Set after DB insert
    id: int | None = None


@dataclass
class Session:
    day_date: datetime.date
    session_type: SessionType = SessionType.PRIMARY
    start_time: datetime.datetime = field(
        default_factory=datetime.datetime.now
    )
    end_time: datetime.datetime | None = None
    goals_json: str | None = None
    report_path: str | None = None
    # Set after DB insert
    id: int | None = None


@dataclass
class Goal:
    day_date: datetime.date
    raw_input: str
    parsed_json: str | None = None
    # Set after DB insert
    id: int | None = None


@dataclass
class ParsedGoal:
    """A single structured goal extracted from free-text input."""
    description: str
    project: str | None
    estimated_minutes: int | None
    target_start_time: datetime.time | None


@dataclass
class Correction:
    day_date: datetime.date
    correction_note: str
    corrected_classification: str
    snapshot_id_start: int | None = None
    snapshot_id_end: int | None = None
    original_classification: str | None = None
    # Set after DB insert
    id: int | None = None


@dataclass
class Observation:
    day_date: datetime.date
    observation_type: ObservationType
    detail: str
    timestamp: datetime.datetime = field(
        default_factory=datetime.datetime.now
    )
    used_in_weekly: bool = False
    # Set after DB insert
    id: int | None = None


@dataclass
class Subgoal:
    """A small editable sub-task tied to a day's high-level goals."""
    day_date: datetime.date
    description: str
    parent_goal: str | None = None
    done: bool = False
    # Set after DB insert
    id: int | None = None


@dataclass
class Note:
    session_id: int
    note_text: str
    day_date: datetime.date
    timestamp: datetime.datetime = field(
        default_factory=datetime.datetime.now
    )
    # Set after DB insert
    id: int | None = None


@dataclass
class CollectionResult:
    """Result of a single daemon poll cycle. Never raises — errors are captured here."""
    snapshot: Snapshot | None
    error: str | None
    success: bool

    @classmethod
    def ok(cls, snapshot: Snapshot) -> CollectionResult:
        return cls(snapshot=snapshot, error=None, success=True)

    @classmethod
    def failed(cls, reason: str) -> CollectionResult:
        return cls(snapshot=None, error=reason, success=False)
