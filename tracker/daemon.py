"""
tracker/daemon.py

The main polling daemon. Thin orchestrator — no business logic lives here.
Wakes every poll_interval_seconds, calls collectors, writes to DB.
"""

from __future__ import annotations

import datetime
import logging
import signal
import time
from pathlib import Path

from tracker.aw_client import ActivityWatchClient
from tracker.collectors.docwatcher import DocWatcher
from tracker.collectors.screenshot import KNOWN_APPS, ScreenshotCollector, should_trigger_llm_analysis
from tracker.collectors.textfield import TextFieldSampler
from tracker.collectors.websocket_server import BrowserEvent, WebSocketServer
from tracker.config import Config
from tracker.core.models import CollectionResult, Observation, ObservationType, Snapshot
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)


class Daemon:
    """
    The polling daemon.

    Responsibilities:
    - Poll ActivityWatch every N seconds
    - Enrich with text field samples, browser events, doc word counts
    - Capture screenshots on schedule, trigger LLM conditionally
    - Write enriched snapshots to the database
    - Handle graceful shutdown on SIGTERM/SIGINT
    - Log observations for weekly review
    - Never crash silently — all errors are caught, named, and logged
    """

    def __init__(
        self,
        config: Config,
        db: Database,
        aw: ActivityWatchClient,
        session_id: int,
    ) -> None:
        self._config = config
        self._db = db
        self._aw = aw
        self._session_id = session_id
        self._running = False
        self._poll_count = 0
        self._seen_apps: set[str] = set()

        # Snapshot deduplication state
        # Apps where every message matters — always save at full poll rate
        self._HIGH_FREQ_APPS: frozenset[str] = frozenset({
            "WhatsApp", "Telegram", "Signal", "Messages", "iMessage",
            "Slack", "Discord", "Teams", "Microsoft Teams",
            "Claude", "ChatGPT", "Notion",
        })
        # Per-app: character length of last saved text field sample
        self._last_saved_text_len: dict[str, int] = {}
        # Per-app: monotonic time of last saved snapshot
        self._last_saved_time: dict[str, float] = {}
        # Last saved app + title to detect switches
        self._last_saved_app: str | None = None
        self._last_saved_title: str | None = None
        # Max gap before saving a "breadcrumb" even if nothing changed
        self._BREADCRUMB_INTERVAL_S: float = 300.0  # 5 minutes

        # Phase 2 collectors
        self._text_sampler = TextFieldSampler(
            sample_chars=config.daemon.text_field_sample_chars
        )
        self._doc_watcher = DocWatcher()
        self._ws_server = WebSocketServer(port=config.daemon.websocket_port)
        self._screenshot_collector = ScreenshotCollector(
            screenshots_dir=config.paths.screenshots_dir
        )

        # Screenshot timing
        self._last_screenshot_time: float = 0.0
        self._last_window_title: str | None = None
        self._last_title_change_time: float = time.monotonic()
        self._last_word_count: int | None = None

        # Latest browser event cache (updated from WS queue each poll)
        self._latest_browser_event: BrowserEvent | None = None

    def run(self) -> None:
        """
        Start the polling loop.
        Runs until stop() is called or SIGTERM/SIGINT received.
        """
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # Start background WebSocket server for Chrome extension
        self._ws_server.start()

        logger.info(
            "Daemon started — session %d, polling every %ds",
            self._session_id,
            self._config.daemon.poll_interval_seconds,
        )

        while self._running:
            loop_start = time.monotonic()

            result = self._poll_once()

            if result.success and result.snapshot is not None:
                if self._should_save(result.snapshot):
                    try:
                        self._db.snapshots.insert(result.snapshot)
                        self._poll_count += 1
                        self._update_save_state(result.snapshot)
                        self._check_for_observations(result.snapshot)
                    except Exception as exc:  # noqa: BLE001 — last resort, re-logged
                        logger.error(
                            "Unexpected error inserting snapshot (poll %d): %s",
                            self._poll_count,
                            exc,
                        )
                else:
                    logger.debug(
                        "Snapshot skipped (no new information): app=%s",
                        result.snapshot.app_name,
                    )
            elif not result.success:
                logger.debug("Poll failed: %s", result.error)

            elapsed = time.monotonic() - loop_start
            sleep_time = max(
                0.0,
                self._config.daemon.poll_interval_seconds - elapsed,
            )
            time.sleep(sleep_time)

        self._ws_server.stop()
        logger.info("Daemon stopped after %d polls", self._poll_count)

    def stop(self) -> None:
        """Signal the daemon to stop after the current poll."""
        logger.info("Daemon stop requested")
        self._running = False

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _poll_once(self) -> CollectionResult:
        """
        Collect one enriched snapshot from all available sources.
        Returns CollectionResult — never raises.
        """
        now = datetime.datetime.now()

        # 1. ActivityWatch — base window + AFK data
        try:
            window_event = self._aw.get_current_window()
            is_afk = self._aw.get_afk_status()
        except Exception as exc:  # noqa: BLE001
            logger.debug("AW poll failed: %s", exc)
            window_event = None
            is_afk = False

        snapshot = Snapshot(
            session_id=self._session_id,
            timestamp=now,
            is_afk=is_afk,
        )

        if window_event is not None:
            snapshot.app_name = window_event.app
            snapshot.window_title = window_event.title
            # Track title change time for screenshot trigger
            if window_event.title != self._last_window_title:
                self._last_window_title = window_event.title
                self._last_title_change_time = time.monotonic()

        # 2. Browser events from Chrome extension (non-blocking queue drain)
        self._apply_browser_events(snapshot)

        # 3. Text field sample (macOS Accessibility API)
        text_sample = self._text_sampler.collect()
        if text_sample is not None:
            snapshot.text_field_sample = text_sample.sample
            # Override app_name if AW missed it and text sampler got it
            if snapshot.app_name is None:
                snapshot.app_name = text_sample.app_name

        # 4. Doc word count (if active file is a doc)
        self._apply_doc_word_count(snapshot)

        # 5. Screenshot (on schedule, LLM trigger evaluated separately)
        self._maybe_capture_screenshot(snapshot)

        return CollectionResult.ok(snapshot)

    def _apply_browser_events(self, snapshot: Snapshot) -> None:
        """
        Drain the WebSocket event queue and apply the most recent event to snapshot.
        For YouTube, video_title goes into page_title.
        """
        events = self._ws_server.drain_events()
        for event in events:
            self._latest_browser_event = event

        if self._latest_browser_event is not None:
            ev = self._latest_browser_event
            if snapshot.url is None:
                snapshot.url = ev.url or None
            if snapshot.page_title is None:
                snapshot.page_title = ev.title or None
            if ev.event_type == "youtube_video" and ev.video_title:
                snapshot.page_title = ev.video_title
            if ev.text_sample and snapshot.text_field_sample is None:
                snapshot.text_field_sample = ev.text_sample

    def _apply_doc_word_count(self, snapshot: Snapshot) -> None:
        """
        If the active window title suggests a doc file path is known,
        poll word count and compute delta.
        Currently: uses active_file_path if set (Phase 2+ will detect from window title).
        """
        if snapshot.active_file_path is None:
            return

        doc_snap = self._doc_watcher.read_word_count(snapshot.active_file_path)
        if doc_snap is None:
            return

        snapshot.word_count = doc_snap.word_count
        delta = self._doc_watcher.get_delta(
            snapshot.active_file_path, doc_snap.word_count
        )
        snapshot.word_count_delta = delta
        self._last_word_count = doc_snap.word_count

    def _maybe_capture_screenshot(self, snapshot: Snapshot) -> None:
        """
        Capture a screenshot if enough time has passed since the last one.
        Also evaluate whether to flag it for LLM analysis.
        """
        now_mono = time.monotonic()
        elapsed_since_last = now_mono - self._last_screenshot_time

        if elapsed_since_last < self._config.daemon.screenshot_interval_seconds:
            return

        path = self._screenshot_collector.capture()
        if path is None:
            return

        self._last_screenshot_time = now_mono
        snapshot.screenshot_path = path

        # Evaluate LLM trigger
        minutes_since_title_change = (now_mono - self._last_title_change_time) / 60.0
        should_trigger, reason = should_trigger_llm_analysis(
            app_name=snapshot.app_name,
            window_title=snapshot.window_title,
            word_count=snapshot.word_count,
            previous_word_count=self._last_word_count,
            minutes_since_title_change=minutes_since_title_change,
            known_apps=KNOWN_APPS,
        )

        if should_trigger:
            snapshot.screenshot_analysed = False  # analysis pending
            logger.debug(
                "Screenshot flagged for LLM analysis: %s (reason: %s)",
                path,
                reason,
            )
            obs = Observation(
                day_date=snapshot.timestamp.date(),
                observation_type=ObservationType.NEW_APP,
                detail=f"Screenshot trigger: {reason}",
            )
            try:
                self._db.observations.insert(obs)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not store screenshot observation: %s", exc)

    # ------------------------------------------------------------------
    # Snapshot deduplication
    # ------------------------------------------------------------------

    def _should_save(self, snapshot: Snapshot) -> bool:
        """
        Return True if this snapshot carries new information worth storing.

        Always save when:
        - First snapshot of the session
        - App or window title changed (new activity)
        - App is a high-frequency app (messaging, AI)
        - AFK status
        - No save for this app in > BREADCRUMB_INTERVAL_S (heartbeat)

        Skip when:
        - Same app + same title + text field hasn't grown by ≥ 400 chars
        """
        app = snapshot.app_name or ""

        # Always save: first poll, AFK, app switch, or title change
        if self._poll_count == 0:
            return True
        if snapshot.is_afk:
            return True
        if app != (self._last_saved_app or ""):
            return True
        if snapshot.window_title != self._last_saved_title:
            return True

        # High-frequency apps: always save
        if app in self._HIGH_FREQ_APPS:
            return True

        # Heartbeat: save at least once per BREADCRUMB_INTERVAL_S per app
        now_mono = time.monotonic()
        last_time = self._last_saved_time.get(app, 0.0)
        if now_mono - last_time >= self._BREADCRUMB_INTERVAL_S:
            return True

        # Skip if text field hasn't grown by ≥ 400 chars
        current_len = len(snapshot.text_field_sample or "")
        last_len = self._last_saved_text_len.get(app, 0)
        if current_len - last_len >= 400:
            return True

        return False

    def _update_save_state(self, snapshot: Snapshot) -> None:
        """Update dedup state after a successful insert."""
        app = snapshot.app_name or ""
        self._last_saved_app = app
        self._last_saved_title = snapshot.window_title
        self._last_saved_time[app] = time.monotonic()
        self._last_saved_text_len[app] = len(snapshot.text_field_sample or "")

    # ------------------------------------------------------------------
    # Observation filing
    # ------------------------------------------------------------------

    def _check_for_observations(self, snapshot: Snapshot) -> None:
        """
        Silently file observations that matter for weekly review.
        Never raises — observation filing must not interrupt the poll cycle.
        """
        try:
            self._check_new_app(snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Observation check error: %s", exc)

    def _check_new_app(self, snapshot: Snapshot) -> None:
        if snapshot.app_name is None:
            return
        if snapshot.app_name not in self._seen_apps:
            self._seen_apps.add(snapshot.app_name)
            if self._poll_count > 5:  # skip initial flurry of new apps on startup
                obs = Observation(
                    day_date=snapshot.timestamp.date(),
                    observation_type=ObservationType.NEW_APP,
                    detail=f"First time seeing app: {snapshot.app_name!r}",
                )
                self._db.observations.insert(obs)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _handle_signal(self, signum: int, frame: object) -> None:
        logger.info("Received signal %d — stopping daemon", signum)
        self._running = False
