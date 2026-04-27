"""tests/analysis/test_patterns.py"""

from __future__ import annotations

import pytest

from tracker.analysis.patterns import (
    DiscoveredPattern,
    discover_weekly_patterns,
    suggest_category_updates,
)


def _make_day(
    date: str = "2025-04-21",
    score: float = 5.0,
    drift_triggers: list | None = None,
    timeline: list | None = None,
    video_count: int = 0,
    ai_chat_minutes: int = 0,
    ai_chat_on_goal_minutes: int = 0,
    goals_comparison: list | None = None,
) -> dict:
    return {
        "date": date,
        "day_score": score,
        "drift_triggers": drift_triggers or [],
        "timeline": timeline or [],
        "video_count": video_count,
        "ai_chat_minutes": ai_chat_minutes,
        "ai_chat_on_goal_minutes": ai_chat_on_goal_minutes,
        "goals_comparison": goals_comparison or [],
    }


class TestDiscoverWeeklyPatterns:
    def test_returns_empty_for_sparse_data(self) -> None:
        patterns = discover_weekly_patterns([_make_day(), _make_day()])
        assert patterns == []

    def test_finds_post_lunch_drift(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                drift_triggers=[{"time": "13:15", "trigger": "opened YouTube"}],
            )
            for i in range(3)
        ]
        patterns = discover_weekly_patterns(days)
        assert any("post-lunch" in p.description.lower() or "12:00" in p.description for p in patterns)

    def test_finds_youtube_trigger_pattern(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                drift_triggers=[{"time": "10:30", "trigger": "opened youtube for music"}],
            )
            for i in range(4)
        ]
        patterns = discover_weekly_patterns(days)
        assert any("YouTube" in p.description for p in patterns)

    def test_finds_arsenal_trigger_pattern(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                drift_triggers=[{"time": "11:00", "trigger": "searched arsenal match result"}],
            )
            for i in range(3)
        ]
        patterns = discover_weekly_patterns(days)
        assert any("Arsenal" in p.description or "Football" in p.description for p in patterns)

    def test_finds_ai_chat_off_goal_pattern(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                ai_chat_minutes=60,
                ai_chat_on_goal_minutes=10,  # 83% off-goal
            )
            for i in range(3)
        ]
        patterns = discover_weekly_patterns(days)
        assert any("AI" in p.description or "ai_chat" in p.pattern_type for p in patterns)

    def test_no_pattern_when_ai_on_goal(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                ai_chat_minutes=60,
                ai_chat_on_goal_minutes=55,  # 92% on-goal
            )
            for i in range(3)
        ]
        patterns = discover_weekly_patterns(days)
        ai_patterns = [p for p in patterns if "AI" in p.description]
        assert len(ai_patterns) == 0

    def test_finds_high_video_consumption(self) -> None:
        days = [
            _make_day(date=f"2025-04-2{i}", video_count=6)
            for i in range(3)
        ]
        patterns = discover_weekly_patterns(days)
        assert any("video" in p.pattern_type or "video" in p.description.lower() for p in patterns)

    def test_finds_late_start_pattern(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                timeline=[
                    {"category": "drift", "start_time": "10:00"},
                    {"category": "deep_work", "start_time": "12:00"},
                ],
            )
            for i in range(3)
        ]
        patterns = discover_weekly_patterns(days)
        assert any("11:30" in p.description or "morning" in p.description.lower() for p in patterns)

    def test_finds_over_planning_pattern(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                goals_comparison=[
                    {"status": "missed"},
                    {"status": "not_started"},
                    {"status": "done"},
                ],
            )
            for i in range(3)
        ]
        patterns = discover_weekly_patterns(days)
        assert any("goal" in p.pattern_type or "goal" in p.description.lower() for p in patterns)

    def test_all_patterns_have_required_fields(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                drift_triggers=[{"time": "13:00", "trigger": "youtube rabbit hole"}],
                video_count=5,
                ai_chat_minutes=60,
                ai_chat_on_goal_minutes=5,
            )
            for i in range(4)
        ]
        patterns = discover_weekly_patterns(days)
        for p in patterns:
            assert p.description
            assert p.evidence
            assert p.severity in ("critical", "notable", "minor")
            assert p.suggestion
            assert p.pattern_type

    def test_severity_critical_for_frequent_triggers(self) -> None:
        days = [
            _make_day(
                date=f"2025-04-2{i}",
                drift_triggers=[{"time": "10:00", "trigger": "arsenal score check"}],
            )
            for i in range(6)
        ]
        patterns = discover_weekly_patterns(days)
        arsenal_pattern = next(
            (p for p in patterns if "Arsenal" in p.description or "Football" in p.description),
            None,
        )
        if arsenal_pattern:
            assert arsenal_pattern.severity == "critical"


class TestSuggestCategoryUpdates:
    def test_suggests_new_category_for_frequent_correction(self) -> None:
        corrections = [
            {"correction_note": "this was a planning session not deep work"}
            for _ in range(4)
        ]
        suggestions = suggest_category_updates(corrections, current_categories=["deep_work", "drift"])
        assert any(s["action"] == "add" and "planning" in s["category"] for s in suggestions)

    def test_no_suggestion_if_category_exists(self) -> None:
        corrections = [
            {"correction_note": "this was a meeting not deep work"}
            for _ in range(4)
        ]
        suggestions = suggest_category_updates(
            corrections,
            current_categories=["deep_work", "meeting", "drift"],
        )
        meeting_adds = [s for s in suggestions if "meeting" in s.get("category", "")]
        assert len(meeting_adds) == 0

    def test_no_suggestion_for_sparse_corrections(self) -> None:
        corrections = [{"correction_note": "this was reading"}, {"correction_note": "this was reading"}]
        suggestions = suggest_category_updates(corrections, current_categories=["deep_work"])
        assert suggestions == []

    def test_empty_corrections_returns_empty(self) -> None:
        assert suggest_category_updates([], ["deep_work"]) == []
