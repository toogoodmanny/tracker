"""
tracker/cli/status.py

Mid-day status summary. Called by `track status`.
Reads current session data and renders a compact terminal summary.
No LLM call — pure DB aggregation.
"""

from __future__ import annotations

import datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from tracker.core.models import Session, Snapshot
from tracker.db.repositories import Database

console = Console()


def render_status(
    session: Session,
    snapshots: list[Snapshot],
    goals_text: str | None,
    day_date: datetime.date,
) -> None:
    """
    Print a compact mid-day status to the terminal.
    Shows: time active, top apps, estimated category breakdown, goals.
    """
    if not snapshots:
        console.print("[dim]No data yet — session just started.[/dim]")
        return

    stats = _compute_stats(snapshots)
    elapsed = _elapsed_since_start(session)

    console.print()
    console.print(Panel(
        f"[bold]{day_date.strftime('%A, %d %B')}[/bold]  ·  "
        f"Session running {elapsed}  ·  "
        f"{len(snapshots)} snapshots",
        expand=False,
    ))
    console.print()

    # Key numbers
    _print_stat_row("Deep work (est.)", f"{stats['deep_work_minutes']}m", "#1d9e75")
    _print_stat_row("Drift/rabbit holes (est.)", f"{stats['drift_minutes']}m", "#e24b4a")
    _print_stat_row("Locked / away", f"{stats['locked_minutes']}m", "#888")
    _print_stat_row("Longest focus streak", f"{stats['longest_streak_minutes']}m", "#378add")

    console.print()

    # Top apps
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("App", style="")
    table.add_column("Est. time", justify="right", style="dim")
    for app, mins in stats["top_apps"][:5]:
        table.add_row(app, f"{mins}m")
    console.print(table)

    # Goals
    if goals_text:
        console.print(f"[dim]Goals:[/dim] {goals_text[:120]}{'...' if len(goals_text) > 120 else ''}")

    console.print()


def _compute_stats(snapshots: list[Snapshot]) -> dict[str, Any]:
    """Compute basic stats from raw snapshots. No LLM — heuristic only."""
    POLL_SECONDS = 30

    app_counts: dict[str, int] = {}
    locked_count = 0
    afk_count = 0

    # Simple heuristic: apps with "deep work" keywords get classified optimistically
    DEEP_WORK_APPS = {"Code", "Figma", "Obsidian", "Pages", "Word", "Microsoft Word",
                      "Claude", "ChatGPT", "Terminal", "Warp", "iTerm2"}
    DRIFT_DOMAINS = {"youtube.com", "twitter.com", "x.com", "instagram.com",
                     "cricbuzz.com", "espncricinfo.com", "reddit.com"}

    deep_polls = 0
    drift_polls = 0

    # Streak tracking
    current_streak = 0
    longest_streak = 0
    last_deep_poll = False

    for snap in snapshots:
        if snap.is_locked:
            locked_count += 1
            last_deep_poll = False
            current_streak = 0
            continue
        if snap.is_afk:
            afk_count += 1
            last_deep_poll = False
            current_streak = 0
            continue

        app = snap.app_name or "unknown"
        app_counts[app] = app_counts.get(app, 0) + 1

        is_deep = app in DEEP_WORK_APPS
        is_drift = any(d in (snap.url or "") for d in DRIFT_DOMAINS)

        if is_drift:
            drift_polls += 1
            last_deep_poll = False
            current_streak = 0
        elif is_deep:
            deep_polls += 1
            if last_deep_poll:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 1
            last_deep_poll = True
        else:
            last_deep_poll = False
            current_streak = 0

    top_apps = sorted(
        [(app, count * POLL_SECONDS // 60) for app, count in app_counts.items()],
        key=lambda x: -x[1],
    )

    return {
        "deep_work_minutes": deep_polls * POLL_SECONDS // 60,
        "drift_minutes": drift_polls * POLL_SECONDS // 60,
        "locked_minutes": locked_count * POLL_SECONDS // 60,
        "longest_streak_minutes": longest_streak * POLL_SECONDS // 60,
        "top_apps": top_apps,
    }


def _elapsed_since_start(session: Session) -> str:
    delta = datetime.datetime.now() - session.start_time
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _print_stat_row(label: str, value: str, color: str) -> None:
    console.print(f"  [dim]{label}:[/dim]  [{color}]{value}[/{color}]")
