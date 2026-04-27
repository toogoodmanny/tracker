"""
tracker/analysis/patterns.py

Conservative pattern discovery engine.
Runs weekly only. Only surfaces patterns with evidence across 3+ days.
Never invents patterns — every finding cites specific data.

Design principle: ADHD brains don't need more noise. Only flag things
that are real, recurring, and actionable.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any


@dataclass
class DiscoveredPattern:
    """A pattern found in weekly data. Must have evidence from 3+ occurrences."""
    pattern_type: str          # timing, trigger, recovery, work_type
    description: str
    evidence: list[str]        # specific data points supporting this
    severity: str              # critical, notable, minor
    suggestion: str            # one concrete change


def discover_weekly_patterns(
    daily_analyses: list[dict[str, Any]],
) -> list[DiscoveredPattern]:
    """
    Analyse a list of daily analysis dicts and return discovered patterns.
    Only returns patterns with 3+ occurrences. Returns empty list if data is sparse.
    Pure function — no I/O, no DB access.
    """
    if len(daily_analyses) < 3:
        return []

    patterns: list[DiscoveredPattern] = []

    patterns.extend(_find_timing_patterns(daily_analyses))
    patterns.extend(_find_trigger_patterns(daily_analyses))
    patterns.extend(_find_ai_chat_patterns(daily_analyses))
    patterns.extend(_find_video_patterns(daily_analyses))
    patterns.extend(_find_goal_accuracy_patterns(daily_analyses))

    return patterns


def _find_timing_patterns(
    analyses: list[dict[str, Any]],
) -> list[DiscoveredPattern]:
    """Find patterns in when focus happens vs when drift happens."""
    patterns = []

    # Post-lunch drift: check if drift events cluster in 12:00-14:00 window
    post_lunch_drift_days: list[str] = []
    for day in analyses:
        for trigger in day.get("drift_triggers", []):
            t = trigger.get("time", "")
            if t and "12:" in t or "13:" in t or "14:" in t:
                post_lunch_drift_days.append(day.get("date", "unknown"))
                break

    if len(post_lunch_drift_days) >= 3:
        patterns.append(DiscoveredPattern(
            pattern_type="timing",
            description="Post-lunch is consistently a dead zone — drift clusters between 12:00-14:00",
            evidence=[f"Drift after lunch on: {', '.join(post_lunch_drift_days[:5])}"],
            severity="notable",
            suggestion="Block 12:30-14:00 explicitly as a low-intensity window. Schedule reviews, admin, or a real break — not deep work.",
        ))

    # Late start pattern: track when first deep work block begins
    late_start_days: list[str] = []
    for day in analyses:
        timeline = day.get("timeline", [])
        first_deep = next(
            (b for b in timeline if b.get("category") == "deep_work"),
            None,
        )
        if first_deep:
            start = first_deep.get("start_time", "")
            if start and start >= "11:30":
                late_start_days.append(day.get("date", ""))

    if len(late_start_days) >= 3:
        patterns.append(DiscoveredPattern(
            pattern_type="timing",
            description="First deep work block starts after 11:30 most days — morning is being lost to warmup drift",
            evidence=[f"Late deep work start on: {', '.join(late_start_days[:5])}"],
            severity="critical",
            suggestion="Protect 10:00-11:30 as a sacred deep work block. No browser, no chat. Start the day directly in the main task.",
        ))

    return patterns


def _find_trigger_patterns(
    analyses: list[dict[str, Any]],
) -> list[DiscoveredPattern]:
    """Find recurring drift triggers."""
    trigger_counts: dict[str, list[str]] = {}

    for day in analyses:
        date = day.get("date", "")
        for trigger in day.get("drift_triggers", []):
            raw = trigger.get("trigger", "").lower()
            # Bucket into trigger categories
            for keyword, bucket in [
                ("youtube", "YouTube"),
                ("ipl", "Cricket/IPL"),
                ("cricket", "Cricket/IPL"),
                ("arsenal", "Arsenal/Football"),
                ("football", "Arsenal/Football"),
                ("twitter", "Twitter/X"),
                ("instagram", "Instagram"),
                ("reddit", "Reddit"),
                ("score", "Score checking"),
                ("bike", "Bikes/Motorcycles"),
                ("claude", "Unrelated AI chat"),
                ("chatgpt", "Unrelated AI chat"),
                ("gemini", "Unrelated AI chat"),
            ]:
                if keyword in raw:
                    if bucket not in trigger_counts:
                        trigger_counts[bucket] = []
                    trigger_counts[bucket].append(date)
                    break

    patterns = []
    for trigger, dates in trigger_counts.items():
        if len(dates) >= 3:
            severity = "critical" if len(dates) >= 5 else "notable"
            patterns.append(DiscoveredPattern(
                pattern_type="trigger",
                description=f"{trigger} is a recurring drift trigger — appeared {len(dates)} times this week",
                evidence=[f"Triggered on: {', '.join(sorted(set(dates))[:5])}"],
                severity=severity,
                suggestion=f"Use a site blocker (like Cold Turkey or 1Blocker) to block {trigger} during work hours. Make it require friction to access.",
            ))

    return patterns


def _find_ai_chat_patterns(
    analyses: list[dict[str, Any]],
) -> list[DiscoveredPattern]:
    """Find patterns in AI chat usage — productive vs rabbit hole."""
    patterns = []

    off_goal_days: list[str] = []
    total_ai_minutes = 0
    total_off_goal_minutes = 0

    for day in analyses:
        ai_total = day.get("ai_chat_minutes", 0)
        ai_on_goal = day.get("ai_chat_on_goal_minutes", 0)
        ai_off = ai_total - ai_on_goal
        total_ai_minutes += ai_total
        total_off_goal_minutes += ai_off
        if ai_total > 0 and ai_off / ai_total > 0.4:
            off_goal_days.append(day.get("date", ""))

    if total_ai_minutes > 0:
        off_pct = round(total_off_goal_minutes / total_ai_minutes * 100)
        if off_pct > 35 and len(off_goal_days) >= 3:
            patterns.append(DiscoveredPattern(
                pattern_type="ai_chat",
                description=f"{off_pct}% of AI chat time this week was off-goal — AI tools are becoming a distraction vector",
                evidence=[
                    f"Off-goal days: {', '.join(off_goal_days)}",
                    f"Total AI time: {round(total_ai_minutes/60, 1)}h, off-goal: {round(total_off_goal_minutes/60, 1)}h",
                ],
                severity="critical" if off_pct > 50 else "notable",
                suggestion="Before opening Claude/ChatGPT, write one sentence: what specific question am I answering right now? If you can't answer it, close the tab.",
            ))

    return patterns


def _find_video_patterns(
    analyses: list[dict[str, Any]],
) -> list[DiscoveredPattern]:
    """Find patterns in YouTube/video consumption."""
    patterns = []

    total_videos = sum(d.get("video_count", 0) for d in analyses)
    high_video_days = [d.get("date", "") for d in analyses if d.get("video_count", 0) >= 4]

    if len(high_video_days) >= 3:
        avg_videos = round(total_videos / len(analyses), 1)
        patterns.append(DiscoveredPattern(
            pattern_type="video",
            description=f"High video consumption pattern — averaging {avg_videos} videos/day during work hours",
            evidence=[
                f"4+ videos on: {', '.join(high_video_days[:5])}",
                f"Total this week: {total_videos} videos",
            ],
            severity="critical" if avg_videos >= 6 else "notable",
            suggestion="YouTube is your biggest time sink. Close the tab after one video. Never let autoplay run.",
        ))

    return patterns


def _find_goal_accuracy_patterns(
    analyses: list[dict[str, Any]],
) -> list[DiscoveredPattern]:
    """Find patterns in goal-setting accuracy."""
    patterns = []

    missed_goals: list[str] = []
    for day in analyses:
        goals = day.get("goals_comparison", [])
        missed = [g for g in goals if g.get("status") in ("missed", "not_started")]
        if len(missed) >= 2:
            missed_goals.append(day.get("date", ""))

    if len(missed_goals) >= 3:
        patterns.append(DiscoveredPattern(
            pattern_type="goal_accuracy",
            description="You consistently set more goals than you complete — 2+ missed goals on most days",
            evidence=[f"Multiple misses on: {', '.join(missed_goals[:5])}"],
            severity="notable",
            suggestion="Cap daily goals at 3 items maximum. You're over-planning and under-executing.",
        ))

    return patterns


def suggest_category_updates(
    corrections: list[dict[str, Any]],
    current_categories: list[str],
) -> list[dict[str, str]]:
    """
    Suggest additions, removals, or splits to the category system
    based on corrections made during the week.
    Returns list of suggestions, each with 'action' and 'reason'.
    Pure function.
    """
    suggestions = []

    # Count correction frequency
    correction_texts = [c.get("correction_note", "").lower() for c in corrections]

    # Look for repeated correction themes
    theme_counts: dict[str, int] = {}
    for text in correction_texts:
        for theme in ["meeting", "research", "admin", "planning", "reading", "writing"]:
            if theme in text:
                theme_counts[theme] = theme_counts.get(theme, 0) + 1

    for theme, count in theme_counts.items():
        if count >= 3 and theme not in [c.lower() for c in current_categories]:
            suggestions.append({
                "action": "add",
                "category": theme,
                "reason": f"Corrected {count} times this week — needs its own category",
            })

    return suggestions
