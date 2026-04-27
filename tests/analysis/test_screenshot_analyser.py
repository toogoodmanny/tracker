"""tests/analysis/test_screenshot_analyser.py"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tracker.analysis.screenshot_analyser import (
    ScreenshotAnalyser,
    ScreenshotAnalysisResult,
)
from tracker.core.models import Session, SessionType, Snapshot


def _make_analyser(test_config, db) -> ScreenshotAnalyser:
    test_config.paths.prompts_dir.mkdir(parents=True, exist_ok=True)
    (test_config.paths.prompts_dir / "screenshot-analysis.txt").write_text(
        "App: {app_name} | Title: {window_title} | At: {timestamp} | "
        "Why: {trigger_reason}\nWhat is the user doing?"
    )
    return ScreenshotAnalyser(config=test_config, db=db)


def _seed_session(db) -> int:
    s = Session(
        day_date=datetime.date.today(),
        session_type=SessionType.PRIMARY,
        start_time=datetime.datetime.now(),
    )
    return db.sessions.insert(s)


def _make_jpeg(path: Path) -> None:
    """Write a tiny valid-ish JPEG so file reads succeed."""
    path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9")


class TestScreenshotAnalyser:
    def test_no_pending_returns_zero(self, test_config, db) -> None:
        analyser = _make_analyser(test_config, db)
        result = analyser.analyse_pending()
        assert result.analysed_count == 0
        assert result.skipped_count == 0

    def test_no_api_key_skips_silently(self, test_config, db, tmp_path) -> None:
        from dataclasses import replace
        from tracker.config import ApiConfig
        cfg = replace(
            test_config,
            api=ApiConfig(
                anthropic_api_key="",
                anthropic_model=test_config.api.anthropic_model,
                aw_base_url=test_config.api.aw_base_url,
            ),
        )
        analyser = _make_analyser(cfg, db)
        result = analyser.analyse_pending()
        assert result.analysed_count == 0

    def test_missing_screenshot_file_is_skipped(self, test_config, db) -> None:
        session_id = _seed_session(db)
        snap = Snapshot(
            session_id=session_id,
            timestamp=datetime.datetime.now(),
            app_name="Figma",
            window_title="Fullhouse",
            screenshot_path="/tmp/does-not-exist.jpg",
        )
        db.snapshots.insert(snap)

        analyser = _make_analyser(test_config, db)
        result = analyser.analyse_pending()
        assert result.analysed_count == 0
        assert result.skipped_count == 1

    def test_successful_analysis_marks_snapshot(self, test_config, db, tmp_path) -> None:
        session_id = _seed_session(db)
        img = tmp_path / "shot.jpg"
        _make_jpeg(img)
        snap = Snapshot(
            session_id=session_id,
            timestamp=datetime.datetime.now(),
            app_name="Figma",
            window_title="Fullhouse - Intro",
            screenshot_path=str(img),
        )
        db.snapshots.insert(snap)

        analyser = _make_analyser(test_config, db)

        fake_message = MagicMock()
        fake_message.content = [MagicMock(type="text", text="User is designing intro screen in Figma. Looks like work.")]
        fake_message.usage = MagicMock(input_tokens=1500, output_tokens=40)

        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_message

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = analyser.analyse_pending()

        assert result.analysed_count == 1
        assert result.input_tokens == 1500
        assert result.output_tokens == 40

        stored = db.snapshots.get_by_session(session_id)[0]
        assert stored.screenshot_analysed is True
        assert "Figma" in stored.screenshot_analysis

    def test_api_error_skips_one_continues_others(self, test_config, db, tmp_path) -> None:
        session_id = _seed_session(db)
        for i in range(2):
            img = tmp_path / f"shot{i}.jpg"
            _make_jpeg(img)
            snap = Snapshot(
                session_id=session_id,
                timestamp=datetime.datetime.now() + datetime.timedelta(seconds=i),
                app_name="Figma",
                window_title=f"shot {i}",
                screenshot_path=str(img),
            )
            db.snapshots.insert(snap)

        analyser = _make_analyser(test_config, db)

        ok_message = MagicMock()
        ok_message.content = [MagicMock(type="text", text="ok")]
        ok_message.usage = MagicMock(input_tokens=100, output_tokens=10)

        import anthropic
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = [
            anthropic.APIError("boom", request=MagicMock(), body=None),
            ok_message,
        ]

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = analyser.analyse_pending()

        assert result.analysed_count == 1
        assert result.skipped_count == 1

    def test_build_prompt_substitutes_fields(self, test_config, db) -> None:
        analyser = _make_analyser(test_config, db)
        snap = Snapshot(
            session_id=1,
            timestamp=datetime.datetime(2026, 4, 24, 14, 30),
            app_name="Figma",
            window_title="Fullhouse",
        )
        prompt = analyser._build_prompt(snap)
        assert "Figma" in prompt
        assert "Fullhouse" in prompt
        assert "2026-04-24" in prompt
