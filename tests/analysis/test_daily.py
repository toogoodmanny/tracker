"""tests/analysis/test_daily.py"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tracker.analysis.daily import (
    DailyAnalyser,
    GoalParser,
    _build_html_report,
    _build_user_context_section,
    _compute_capture_stats,
    _parse_reddit_url,
    _score_color,
)
from tracker.core.models import Goal, Session, Snapshot


class TestBuildHtmlReport:
    def _sample_analysis(self) -> dict:
        return {
            "day_score": 5.5,
            "score_reasoning": "Mixed day with some good deep work but significant drift.",
            "active_minutes": 480,
            "deep_work_minutes": 90,
            "drift_minutes": 60,
            "rabbit_hole_minutes": 54,
            "meeting_minutes": 45,
            "break_minutes": 72,
            "longest_focus_streak_minutes": 41,
            "longest_focus_streak_start": "09:02",
            "video_count": 6,
            "video_minutes": 54,
            "ai_chat_minutes": 60,
            "ai_chat_on_goal_minutes": 41,
            "goals_comparison": [
                {
                    "goal": "Fullhouse intro screen",
                    "target_minutes": 60,
                    "actual_minutes": 38,
                    "status": "partial",
                    "note": "Started late",
                }
            ],
            "timeline": [
                {
                    "start_time": "09:02",
                    "end_time": "09:43",
                    "duration_minutes": 41,
                    "category": "deep_work",
                    "sub_category": "original_thinking",
                    "app": "Claude",
                    "project": "Fullhouse",
                    "description": "Working on Fullhouse intro screen copy",
                    "drift_trigger": None,
                    "ai_on_goal": True,
                }
            ],
            "drift_triggers": [
                {
                    "time": "09:43",
                    "trigger": "Opened new tab, searched IPL score",
                    "what_was_abandoned": "Fullhouse Claude session",
                    "duration_minutes": 18,
                    "recovery": "Returned to Figma after closing tabs",
                }
            ],
            "narrative": "You spent 54 minutes reading about Arsenal tactics in the middle of your Fullhouse Obsidian session. This was not research.",
            "pattern_observations": ["Post-lunch drift pattern detected"],
        }

    def test_renders_valid_html(self) -> None:
        analysis = self._sample_analysis()
        html = _build_html_report(analysis, datetime.date(2025, 4, 24))
        assert "<!DOCTYPE html>" in html
        assert "Thursday, 24 April 2025" in html

    def test_contains_score(self) -> None:
        analysis = self._sample_analysis()
        html = _build_html_report(analysis, datetime.date(2025, 4, 24))
        assert "5.5" in html

    def test_contains_narrative(self) -> None:
        analysis = self._sample_analysis()
        html = _build_html_report(analysis, datetime.date(2025, 4, 24))
        assert "Arsenal tactics" in html

    def test_contains_timeline_entry(self) -> None:
        analysis = self._sample_analysis()
        html = _build_html_report(analysis, datetime.date(2025, 4, 24))
        assert "deep_work" in html or "deep work" in html

    def test_contains_drift_trigger(self) -> None:
        analysis = self._sample_analysis()
        html = _build_html_report(analysis, datetime.date(2025, 4, 24))
        assert "IPL score" in html

    def test_empty_timeline_renders_without_error(self) -> None:
        analysis = self._sample_analysis()
        analysis["timeline"] = []
        analysis["drift_triggers"] = []
        html = _build_html_report(analysis, datetime.date(2025, 4, 24))
        assert "<!DOCTYPE html>" in html

    def test_score_color_green_for_high(self) -> None:
        assert _score_color(8.0) == "#1d9e75"

    def test_score_color_amber_for_mid(self) -> None:
        assert _score_color(5.0) == "#ba7517"

    def test_score_color_red_for_low(self) -> None:
        assert _score_color(2.0) == "#e24b4a"


class TestDailyAnalyserParseResponse:
    def _make_analyser(self, test_config, db) -> DailyAnalyser:
        # Write required prompt template
        test_config.paths.prompts_dir.mkdir(parents=True, exist_ok=True)
        template_path = test_config.paths.prompts_dir / "daily-analysis.txt"
        template_path.write_text(
            "{goals_section}\n{snapshots_json}\n{corrections_section}"
        )
        return DailyAnalyser(config=test_config, db=db)

    def test_parse_valid_json(self, test_config, db) -> None:
        analyser = self._make_analyser(test_config, db)
        valid = json.dumps({
            "day_score": 6.0,
            "timeline": [],
            "narrative": "test narrative",
            "drift_triggers": [],
        })
        result = analyser._parse_response(valid)
        assert result["day_score"] == 6.0

    def test_parse_strips_markdown_fences(self, test_config, db) -> None:
        analyser = self._make_analyser(test_config, db)
        fenced = "```json\n" + json.dumps({
            "day_score": 7.0,
            "timeline": [],
            "narrative": "ok",
            "drift_triggers": [],
        }) + "\n```"
        result = analyser._parse_response(fenced)
        assert result["day_score"] == 7.0

    def test_parse_invalid_json_raises(self, test_config, db) -> None:
        analyser = self._make_analyser(test_config, db)
        with pytest.raises(ValueError, match="invalid JSON"):
            analyser._parse_response("{not json}")

    def test_parse_missing_keys_raises(self, test_config, db) -> None:
        analyser = self._make_analyser(test_config, db)
        with pytest.raises(ValueError, match="missing required keys"):
            analyser._parse_response(json.dumps({"day_score": 5.0}))

    def test_parse_extracts_json_from_surrounding_text(self, test_config, db) -> None:
        """If LLM adds prose before/after the JSON, extract the JSON block."""
        analyser = self._make_analyser(test_config, db)
        wrapped = (
            "Here's the analysis:\n\n"
            + json.dumps({
                "day_score": 4.0,
                "timeline": [],
                "narrative": "yo",
                "drift_triggers": [],
            })
            + "\n\nHope that helps!"
        )
        result = analyser._parse_response(wrapped)
        assert result["day_score"] == 4.0


    def test_save_raw_response_writes_to_data_dir(self, test_config, db) -> None:
        analyser = self._make_analyser(test_config, db)
        path = analyser._save_raw_response("hello world", datetime.date(2026, 4, 27))
        assert Path(path).exists()
        assert "hello world" in Path(path).read_text()
        assert "2026-04-27" in str(path)

    def test_render_html_report_creates_file(self, test_config, db) -> None:
        analyser = self._make_analyser(test_config, db)
        minimal = {
            "day_score": 5.0,
            "score_reasoning": "ok",
            "active_minutes": 60,
            "deep_work_minutes": 30,
            "drift_minutes": 10,
            "rabbit_hole_minutes": 0,
            "meeting_minutes": 0,
            "break_minutes": 10,
            "longest_focus_streak_minutes": 30,
            "longest_focus_streak_start": "10:00",
            "video_count": 0,
            "video_minutes": 0,
            "ai_chat_minutes": 20,
            "ai_chat_on_goal_minutes": 20,
            "goals_comparison": [],
            "timeline": [],
            "drift_triggers": [],
            "narrative": "test",
            "pattern_observations": [],
        }
        day = datetime.date(2025, 4, 24)
        path = analyser._render_html_report(minimal, day)
        assert Path(path).exists()
        assert Path(path).read_text().startswith("<!DOCTYPE html>")

    def test_build_prompt_no_goals(self, test_config, db, today) -> None:
        analyser = self._make_analyser(test_config, db)
        snaps: list[Snapshot] = []
        prompt = analyser._build_prompt(snaps, None, [])
        assert "No goals" in prompt

    def test_build_prompt_with_goals(self, test_config, db, today) -> None:
        analyser = self._make_analyser(test_config, db)
        goal = Goal(day_date=today, raw_input="Work on Fullhouse")
        prompt = analyser._build_prompt([], goal, [])
        assert "Fullhouse" in prompt

    def test_build_prompt_with_real_template_has_literal_json(self, test_config, db, today) -> None:
        """The real daily-analysis.txt contains literal JSON schema with {} braces.
        _build_prompt must not treat those as format placeholders."""
        real_template = (
            Path(__file__).parent.parent.parent
            / "tracker" / "analysis" / "prompts" / "daily-analysis.txt"
        )
        test_config.paths.prompts_dir.mkdir(parents=True, exist_ok=True)
        (test_config.paths.prompts_dir / "daily-analysis.txt").write_text(
            real_template.read_text()
        )
        analyser = DailyAnalyser(config=test_config, db=db)
        goal = Goal(day_date=today, raw_input="Work on Fullhouse")
        prompt = analyser._build_prompt([], goal, [])
        assert "Fullhouse" in prompt
        assert '"day_score"' in prompt  # literal JSON schema preserved


class TestGoalParser:
    def test_returns_empty_when_no_api_key(self, test_config) -> None:
        from dataclasses import replace
        from tracker.config import ApiConfig
        cfg = Config_with_no_key(test_config)
        parser = GoalParser(cfg)
        result = parser.parse("Work on Fullhouse for 2 hours")
        assert result == []

    def test_parse_handles_llm_failure_gracefully(self, test_config) -> None:
        parser = GoalParser(test_config)
        with patch.object(parser, "_call_parser", side_effect=RuntimeError("API down")):
            result = parser.parse("some goals")
        assert result == []


class TestBuildUserContextSection:
    def test_includes_projects_and_distractions(self, test_config) -> None:
        section = _build_user_context_section(test_config)
        # test_config has Fullhouse + Arsenal/cricket
        assert "Fullhouse" in section
        assert "arsenal" in section.lower()
        assert "Work hours" in section

    def test_empty_config_returns_fallback(self, test_config) -> None:
        from dataclasses import replace
        from tracker.config import DistractionConfig
        cfg = replace(
            test_config,
            projects=[],
            people=[],
            distractions=DistractionConfig(keywords=[], domains=[]),
        )
        section = _build_user_context_section(cfg)
        # Still has work hours, but no project section
        assert "Projects" not in section


class TestParseRedditUrl:
    def test_non_reddit_url_returns_none(self) -> None:
        assert _parse_reddit_url("https://example.com/foo", None) is None

    def test_subreddit_extracted(self) -> None:
        result = _parse_reddit_url("https://www.reddit.com/r/Gunners/", None)
        assert result is not None
        assert result["subreddit"] == "Gunners"

    def test_post_slug_extracted(self) -> None:
        url = "https://www.reddit.com/r/Gunners/comments/abc123/saka_match_winner_again/"
        result = _parse_reddit_url(url, None)
        assert result["subreddit"] == "Gunners"
        assert "saka" in result["post_title"].lower()

    def test_page_title_used_when_no_slug(self) -> None:
        result = _parse_reddit_url(
            "https://www.reddit.com/r/Gunners/",
            "Saka match winner again : r/Gunners",
        )
        assert result["post_title"].startswith("Saka")


class TestComputeCaptureStats:
    def test_counts_per_app(self) -> None:
        from tracker.core.models import Snapshot
        snaps = [
            Snapshot(session_id=1, timestamp=datetime.datetime.now(),
                     app_name="Figma", text_field_sample=None, screenshot_path="/x.jpg"),
            Snapshot(session_id=1, timestamp=datetime.datetime.now(),
                     app_name="WhatsApp", text_field_sample="hello"),
            Snapshot(session_id=1, timestamp=datetime.datetime.now(),
                     app_name="WhatsApp", text_field_sample="world"),
        ]
        stats = _compute_capture_stats(snaps)
        assert stats["snapshots_total"] == 3
        assert stats["snapshots_with_text"] == 2
        assert stats["snapshots_with_screenshot"] == 1
        assert stats["per_app_capture"]["WhatsApp"]["text"] == 2
        assert stats["per_app_capture"]["Figma"]["screenshot"] == 1


def Config_with_no_key(config):
    """Return a config with empty API key."""
    from tracker.config import ApiConfig
    import dataclasses
    new_api = ApiConfig(
        anthropic_api_key="",
        anthropic_model=config.api.anthropic_model,
        aw_base_url=config.api.aw_base_url,
    )
    return dataclasses.replace(config, api=new_api)
