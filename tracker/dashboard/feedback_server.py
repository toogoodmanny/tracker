"""
tracker/dashboard/feedback_server.py

Tiny standalone HTTP server that accepts feedback POSTs from daily reports.
Spawned automatically by `track end` — runs silently in the background.
Auto-shuts down after MAX_IDLE_MINUTES of no traffic.

The report HTML tries port 27184 (this server) first, then falls back to
27183 (dashboard server) so either works.

Usage (internal — called by cli/main.py):
    python -m tracker.dashboard.feedback_server <db_path> <port>
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FEEDBACK_PORT = 27184
MAX_IDLE_MINUTES = 120  # auto-shutdown after 2 h of no traffic

logger = logging.getLogger(__name__)


class _Handler(BaseHTTPRequestHandler):
    db_path: Path  # set by build_handler()
    _last_hit: list[float] = [time.monotonic()]  # shared mutable for watchdog

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silence access logs entirely

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        type(self)._last_hit[0] = time.monotonic()

        if self.path != "/api/feedback":
            self._json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        body: dict = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        day_str = (body.get("day_date") or "").strip()
        score_raw = body.get("score_override")
        reasoning = (body.get("reasoning") or "").strip()
        other = (body.get("other_notes") or "").strip()

        if not reasoning and not other and score_raw is None:
            self._json(400, {"error": "nothing to save"})
            return

        try:
            import datetime
            from tracker.db.connection import open_database
            from tracker.db.repositories import Database

            try:
                day = datetime.date.fromisoformat(day_str) if day_str else datetime.date.today()
            except ValueError:
                day = datetime.date.today()

            try:
                score = float(score_raw) if score_raw not in (None, "") else None
            except (TypeError, ValueError):
                score = None

            conn = open_database(self.db_path)
            db = Database(conn)
            try:
                db.feedback.insert(day, score, reasoning, other)
            finally:
                db.close()

            self._json(200, {"ok": True})
        except Exception as exc:  # noqa: BLE001
            logger.error("Feedback save error: %s", exc)
            self._json(500, {"error": str(exc)})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


def _build_handler(db_path: Path) -> type[_Handler]:
    return type("BoundHandler", (_Handler,), {"db_path": db_path, "_last_hit": [time.monotonic()]})


def run(db_path: Path, port: int = FEEDBACK_PORT) -> None:
    """Start the feedback server; blocks until idle timeout or KeyboardInterrupt."""
    handler_cls = _build_handler(db_path)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    except OSError:
        # Port already in use — another instance is running, just exit
        sys.exit(0)

    def _watchdog() -> None:
        while True:
            time.sleep(60)
            idle_s = time.monotonic() - handler_cls._last_hit[0]
            if idle_s > MAX_IDLE_MINUTES * 60:
                server.shutdown()
                return

    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: python -m tracker.dashboard.feedback_server <db_path> <port>")
        sys.exit(1)
    # Suppress all logging — this runs silently in background
    logging.disable(logging.CRITICAL)
    run(Path(sys.argv[1]), int(sys.argv[2]))
