"""
tracker/aw_client.py

Typed wrapper around the ActivityWatch REST API.
All ActivityWatch queries live here — nothing else calls AW directly.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_AW_TIMEOUT = 3.0  # seconds — AW is local, should be fast


@dataclass(frozen=True)
class AWWindowEvent:
    """A single event from aw-watcher-window."""
    timestamp: datetime.datetime
    duration_seconds: float
    app: str
    title: str


@dataclass(frozen=True)
class AWWebEvent:
    """A single event from aw-watcher-web."""
    timestamp: datetime.datetime
    duration_seconds: float
    url: str
    title: str
    audible: bool
    incognito: bool


@dataclass(frozen=True)
class AWStatus:
    """Result of a connectivity check."""
    reachable: bool
    version: str | None
    error: str | None


class ActivityWatchClient:
    """
    Thin typed wrapper around ActivityWatch's REST API.

    Only reads data — never writes to AW. AW manages its own state.
    """

    def __init__(self, base_url: str = "http://localhost:5600") -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=_AW_TIMEOUT,
        )

    def check_connectivity(self) -> AWStatus:
        """
        Ping the AW server.
        Returns AWStatus — never raises.
        """
        try:
            resp = self._client.get("/api/0/info")
            resp.raise_for_status()
            data = resp.json()
            return AWStatus(
                reachable=True,
                version=data.get("version"),
                error=None,
            )
        except httpx.ConnectError:
            return AWStatus(reachable=False, version=None, error="connection refused")
        except httpx.TimeoutException:
            return AWStatus(reachable=False, version=None, error="timeout")
        except httpx.HTTPStatusError as exc:
            return AWStatus(reachable=False, version=None, error=str(exc))

    def get_current_window(self) -> AWWindowEvent | None:
        """
        Return the most recent window event.
        Returns None if AW is unreachable or no events exist.
        Raises httpx.HTTPStatusError on unexpected server errors.
        """
        bucket_id = self._find_window_bucket()
        if bucket_id is None:
            return None

        try:
            resp = self._client.get(
                f"/api/0/buckets/{bucket_id}/events",
                params={"limit": 1},
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            logger.debug("AW unreachable when fetching window events")
            return None
        except httpx.TimeoutException:
            logger.debug("AW timeout when fetching window events")
            return None

        events = resp.json()
        if not events:
            return None

        return self._parse_window_event(events[0])

    def get_afk_status(self) -> bool:
        """
        Return True if the user is currently AFK (away from keyboard).
        Returns False (not AFK) if AW is unreachable.
        """
        bucket_id = self._find_afk_bucket()
        if bucket_id is None:
            return False

        try:
            resp = self._client.get(
                f"/api/0/buckets/{bucket_id}/events",
                params={"limit": 1},
            )
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

        events = resp.json()
        if not events:
            return False

        status: str = events[0].get("data", {}).get("status", "not-afk")
        return status == "afk"

    def get_window_events_for_day(
        self, day_date: datetime.date
    ) -> list[AWWindowEvent]:
        """
        Return all window events for a calendar day.
        Returns empty list if AW unreachable or no data.
        Raises httpx.HTTPStatusError on unexpected server errors.
        """
        bucket_id = self._find_window_bucket()
        if bucket_id is None:
            return []

        start = datetime.datetime.combine(day_date, datetime.time.min).isoformat() + "Z"
        end = datetime.datetime.combine(
            day_date + datetime.timedelta(days=1), datetime.time.min
        ).isoformat() + "Z"

        try:
            resp = self._client.get(
                f"/api/0/buckets/{bucket_id}/events",
                params={"start": start, "end": end, "limit": 10000},
            )
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("Could not fetch AW window events for %s: %s", day_date, exc)
            return []

        return [self._parse_window_event(e) for e in resp.json()]

    def list_buckets(self) -> list[str]:
        """
        Return all bucket IDs.
        Returns empty list if AW unreachable.
        """
        try:
            resp = self._client.get("/api/0/buckets/")
            resp.raise_for_status()
            return list(resp.json().keys())
        except (httpx.ConnectError, httpx.TimeoutException):
            return []

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_window_bucket(self) -> str | None:
        """Find the aw-watcher-window bucket ID (hostname varies)."""
        try:
            buckets = self.list_buckets()
            for b in buckets:
                if b.startswith("aw-watcher-window"):
                    return b
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("Error listing AW buckets: %s", exc)
            return None

    def _find_afk_bucket(self) -> str | None:
        """Find the aw-watcher-afk bucket ID."""
        try:
            buckets = self.list_buckets()
            for b in buckets:
                if b.startswith("aw-watcher-afk"):
                    return b
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("Error listing AW buckets: %s", exc)
            return None

    @staticmethod
    def _parse_window_event(raw: dict) -> AWWindowEvent:
        return AWWindowEvent(
            timestamp=datetime.datetime.fromisoformat(
                raw["timestamp"].replace("Z", "+00:00")
            ),
            duration_seconds=raw.get("duration", 0.0),
            app=raw.get("data", {}).get("app", ""),
            title=raw.get("data", {}).get("title", ""),
        )
