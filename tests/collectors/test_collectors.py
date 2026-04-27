"""tests/collectors/test_collectors.py"""

from __future__ import annotations

import datetime
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tracker.collectors.docwatcher import (
    DocWatcher,
    _count_words_plaintext,
    extract_last_n_words_plaintext,
)
from tracker.collectors.pdf_tracker import FileOrigin, detect_file_origin
from tracker.collectors.screenshot import KNOWN_APPS, should_trigger_llm_analysis
from tracker.collectors.textfield import TextFieldSampler
from tracker.collectors.websocket_server import BrowserEvent, WebSocketServer


# ---------------------------------------------------------------------------
# DocWatcher
# ---------------------------------------------------------------------------

class TestDocWatcher:
    def test_read_word_count_plaintext(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("Hello world this is a test")
        watcher = DocWatcher()
        snap = watcher.read_word_count(str(f))
        assert snap is not None
        assert snap.word_count == 6
        assert snap.extension == ".md"

    def test_read_word_count_missing_file(self, tmp_path: Path) -> None:
        watcher = DocWatcher()
        result = watcher.read_word_count(str(tmp_path / "nonexistent.md"))
        assert result is None

    def test_read_word_count_unsupported_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "test.exe"
        f.write_text("some content")
        watcher = DocWatcher()
        assert watcher.read_word_count(str(f)) is None

    def test_get_delta_first_read_returns_zero(self, tmp_path: Path) -> None:
        watcher = DocWatcher()
        delta = watcher.get_delta("somefile.md", 100)
        assert delta == 0  # first read: no previous

    def test_get_delta_positive(self, tmp_path: Path) -> None:
        watcher = DocWatcher()
        watcher.get_delta("somefile.md", 100)
        delta = watcher.get_delta("somefile.md", 150)
        assert delta == 50

    def test_get_delta_negative(self) -> None:
        watcher = DocWatcher()
        watcher.get_delta("f.md", 200)
        assert watcher.get_delta("f.md", 180) == -20

    def test_clear_history(self) -> None:
        watcher = DocWatcher()
        watcher.get_delta("f.md", 100)
        watcher.clear_history("f.md")
        # After clear, next read should show 0 delta again
        assert watcher.get_delta("f.md", 200) == 0

    def test_count_words_plaintext(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("one two three four five")
        assert _count_words_plaintext(f) == 5

    def test_count_words_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert _count_words_plaintext(f) == 0

    def test_extract_last_n_words(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        words = " ".join(f"word{i}" for i in range(200))
        f.write_text(words)
        result = extract_last_n_words_plaintext(f, n=10)
        assert "word199" in result
        assert "word0" not in result

    def test_extract_last_n_words_missing_file(self, tmp_path: Path) -> None:
        result = extract_last_n_words_plaintext(tmp_path / "gone.md", n=10)
        assert result == ""


# ---------------------------------------------------------------------------
# Screenshot trigger logic (pure function — no I/O)
# ---------------------------------------------------------------------------

class TestShouldTriggerLlmAnalysis:
    def test_unknown_app_triggers(self) -> None:
        should, reason = should_trigger_llm_analysis(
            app_name="WeirdUnknownApp",
            window_title="something",
            word_count=None,
            previous_word_count=None,
            minutes_since_title_change=1.0,
            known_apps=KNOWN_APPS,
        )
        assert should is True
        assert "unknown_app" in reason

    def test_known_app_no_trigger(self) -> None:
        should, reason = should_trigger_llm_analysis(
            app_name="Google Chrome",
            window_title="Claude",
            word_count=None,
            previous_word_count=None,
            minutes_since_title_change=5.0,
            known_apps=KNOWN_APPS,
        )
        assert should is False

    def test_figma_idle_10min_triggers(self) -> None:
        should, reason = should_trigger_llm_analysis(
            app_name="Figma",
            window_title="Fullhouse — Intro Screen",
            word_count=None,
            previous_word_count=None,
            minutes_since_title_change=11.0,
            known_apps=KNOWN_APPS,
        )
        assert should is True
        assert reason == "figma_idle_10min"

    def test_figma_active_no_trigger(self) -> None:
        should, _ = should_trigger_llm_analysis(
            app_name="Figma",
            window_title="Fullhouse",
            word_count=None,
            previous_word_count=None,
            minutes_since_title_change=5.0,
            known_apps=KNOWN_APPS,
        )
        assert should is False

    def test_doc_stalled_triggers(self) -> None:
        should, reason = should_trigger_llm_analysis(
            app_name="Obsidian",
            window_title="Fullhouse vault",
            word_count=1500,
            previous_word_count=1500,
            minutes_since_title_change=25.0,
            known_apps=KNOWN_APPS,
        )
        assert should is True
        assert "stalled" in reason

    def test_no_app_name_triggers(self) -> None:
        should, reason = should_trigger_llm_analysis(
            app_name=None,
            window_title=None,
            word_count=None,
            previous_word_count=None,
            minutes_since_title_change=0.0,
            known_apps=KNOWN_APPS,
        )
        assert should is True
        assert reason == "no_app_detected"


# ---------------------------------------------------------------------------
# TextFieldSampler (non-macOS path)
# ---------------------------------------------------------------------------

class TestTextFieldSamplerNonMac:
    def test_returns_none_on_non_macos(self) -> None:
        sampler = TextFieldSampler()
        with patch("tracker.collectors.textfield._IS_MACOS", False):
            result = sampler.collect()
        assert result is None

    def test_has_changed_detects_change(self) -> None:
        from tracker.collectors.textfield import TextFieldSample
        sampler = TextFieldSampler()
        sample1 = TextFieldSample(app_name="Claude", sample="hello", full_length=5, field_role="AXTextArea")
        sample2 = TextFieldSample(app_name="Claude", sample="hello world", full_length=11, field_role="AXTextArea")
        assert sampler.has_changed(sample1) is True   # first time = changed
        assert sampler.has_changed(sample1) is False  # same = not changed
        assert sampler.has_changed(sample2) is True   # different = changed


class TestTextFieldSamplerExclusions:
    """Terminal-like apps expose their scrollback as AXTextArea. Skip them."""

    def test_terminal_is_excluded(self) -> None:
        from tracker.collectors.textfield import _is_excluded_app
        assert _is_excluded_app("Terminal") is True
        assert _is_excluded_app("iTerm2") is True
        assert _is_excluded_app("iTerm") is True

    def test_regular_apps_not_excluded(self) -> None:
        from tracker.collectors.textfield import _is_excluded_app
        assert _is_excluded_app("WhatsApp") is False
        assert _is_excluded_app("Telegram") is False
        assert _is_excluded_app("Microsoft Word") is False
        assert _is_excluded_app("Google Chrome") is False
        assert _is_excluded_app("Claude") is False

    def test_unicode_whatsapp_name_not_excluded(self) -> None:
        from tracker.collectors.textfield import _is_excluded_app
        assert _is_excluded_app("\u200eWhatsApp") is False


# ---------------------------------------------------------------------------
# PDF origin detector
# ---------------------------------------------------------------------------

class TestPdfOriginDetector:
    def test_returns_unknown_for_missing_file(self, tmp_path: Path) -> None:
        result = detect_file_origin(str(tmp_path / "missing.pdf"))
        assert result.origin == FileOrigin.UNKNOWN

    def test_returns_created_when_no_quarantine(self, tmp_path: Path) -> None:
        f = tmp_path / "myfile.pdf"
        f.write_bytes(b"fake pdf content")
        with (
            patch("tracker.collectors.pdf_tracker._IS_MACOS", True),
            patch(
                "tracker.collectors.pdf_tracker._read_quarantine_xattr",
                return_value=None,
            ),
        ):
            result = detect_file_origin(str(f))
        assert result.origin == FileOrigin.CREATED

    def test_returns_received_when_quarantine_present(self, tmp_path: Path) -> None:
        f = tmp_path / "received.pdf"
        f.write_bytes(b"fake pdf content")
        with (
            patch("tracker.collectors.pdf_tracker._IS_MACOS", True),
            patch(
                "tracker.collectors.pdf_tracker._read_quarantine_xattr",
                return_value="0083;12345678;Chrome;",
            ),
        ):
            result = detect_file_origin(str(f))
        assert result.origin == FileOrigin.RECEIVED
        assert result.quarantine_value is not None

    def test_non_macos_returns_unknown(self, tmp_path: Path) -> None:
        f = tmp_path / "file.pdf"
        f.write_bytes(b"content")
        with patch("tracker.collectors.pdf_tracker._IS_MACOS", False):
            result = detect_file_origin(str(f))
        assert result.origin == FileOrigin.UNKNOWN


# ---------------------------------------------------------------------------
# WebSocketServer event parsing
# ---------------------------------------------------------------------------

class TestWebSocketServerEventParsing:
    def test_parse_tab_activated_event(self) -> None:
        server = WebSocketServer()
        server._parse_and_queue('{"type":"tab_activated","url":"https://claude.ai","title":"Claude","timestamp":1714000000000}')
        events = server.drain_events()
        assert len(events) == 1
        assert events[0].event_type == "tab_activated"
        assert events[0].url == "https://claude.ai"

    def test_parse_youtube_video_event(self) -> None:
        server = WebSocketServer()
        server._parse_and_queue('{"type":"youtube_video","url":"https://youtube.com/watch?v=abc","title":"","video_title":"Arsenal vs City","channel":"Sky Sports","timestamp":1714000000000}')
        events = server.drain_events()
        assert len(events) == 1
        assert events[0].video_title == "Arsenal vs City"
        assert events[0].channel == "Sky Sports"

    def test_parse_malformed_json_drops_silently(self) -> None:
        server = WebSocketServer()
        server._parse_and_queue("{not valid json}")
        assert server.drain_events() == []

    def test_parse_missing_type_drops_silently(self) -> None:
        server = WebSocketServer()
        server._parse_and_queue('{"url":"https://example.com"}')
        assert server.drain_events() == []

    def test_drain_events_empty_queue(self) -> None:
        server = WebSocketServer()
        assert server.drain_events() == []

    def test_drain_events_multiple(self) -> None:
        server = WebSocketServer()
        for i in range(3):
            server._parse_and_queue(f'{{"type":"tab_updated","url":"https://example.com/{i}","title":"Page {i}","timestamp":0}}')
        events = server.drain_events()
        assert len(events) == 3
        # After drain, queue is empty
        assert server.drain_events() == []
