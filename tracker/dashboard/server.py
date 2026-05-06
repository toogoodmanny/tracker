"""
tracker/dashboard/server.py

Tiny localhost HTTP server that exposes the live tracker state and lets
the user edit goals/subgoals from a browser.

Endpoints
  GET  /                   single-page HTML dashboard
  GET  /api/state          JSON snapshot of today's session for polling
  POST /api/goals          {"raw_input": "..."} -> upsert today's goals
  POST /api/subgoals       {"description": "...", "parent_goal": "..."} -> insert
  PATCH /api/subgoals/<id> {"done": true} or {"description": "..."}
  DELETE /api/subgoals/<id>

Bind: 127.0.0.1:<port> only — never exposed beyond the local machine.

The server holds its own DB handle (sqlite3 with check_same_thread=False)
so it can be queried while the daemon is also writing.
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tracker.config import Config
from tracker.core.models import Goal, Subgoal
from tracker.db.connection import open_database
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)

DEFAULT_PORT = 27183  # one above the websocket port (27182)

_INDEX_HTML_PATH = Path(__file__).parent / "index.html"


def _build_timeline_blocks(snapshots: list) -> list[dict[str, Any]]:
    """
    Collapse consecutive same-app snapshots into a single block.
    A block ends when the app changes (or the snapshot is locked/AFK).

    Each block has:
      start_time, end_time, duration_minutes, app, titles (unique, top 3),
      url_count, text_capture_count, screenshot_count
    """
    if not snapshots:
        return []

    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for s in snapshots:
        # Treat locked/AFK as their own pseudo-app so they break the chain.
        app_key = "Locked" if s.is_locked else ("AFK" if s.is_afk else (s.app_name or "unknown"))

        if current is None or current["app"] != app_key:
            if current is not None:
                blocks.append(current)
            current = {
                "app": app_key,
                "start_time": s.timestamp,
                "end_time": s.timestamp,
                "titles": [],
                "url_count": 0,
                "text_capture_count": 0,
                "screenshot_count": 0,
            }
        current["end_time"] = s.timestamp
        if s.window_title and s.window_title not in current["titles"]:
            current["titles"].append(s.window_title)
        if s.url:
            current["url_count"] += 1
        if s.text_field_sample:
            current["text_capture_count"] += 1
        if s.screenshot_path:
            current["screenshot_count"] += 1

    if current is not None:
        blocks.append(current)

    # Format for JSON
    out: list[dict[str, Any]] = []
    for b in blocks:
        duration = (b["end_time"] - b["start_time"]).total_seconds() / 60
        out.append({
            "app": b["app"],
            "start": b["start_time"].strftime("%H:%M"),
            "end": b["end_time"].strftime("%H:%M"),
            "duration_minutes": max(1, round(duration)),
            "titles": b["titles"][:3],
            "url_count": b["url_count"],
            "text_capture_count": b["text_capture_count"],
            "screenshot_count": b["screenshot_count"],
        })
    return out


def _split_goal_lines(raw: str) -> list[str]:
    """Split the user's free-text goal blob into one main goal per non-empty line."""
    if not raw:
        return []
    lines = [ln.strip(" \t-•*") for ln in raw.splitlines()]
    return [ln for ln in lines if ln]


def _serialise_state(db: Database, day_date: datetime.date) -> dict[str, Any]:
    sessions = db.sessions.get_by_day(day_date)
    active = next((s for s in sessions if s.end_time is None), None)

    all_snapshots = []
    if active and active.id is not None:
        all_snapshots = db.snapshots.get_by_session(active.id)

    timeline_blocks = _build_timeline_blocks(all_snapshots)

    goal = db.goals.get_for_day(day_date)
    raw_goal = goal.raw_input if goal else ""
    main_goals = _split_goal_lines(raw_goal)

    # Group subgoals by parent_goal. Subgoals whose parent doesn't match any
    # current main goal are bucketed under "" (rendered as "Other").
    subgoal_rows = db.subgoals.list_for_day(day_date)
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for sg in subgoal_rows:
        key = sg.parent_goal if (sg.parent_goal and sg.parent_goal in main_goals) else ""
        by_parent.setdefault(key, []).append({
            "id": sg.id,
            "description": sg.description,
            "done": sg.done,
        })

    goals_with_subs = [
        {"goal": g, "subgoals": by_parent.get(g, [])}
        for g in main_goals
    ]
    if by_parent.get("") and main_goals:
        goals_with_subs.append({"goal": "", "subgoals": by_parent[""]})
    elif by_parent.get("") and not main_goals:
        # No main goals at all — surface orphans so they're not invisible
        goals_with_subs.append({"goal": "", "subgoals": by_parent[""]})

    return {
        "day": day_date.isoformat(),
        "session_active": active is not None,
        "session_id": active.id if active else None,
        "started_at": active.start_time.isoformat() if active else None,
        "snapshot_count": len(all_snapshots),
        "goal_raw": raw_goal,
        "main_goals": main_goals,
        "goals_with_subs": goals_with_subs,
        "timeline_blocks": timeline_blocks,
    }


class _Handler(BaseHTTPRequestHandler):
    # Filled in by build_handler()
    config: Config = None  # type: ignore[assignment]

    # Silence default access logging — too noisy with 5s polling.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("dashboard: " + format, *args)

    def _open_db(self) -> Database:
        conn = open_database(self.config.paths.db_path)
        return Database(conn)

    def _send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Allow the report HTML (opened as file://) to POST feedback here
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Pre-flight CORS for file:// report pages POSTing feedback."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ----- routing -----

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path == "/index.html":
            try:
                html = _INDEX_HTML_PATH.read_text(encoding="utf-8")
            except OSError as exc:
                self._send_json(500, {"error": f"index missing: {exc}"})
                return
            self._send_html(200, html)
            return

        if self.path == "/api/state":
            db = self._open_db()
            try:
                state = _serialise_state(db, datetime.date.today())
            finally:
                db.close()
            self._send_json(200, state)
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_json()
        today = datetime.date.today()
        db = self._open_db()
        try:
            if self.path == "/api/goals":
                raw = (body.get("raw_input") or "").strip()
                if not raw:
                    self._send_json(400, {"error": "raw_input required"})
                    return
                db.goals.upsert(Goal(day_date=today, raw_input=raw))
                self._send_json(200, {"ok": True})
                return

            if self.path == "/api/feedback":
                day_str = (body.get("day_date") or "").strip()
                try:
                    day = datetime.date.fromisoformat(day_str) if day_str else today
                except ValueError:
                    day = today
                score_raw = body.get("score_override")
                try:
                    score = float(score_raw) if score_raw not in (None, "") else None
                except (TypeError, ValueError):
                    score = None
                reasoning = (body.get("reasoning") or "").strip()
                other = (body.get("other_notes") or "").strip()
                if not reasoning and not other and score is None:
                    self._send_json(400, {"error": "nothing to save"})
                    return
                db.feedback.insert(day, score, reasoning, other)
                logger.info("Feedback saved for %s", day)
                self._send_json(200, {"ok": True})
                return

            if self.path == "/api/subgoals":
                desc = (body.get("description") or "").strip()
                if not desc:
                    self._send_json(400, {"error": "description required"})
                    return
                sg = Subgoal(
                    day_date=today,
                    description=desc,
                    parent_goal=body.get("parent_goal") or None,
                )
                db.subgoals.insert(sg)
                self._send_json(200, {"ok": True, "id": sg.id})
                return
        finally:
            db.close()

        self._send_json(404, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        if not self.path.startswith("/api/subgoals/"):
            self._send_json(404, {"error": "not found"})
            return
        try:
            sg_id = int(self.path.rsplit("/", 1)[1])
        except ValueError:
            self._send_json(400, {"error": "invalid id"})
            return
        body = self._read_json()
        db = self._open_db()
        try:
            db.subgoals.update(
                sg_id,
                description=body.get("description"),
                done=body.get("done"),
            )
        finally:
            db.close()
        self._send_json(200, {"ok": True})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self.path.startswith("/api/subgoals/"):
            self._send_json(404, {"error": "not found"})
            return
        try:
            sg_id = int(self.path.rsplit("/", 1)[1])
        except ValueError:
            self._send_json(400, {"error": "invalid id"})
            return
        db = self._open_db()
        try:
            db.subgoals.delete(sg_id)
        finally:
            db.close()
        self._send_json(200, {"ok": True})


def _build_handler(config: Config) -> type[_Handler]:
    """Return a Handler subclass with the config baked in."""
    cls = type("BoundHandler", (_Handler,), {"config": config})
    return cls  # type: ignore[return-value]


def serve(config: Config, port: int = DEFAULT_PORT) -> None:
    """
    Start the dashboard HTTP server on 127.0.0.1:<port>.
    Blocks until KeyboardInterrupt.
    """
    handler_cls = _build_handler(config)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    logger.info("Dashboard listening on http://127.0.0.1:%d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard stopping...")
    finally:
        server.server_close()


def serve_in_thread(config: Config, port: int = DEFAULT_PORT) -> threading.Thread:
    """Spawn the dashboard in a daemon thread; useful for tests."""
    t = threading.Thread(target=serve, args=(config, port), daemon=True)
    t.start()
    return t
