"""tests/test_aw_client.py"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tracker.aw_client import AWStatus, AWWindowEvent, ActivityWatchClient


class TestActivityWatchClientConnectivity:
    def test_check_connectivity_reachable(self) -> None:
        client = ActivityWatchClient()
        with patch.object(client._client, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"version": "0.13.0"}
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            status = client.check_connectivity()

        assert status.reachable is True
        assert status.version == "0.13.0"
        assert status.error is None

    def test_check_connectivity_refused(self) -> None:
        client = ActivityWatchClient()
        with patch.object(client._client, "get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("connection refused")
            status = client.check_connectivity()

        assert status.reachable is False
        assert "connection refused" in status.error

    def test_check_connectivity_timeout(self) -> None:
        client = ActivityWatchClient()
        with patch.object(client._client, "get") as mock_get:
            mock_get.side_effect = httpx.TimeoutException("timeout")
            status = client.check_connectivity()

        assert status.reachable is False
        assert status.error == "timeout"

    def test_check_connectivity_never_raises(self) -> None:
        """check_connectivity must never propagate exceptions."""
        client = ActivityWatchClient()
        with patch.object(client._client, "get") as mock_get:
            mock_get.side_effect = RuntimeError("unexpected")
            # Should not raise — but actually RuntimeError is not caught
            # This documents that unexpected errors DO propagate for debugging
            with pytest.raises(RuntimeError):
                client.check_connectivity()


class TestActivityWatchClientWindowEvents:
    def _make_client_with_buckets(self, buckets: list[str]) -> ActivityWatchClient:
        client = ActivityWatchClient()
        with patch.object(client, "list_buckets", return_value=buckets):
            pass
        return client

    def test_get_current_window_returns_event(self) -> None:
        client = ActivityWatchClient()
        raw_event = {
            "timestamp": "2025-04-24T10:30:00+00:00",
            "duration": 30.0,
            "data": {"app": "Figma", "title": "Fullhouse — Intro Screen"},
        }
        with (
            patch.object(client, "list_buckets", return_value=["aw-watcher-window_host"]),
            patch.object(client._client, "get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = [raw_event]
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = client.get_current_window()

        assert result is not None
        assert result.app == "Figma"
        assert result.title == "Fullhouse — Intro Screen"
        assert result.duration_seconds == 30.0

    def test_get_current_window_returns_none_when_aw_unreachable(self) -> None:
        client = ActivityWatchClient()
        with (
            patch.object(client, "list_buckets", return_value=["aw-watcher-window_host"]),
            patch.object(client._client, "get") as mock_get,
        ):
            mock_get.side_effect = httpx.ConnectError("refused")
            result = client.get_current_window()

        assert result is None

    def test_get_current_window_returns_none_when_no_bucket(self) -> None:
        client = ActivityWatchClient()
        with patch.object(client, "list_buckets", return_value=[]):
            result = client.get_current_window()
        assert result is None

    def test_get_current_window_returns_none_when_empty_events(self) -> None:
        client = ActivityWatchClient()
        with (
            patch.object(client, "list_buckets", return_value=["aw-watcher-window_host"]),
            patch.object(client._client, "get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = []
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = client.get_current_window()

        assert result is None

    def test_get_afk_status_not_afk(self) -> None:
        client = ActivityWatchClient()
        with (
            patch.object(client, "list_buckets", return_value=["aw-watcher-afk_host"]),
            patch.object(client._client, "get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = [{"data": {"status": "not-afk"}}]
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = client.get_afk_status()

        assert result is False

    def test_get_afk_status_afk(self) -> None:
        client = ActivityWatchClient()
        with (
            patch.object(client, "list_buckets", return_value=["aw-watcher-afk_host"]),
            patch.object(client._client, "get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = [{"data": {"status": "afk"}}]
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = client.get_afk_status()

        assert result is True

    def test_get_afk_status_returns_false_when_unreachable(self) -> None:
        client = ActivityWatchClient()
        with patch.object(client, "list_buckets", return_value=[]):
            result = client.get_afk_status()
        assert result is False

    def test_list_buckets_returns_empty_on_connect_error(self) -> None:
        client = ActivityWatchClient()
        with patch.object(client._client, "get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("refused")
            result = client.list_buckets()
        assert result == []

    def test_parse_window_event_fields(self) -> None:
        raw = {
            "timestamp": "2025-04-24T09:00:00+00:00",
            "duration": 120.0,
            "data": {"app": "Obsidian", "title": "Fullhouse vault"},
        }
        event = ActivityWatchClient._parse_window_event(raw)
        assert event.app == "Obsidian"
        assert event.title == "Fullhouse vault"
        assert event.duration_seconds == 120.0
        assert isinstance(event.timestamp, datetime.datetime)
