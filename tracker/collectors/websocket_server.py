"""
tracker/collectors/websocket_server.py

Receives events from the Chrome extension via WebSocket.
Runs in a background thread alongside the daemon.
Stores incoming events in a thread-safe queue for the daemon to consume.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BrowserEvent:
    """A single event received from the Chrome extension."""
    event_type: str       # tab_activated, tab_updated, youtube_video, page_text
    url: str
    title: str
    timestamp_ms: int
    # Optional fields
    video_title: str | None = None
    channel: str | None = None
    text_sample: str | None = None


class WebSocketServer:
    """
    Runs an asyncio WebSocket server in a daemon thread.
    Incoming events are placed into a thread-safe queue.
    The main daemon thread reads from this queue each poll cycle.
    """

    def __init__(self, port: int = 27182) -> None:
        self._port = port
        self._event_queue: queue.Queue[BrowserEvent] = queue.Queue(maxsize=500)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None

    def start(self) -> None:
        """Start the WebSocket server in a background daemon thread."""
        self._thread = threading.Thread(
            target=self._run_event_loop,
            daemon=True,
            name="tracker-ws-server",
        )
        self._thread.start()
        logger.info("WebSocket server started on port %d", self._port)

    def stop(self) -> None:
        """Request shutdown of the WebSocket server."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("WebSocket server stopped")

    def drain_events(self) -> list[BrowserEvent]:
        """
        Return all pending events from the queue without blocking.
        Called by the daemon each poll cycle.
        """
        events: list[BrowserEvent] = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _run_event_loop(self) -> None:
        """Entry point for the background thread."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            logger.error("WebSocket server thread error: %s", exc)

    async def _serve(self) -> None:
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("websockets package not installed — browser integration disabled")
            return

        try:
            self._server = await websockets.serve(
                self._handle_connection,
                "localhost",
                self._port,
            )
            await asyncio.Future()  # Run forever until loop.stop()
        except OSError as exc:
            logger.warning(
                "Could not bind WebSocket server on port %d: %s. "
                "Another instance may be running.",
                self._port,
                exc,
            )

    async def _handle_connection(self, websocket: Any) -> None:
        """Handle a single Chrome extension connection."""
        logger.debug("Chrome extension connected")
        try:
            async for raw_message in websocket:
                self._parse_and_queue(raw_message)
        except Exception as exc:
            logger.debug("WebSocket connection closed: %s", exc)

    def _parse_and_queue(self, raw: str) -> None:
        """
        Parse a JSON message from the extension and enqueue it.
        Silently drops malformed messages — the extension is not trusted input.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("Malformed WS message (not JSON): %s", exc)
            return

        event_type = data.get("type", "")
        if not event_type:
            return

        try:
            event = BrowserEvent(
                event_type=event_type,
                url=str(data.get("url", "")),
                title=str(data.get("title", "") or data.get("page_title", "")),
                timestamp_ms=int(data.get("timestamp", 0)),
                video_title=data.get("video_title"),
                channel=data.get("channel"),
                text_sample=data.get("text_sample"),
            )
            try:
                self._event_queue.put_nowait(event)
            except queue.Full:
                # Drop oldest event to make room
                try:
                    self._event_queue.get_nowait()
                    self._event_queue.put_nowait(event)
                except queue.Empty:
                    pass
        except (TypeError, ValueError) as exc:
            logger.debug("Could not construct BrowserEvent: %s", exc)
