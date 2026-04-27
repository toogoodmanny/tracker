"""
tracker/analysis/late_session.py

Handles late-night work sessions that happen after the primary session
has been closed with `track end`.

When the user opens their laptop after `track end`, the daemon detects
the resume and offers to append a "late session" block to today's report
without regenerating the full LLM analysis.

A late session produces a short summary appended to the existing HTML report.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any

from tracker.config import Config
from tracker.core.models import Session, SessionType, Snapshot
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)


class LateSessionAppender:
    """
    Appends a late session block to an existing daily HTML report.
    Called when `track end` is run after midnight, or when `track sleep`
    is followed by more work the same night.
    """

    def __init__(self, config: Config, db: Database) -> None:
        self._config = config
        self._db = db

    def append_to_report(
        self,
        late_session_id: int,
        day_date: datetime.date,
        existing_report_path: str,
    ) -> str:
        """
        Generate a short late-session summary and append it to the existing report.
        Returns the updated report path.
        Raises FileNotFoundError if existing report doesn't exist.
        Raises ValueError if no snapshots found for session.
        """
        report_path = Path(existing_report_path)
        if not report_path.exists():
            raise FileNotFoundError(f"Report not found: {existing_report_path}")

        snapshots = self._db.snapshots.get_by_session(late_session_id)
        if not snapshots:
            raise ValueError(f"No snapshots for session {late_session_id}")

        summary = self._summarise_late_session(snapshots)
        block_html = _build_late_session_block(summary, day_date)

        # Inject before </body>
        existing = report_path.read_text(encoding="utf-8")
        if "</body>" in existing:
            updated = existing.replace("</body>", block_html + "\n</body>")
        else:
            updated = existing + block_html

        report_path.write_text(updated, encoding="utf-8")
        logger.info("Late session appended to %s", existing_report_path)
        return existing_report_path

    def _summarise_late_session(
        self, snapshots: list[Snapshot]
    ) -> dict[str, Any]:
        """Compute basic stats for a late session without LLM call."""
        if not snapshots:
            return {}

        start = snapshots[0].timestamp
        end = snapshots[-1].timestamp
        duration_minutes = int((end - start).total_seconds() / 60)

        # Count by app
        app_times: dict[str, int] = {}
        for snap in snapshots:
            app = snap.app_name or "unknown"
            app_times[app] = app_times.get(app, 0) + 1

        # Convert poll count to approximate minutes (30s per poll)
        top_apps = sorted(app_times.items(), key=lambda x: -x[1])[:3]
        top_apps_str = ", ".join(
            f"{app} ({count * 30 // 60}m)" for app, count in top_apps
        )

        locked_polls = sum(1 for s in snapshots if s.is_locked)
        active_minutes = duration_minutes - (locked_polls * 30 // 60)

        return {
            "start": start.strftime("%H:%M"),
            "end": end.strftime("%H:%M"),
            "duration_minutes": duration_minutes,
            "active_minutes": active_minutes,
            "top_apps": top_apps_str,
            "snapshot_count": len(snapshots),
        }


def _build_late_session_block(summary: dict[str, Any], day_date: datetime.date) -> str:
    start = summary.get("start", "?")
    end = summary.get("end", "?")
    duration = summary.get("duration_minutes", 0)
    active = summary.get("active_minutes", 0)
    apps = summary.get("top_apps", "unknown")

    return f"""
<div style="max-width:900px;margin:1rem auto;background:#fff;border:0.5px solid #e0e0d8;
            border-radius:8px;padding:1.25rem 1.5rem;border-left:3px solid #7f77dd;">
  <h2 style="font-size:1rem;font-weight:500;color:#555;margin-bottom:0.75rem">
    Late session — {start}–{end}
  </h2>
  <p style="font-size:0.85rem;color:#666;line-height:1.6">
    {duration} minutes total &nbsp;·&nbsp; ~{active} minutes active &nbsp;·&nbsp;
    Apps: {apps}
  </p>
  <p style="font-size:0.75rem;color:#aaa;margin-top:0.5rem">
    No full analysis generated for late sessions. Run <code>track end --full</code> to analyse.
  </p>
</div>"""
