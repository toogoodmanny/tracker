"""
tracker/analysis/daily.py

End-of-day analysis pipeline.
Reads snapshots from DB, sends to Claude API, renders HTML report.

This module only does analysis — it never writes to the DB directly.
The CLI layer handles DB writes (report_path update).
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tracker.config import Config
from tracker.core.models import Correction, Goal, ParsedGoal, Snapshot
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)


class DailyAnalyser:
    """
    Runs the end-of-day LLM analysis pipeline.

    Flow:
    1. Load snapshots + goals + corrections for the day
    2. Build prompt from template
    3. Call Claude API
    4. Parse structured JSON response
    5. Render HTML report
    6. Return report path
    """

    def __init__(self, config: Config, db: Database) -> None:
        self._config = config
        self._db = db
        self._prompt_template = self._load_prompt("daily-analysis.txt")
        # Token + capture stats accumulated during a run, surfaced in the report.
        self._stats: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "screenshots_analysed": 0,
            "screenshots_skipped": 0,
        }

    def run(self, session_id: int, day_date: datetime.date) -> str:
        """
        Run the full daily analysis.
        Returns the path to the generated HTML report.
        Raises ValueError if API key is not configured.
        Raises RuntimeError if the LLM call fails after retries.
        """
        if not self._config.api.anthropic_api_key:
            raise ValueError("Anthropic API key not configured")

        # Run vision pipeline first so its results enrich the prompt.
        self._run_screenshot_analysis()

        snapshots = self._db.snapshots.get_by_session(session_id)
        if not snapshots:
            raise ValueError(f"No snapshots found for session {session_id}")

        goals = self._db.goals.get_for_day(day_date)
        corrections = self._db.corrections.get_by_day(day_date)

        capture_stats = _compute_capture_stats(snapshots)
        self._stats.update(capture_stats)

        prompt = self._build_prompt(snapshots, goals, corrections)
        logger.info(
            "Calling Claude API for daily analysis (%d snapshots, %d goals)",
            len(snapshots),
            len(self._db.goals.get_parsed_goals(day_date)),
        )

        raw_response = self._call_claude(prompt)
        # Save raw response to disk before parsing so a parse failure
        # doesn't waste the API call.
        raw_path = self._save_raw_response(raw_response, day_date)
        try:
            analysis = self._parse_response(raw_response)
        except ValueError as exc:
            # JSON parse failed — write a minimal fallback report so the user
            # still sees SOMETHING and knows where the raw file is.
            logger.warning("JSON parse failed: %s — writing fallback report", exc)
            analysis = _make_fallback_analysis(raw_response, str(exc), raw_path)

        # Inject run stats so the HTML report can render them.
        analysis["_run_stats"] = dict(self._stats)

        report_path = self._render_html_report(analysis, day_date)

        # File weekly observations from pattern_observations
        self._file_observations(analysis, day_date)

        return report_path

    def _run_screenshot_analysis(self) -> None:
        """Analyse pending screenshots and accumulate token usage."""
        from tracker.analysis.screenshot_analyser import ScreenshotAnalyser

        try:
            shot_analyser = ScreenshotAnalyser(self._config, self._db)
            result = shot_analyser.analyse_pending()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Screenshot analysis pipeline failed: %s", exc)
            return

        self._stats["screenshots_analysed"] = result.analysed_count
        self._stats["screenshots_skipped"] = result.skipped_count
        self._stats["input_tokens"] += result.input_tokens
        self._stats["output_tokens"] += result.output_tokens

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        snapshots: list[Snapshot],
        goals: Goal | None,
        corrections: list[Correction],
    ) -> str:
        goals_section = self._format_goals(goals)
        snapshots_json = self._format_snapshots(snapshots)
        corrections_section = self._format_corrections(corrections)
        user_context_section = _build_user_context_section(self._config)

        return (
            self._prompt_template
            .replace("{user_context_section}", user_context_section)
            .replace("{goals_section}", goals_section)
            .replace("{snapshots_json}", snapshots_json)
            .replace("{corrections_section}", corrections_section)
        )

    def _format_goals(self, goals: Goal | None) -> str:
        if goals is None:
            return "No goals were set for today."
        return f"Raw input: {goals.raw_input}\n\nParsed: {goals.parsed_json or 'not parsed'}"

    def _format_snapshots(self, snapshots: list[Snapshot]) -> str:
        """Convert snapshots to compact JSON, capped at 200 snapshots to fit context."""
        # Sample evenly if too many
        if len(snapshots) > 200:
            step = len(snapshots) // 200
            snapshots = snapshots[::step][:200]

        rows = []
        for s in snapshots:
            row: dict[str, Any] = {
                "t": s.timestamp.strftime("%H:%M:%S"),
                "app": s.app_name,
                "title": s.window_title,
            }
            if s.url:
                row["url"] = s.url
                reddit = _parse_reddit_url(s.url, s.page_title)
                if reddit is not None:
                    row["reddit"] = reddit
            if s.page_title:
                row["page"] = s.page_title
            if s.text_field_sample:
                row["typed"] = s.text_field_sample[-200:]
            if s.word_count is not None:
                row["wc"] = s.word_count
            if s.word_count_delta:
                row["wc_delta"] = s.word_count_delta
            if s.is_locked:
                row["locked"] = True
            if s.is_afk:
                row["afk"] = True
            if s.screenshot_analysis:
                row["screenshot"] = s.screenshot_analysis
            rows.append(row)

        return json.dumps(rows, ensure_ascii=False)

    def _format_corrections(self, corrections: list[Correction]) -> str:
        if not corrections:
            return "No corrections."
        lines = [f"- {c.correction_note}" for c in corrections]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Claude API call
    # ------------------------------------------------------------------

    def _call_claude(self, prompt: str) -> str:
        """
        Call the Claude API with the analysis prompt.
        Raises RuntimeError on API failure.
        """
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc

        client = anthropic.Anthropic(
            api_key=self._config.api.anthropic_api_key,
            max_retries=2,
            timeout=120.0,  # 2 min — fail fast rather than hanging for hours
        )

        try:
            message = client.messages.create(
                model=self._config.api.anthropic_model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as exc:
            raise RuntimeError(f"Invalid Anthropic API key: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError(f"Anthropic rate limit hit: {exc}") from exc
        except anthropic.APIError as exc:
            raise RuntimeError(f"Anthropic API error: {exc}") from exc

        content = message.content[0]
        if content.type != "text":
            raise RuntimeError(f"Unexpected content type: {content.type}")

        if message.stop_reason == "max_tokens":
            logger.warning(
                "Daily analysis hit max_tokens — response truncated. "
                "Consider increasing limit or shortening prompt."
            )

        usage = getattr(message, "usage", None)
        if usage is not None:
            self._stats["input_tokens"] += getattr(usage, "input_tokens", 0)
            self._stats["output_tokens"] += getattr(usage, "output_tokens", 0)

        return content.text

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """
        Parse the JSON response from Claude.
        Raises ValueError if parsing fails.
        """
        text = _extract_json_payload(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM returned invalid JSON: {exc}\nRaw (first 500 chars): {raw[:500]}"
            ) from exc

        # Validate required top-level keys
        required = {"day_score", "timeline", "narrative", "drift_triggers"}
        missing = required - set(data.keys())
        if missing:
            raise ValueError(f"LLM response missing required keys: {missing}")

        return data

    def _save_raw_response(self, raw: str, day_date: datetime.date) -> str:
        """Persist the raw LLM response so a parse failure doesn't waste the call."""
        raw_dir = self._config.paths.data_dir / "raw_responses"
        raw_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%H%M%S")
        path = raw_dir / f"daily-{day_date.isoformat()}-{ts}.txt"
        path.write_text(raw, encoding="utf-8")
        return str(path)

    # ------------------------------------------------------------------
    # HTML report rendering
    # ------------------------------------------------------------------

    def _render_html_report(
        self, analysis: dict[str, Any], day_date: datetime.date
    ) -> str:
        """
        Render the analysis dict as a self-contained HTML file.
        Returns the file path.
        """
        self._config.paths.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = self._config.paths.reports_dir / f"{day_date.isoformat()}.html"

        html = _build_html_report(analysis, day_date)
        report_path.write_text(html, encoding="utf-8")

        logger.info("Report written to %s", report_path)
        return str(report_path)

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def _file_observations(
        self, analysis: dict[str, Any], day_date: datetime.date
    ) -> None:
        """File pattern observations into the DB for weekly review."""
        from tracker.core.models import Observation, ObservationType

        for pattern in analysis.get("pattern_observations", []):
            obs = Observation(
                day_date=day_date,
                observation_type=ObservationType.PATTERN,
                detail=str(pattern),
            )
            try:
                self._db.observations.insert(obs)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not store observation: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_prompt(self, filename: str) -> str:
        path = self._config.paths.prompts_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Goal parser (uses same API client)
# ------------------------------------------------------------------

class GoalParser:
    """
    Parses free-text goal input into structured ParsedGoal objects.
    Used by `track plan` to enrich raw goals before storing.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        prompt_path = config.paths.prompts_dir / "goal-parser.txt"
        self._template = (
            prompt_path.read_text(encoding="utf-8")
            if prompt_path.exists()
            else "{raw_goals}"
        )

    def parse(self, raw_goals: str) -> list[ParsedGoal]:
        """
        Parse goals from free text.
        Returns empty list if API key not set or parsing fails.
        Never raises.
        """
        if not self._config.api.anthropic_api_key:
            return []

        try:
            return self._call_parser(raw_goals)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Goal parsing failed (will use raw text): %s", exc)
            return []

    def _call_parser(self, raw_goals: str) -> list[ParsedGoal]:
        import anthropic

        # Build a compact projects-only context for the goal parser.
        if self._config.projects:
            projects_lines = []
            for p in self._config.projects:
                kw = f" — keywords: {', '.join(p.keywords)}" if p.keywords else ""
                projects_lines.append(f"- {p.name}{kw}")
            projects_section = "\n".join(projects_lines)
        else:
            projects_section = "(no projects configured)"

        prompt = (
            self._template
            .replace("{user_projects_section}", projects_section)
            .replace("{raw_goals}", raw_goals)
        )
        client = anthropic.Anthropic(
            api_key=self._config.api.anthropic_api_key,
            max_retries=2,
            timeout=30.0,
        )

        message = client.messages.create(
            model=self._config.api.anthropic_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        text = message.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])

        raw_list: list[dict] = json.loads(text)

        result = []
        for g in raw_list:
            try:
                target_time = None
                if g.get("target_start_time"):
                    target_time = datetime.time.fromisoformat(g["target_start_time"])
                result.append(ParsedGoal(
                    description=g["description"],
                    project=g.get("project"),
                    estimated_minutes=g.get("estimated_minutes"),
                    target_start_time=target_time,
                ))
            except (KeyError, ValueError) as exc:
                logger.debug("Skipping malformed goal item: %s", exc)

        return result


# ------------------------------------------------------------------
# HTML report builder (pure function — no I/O)
# ------------------------------------------------------------------

def _build_user_context_section(config: Config) -> str:
    """
    Render the user-context block injected into prompts.
    Pulls from config (projects, people, distractions) AND from CLAUDE.md
    if present (free-text personal context).
    """
    lines: list[str] = []

    if config.projects:
        lines.append("**Projects:**")
        for p in config.projects:
            kw = ", ".join(p.keywords) if p.keywords else ""
            kw_part = f" (keywords: {kw})" if kw else ""
            desc = f" — {p.description}" if p.description else ""
            lines.append(f"- {p.name}{desc}{kw_part}")
        lines.append("")

    if config.people:
        lines.append("**Team / people:**")
        for person in config.people:
            role = f" ({person.role})" if person.role else ""
            project = f" [{person.project}]" if person.project else ""
            lines.append(f"- {person.name}{role}{project}")
        lines.append("")

    distractions = config.distractions
    if distractions.keywords or distractions.domains:
        lines.append("**Known distractions — flag these aggressively when they appear during work hours:**")
        if distractions.keywords:
            lines.append(f"- Topics: {', '.join(distractions.keywords)}")
        if distractions.domains:
            lines.append(f"- Domains: {', '.join(distractions.domains)}")
        lines.append("")

    sched = config.schedule
    lines.append(
        f"**Work hours:** {sched.work_start_hour:02d}:00 to {sched.work_end_hour:02d}:00 "
        f"(late session window: {sched.late_session_start_hour:02d}:00 to "
        f"{sched.late_session_end_hour % 24:02d}:00 next day)"
    )
    lines.append("")

    # Append free-text personal context from CLAUDE.md if it exists.
    claude_md = config.paths.claude_md_path
    if claude_md.exists():
        try:
            text = claude_md.read_text(encoding="utf-8").strip()
            if text:
                lines.append("**Personal context (from CLAUDE.md):**")
                lines.append(text)
                lines.append("")
        except OSError:
            pass

    if not lines:
        return "(No user context configured. Run `track setup` to personalize this.)"

    return "\n".join(lines).strip()


def _parse_reddit_url(url: str, page_title: str | None) -> dict[str, str] | None:
    """
    Extract subreddit + post title from a Reddit URL.
    Returns {"subreddit": "...", "post_title": "..."} or None if not a Reddit URL.
    Post title is best-effort: pulled from URL slug or page title.
    """
    if not url:
        return None
    lowered = url.lower()
    if "reddit.com" not in lowered:
        return None

    import re

    subreddit_match = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", url)
    if not subreddit_match:
        return None
    subreddit = subreddit_match.group(1)

    post_title = ""
    # /r/foo/comments/<id>/<slug>/...
    slug_match = re.search(
        r"reddit\.com/r/[A-Za-z0-9_]+/comments/[A-Za-z0-9]+/([A-Za-z0-9_\-]+)",
        url,
    )
    if slug_match:
        post_title = slug_match.group(1).replace("_", " ").replace("-", " ").strip()

    if not post_title and page_title:
        # Reddit page titles look like: "Foo bar baz : r/Subreddit"
        cleaned = page_title.split(" : r/")[0].strip()
        if cleaned and cleaned.lower() != "reddit":
            post_title = cleaned

    return {"subreddit": subreddit, "post_title": post_title}


def _compute_capture_stats(snapshots: list[Snapshot]) -> dict[str, Any]:
    """
    Per-app counts: total snapshots, snapshots with text-field samples, screenshots.
    Returns a dict suitable for merging into _stats.
    """
    per_app: dict[str, dict[str, int]] = {}
    total = len(snapshots)
    with_text = 0
    with_screenshot = 0

    for s in snapshots:
        app = s.app_name or "unknown"
        bucket = per_app.setdefault(app, {"total": 0, "text": 0, "screenshot": 0})
        bucket["total"] += 1
        if s.text_field_sample:
            bucket["text"] += 1
            with_text += 1
        if s.screenshot_path:
            bucket["screenshot"] += 1
            with_screenshot += 1

    return {
        "snapshots_total": total,
        "snapshots_with_text": with_text,
        "snapshots_with_screenshot": with_screenshot,
        "per_app_capture": per_app,
    }


def _make_fallback_analysis(raw: str, error: str, raw_path: str) -> dict[str, Any]:
    """
    When JSON parsing fails, return a minimal analysis dict that still
    renders a (partial) HTML report so the user sees something useful.
    The raw LLM text is embedded so nothing is lost.
    """
    return {
        "day_score": 0,
        "score_reasoning": f"Report generation failed — could not parse LLM response.\nError: {error}",
        "active_minutes": 0,
        "deep_work_minutes": 0,
        "drift_minutes": 0,
        "rabbit_hole_minutes": 0,
        "waste_of_time_minutes": 0,
        "break_minutes": 0,
        "afk_minutes": 0,
        "narrative": (
            f"<strong>⚠️ JSON parse failed.</strong> The raw Claude response is saved at:<br>"
            f"<code>{raw_path}</code><br><br>"
            f"Raw response preview:<br><pre style='font-size:0.78rem;white-space:pre-wrap'>"
            f"{raw[:2000].replace('<', '&lt;').replace('>', '&gt;')}"
            f"{'…' if len(raw) > 2000 else ''}</pre>"
        ),
        "timeline": [],
        "drift_triggers": [],
        "goals_comparison": [],
        "_run_stats": {},
        "_parse_error": True,
    }


def _extract_json_payload(raw: str) -> str:
    """
    Pull out the JSON object from an LLM response that may have:
    - leading/trailing whitespace
    - ```json ... ``` markdown fences
    - prose before or after the JSON
    Returns the JSON substring (or the original text if no obvious JSON found).
    """
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    # If there's still surrounding prose, grab the largest {...} block
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1]
    return text


def _build_html_report(analysis: dict[str, Any], day_date: datetime.date) -> str:
    """Build a self-contained HTML report from the analysis dict."""

    score = analysis.get("day_score", 0)
    score_color = _score_color(score)
    timeline_rows = _build_timeline_rows(analysis.get("timeline", []))
    drift_rows = _build_drift_rows(analysis.get("drift_triggers", []))
    goals_rows = _build_goals_rows(analysis.get("goals_comparison", []))
    bar_chart = _build_bar_chart(analysis)
    narrative = analysis.get("narrative", "")
    score_reasoning = analysis.get("score_reasoning", "")
    stats_panel = _build_stats_panel(analysis.get("_run_stats", {}))

    deep_work_h = round(analysis.get("deep_work_minutes", 0) / 60, 1)
    waste_min = (
        analysis.get("drift_minutes", 0)
        + analysis.get("waste_of_time_minutes", 0)
        + analysis.get("rabbit_hole_minutes", 0)
    )
    drift_h = round(waste_min / 60, 1)
    rabbit_min = analysis.get("rabbit_hole_minutes", 0)
    waste_only_min = analysis.get("waste_of_time_minutes", 0)
    drift_only_min = analysis.get("drift_minutes", 0)
    longest = analysis.get("longest_focus_streak_minutes", 0)
    longest_start = analysis.get("longest_focus_streak_start", "")
    video_count = analysis.get("video_count", 0)
    ai_total = analysis.get("ai_chat_minutes", 0)
    ai_on_goal = analysis.get("ai_chat_on_goal_minutes", 0)
    ai_off = ai_total - ai_on_goal

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tracker — {day_date.strftime('%A, %d %B %Y')}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f0; color: #1a1a1a; line-height: 1.6; padding: 2rem; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; font-weight: 500; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1rem; font-weight: 500; color: #555; margin: 1.5rem 0 0.75rem; }}
  .meta {{ font-size: 0.85rem; color: #888; margin-bottom: 1.5rem; }}
  .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; margin-bottom: 1.5rem; }}
  .metric {{ background: #fff; border: 0.5px solid #e0e0d8; border-radius: 8px; padding: 1rem; }}
  .metric .label {{ font-size: 0.7rem; color: #999; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 4px; }}
  .metric .value {{ font-size: 1.4rem; font-weight: 500; }}
  .metric .sub {{ font-size: 0.75rem; color: #aaa; margin-top: 2px; }}
  .score-badge {{ background: {score_color}20; color: {score_color}; padding: 2px 10px; border-radius: 6px; }}
  .section {{ background: #fff; border: 0.5px solid #e0e0d8; border-radius: 8px;
              padding: 1.25rem 1.5rem; margin-bottom: 1rem; }}
  table {{ width: 100%; font-size: 0.83rem; border-collapse: collapse; }}
  th {{ text-align: left; font-weight: 500; font-size: 0.75rem; color: #999;
       text-transform: uppercase; letter-spacing: .04em; padding: 0 0 0.5rem; border-bottom: 0.5px solid #eee; }}
  td {{ padding: 0.6rem 0.5rem 0.6rem 0; border-bottom: 0.5px solid #f5f5f0; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  .cat {{ display: inline-block; font-size: 0.7rem; font-weight: 500; padding: 2px 7px;
          border-radius: 4px; white-space: nowrap; }}
  .cat-deep_work {{ background: #e1f5ee; color: #085041; }}
  .cat-light_work {{ background: #e6f1fb; color: #0c447c; }}
  .cat-research {{ background: #eeedfe; color: #3c3489; }}
  .cat-drift {{ background: #fcebeb; color: #791f1f; }}
  .cat-waste_of_time {{ background: #fce5d4; color: #8a4318; }}
  .cat-rabbit_hole {{ background: #faeeda; color: #633806; }}
  .cat-meeting {{ background: #eeedfe; color: #3c3489; }}
  .cat-break {{ background: #f1efe8; color: #444441; }}
  .cat-locked {{ background: #f1efe8; color: #888; }}
  .cat-unknown {{ background: #f1efe8; color: #888; }}
  .status-done {{ color: #1d9e75; font-weight: 500; }}
  .status-partial {{ color: #ba7517; font-weight: 500; }}
  .status-missed {{ color: #e24b4a; font-weight: 500; }}
  .status-not_started {{ color: #e24b4a; font-weight: 500; }}
  .narrative {{ background: #fff8f0; border-left: 3px solid #e24b4a; border-radius: 8px;
                padding: 1rem 1.25rem; font-size: 0.9rem; line-height: 1.7; margin-bottom: 1rem; }}
  .bar-row {{ display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; font-size: 0.83rem; }}
  .bar-label {{ width: 120px; text-align: right; color: #666; flex-shrink: 0; }}
  .bar-track {{ flex: 1; height: 8px; background: #f0f0e8; border-radius: 4px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; }}
  .bar-mins {{ width: 50px; color: #aaa; font-size: 0.75rem; }}
  details {{ cursor: pointer; }}
  details summary {{ list-style: none; }}
  details summary::-webkit-details-marker {{ display: none; }}
</style>
</head>
<body>
<div class="wrap">

<h1>{day_date.strftime('%A, %d %B %Y')}</h1>
<p class="meta">
  {analysis.get('active_minutes', 0)} active minutes &nbsp;·&nbsp;
  Generated by tracker
</p>

<div class="metrics">
  <div class="metric">
    <div class="label">Day score</div>
    <div class="value"><span class="score-badge">{score:.1f} / 10</span></div>
    <div class="sub">{score_reasoning[:60]}...</div>
  </div>
  <div class="metric">
    <div class="label">Deep work</div>
    <div class="value">{deep_work_h}h</div>
    <div class="sub">Longest streak: {longest}m at {longest_start}</div>
  </div>
  <div class="metric">
    <div class="label">Off-task time</div>
    <div class="value">{drift_h}h</div>
    <div class="sub">Drift {drift_only_min}m · Waste {waste_only_min}m · Rabbit hole {rabbit_min}m</div>
  </div>
  <div class="metric">
    <div class="label">AI chats</div>
    <div class="value">{round(ai_total/60,1)}h</div>
    <div class="sub">On-goal: {round(ai_on_goal/60,1)}h · Off: {round(ai_off/60,1)}h</div>
  </div>
</div>

<div class="narrative">{narrative}</div>

<h2>Goals vs reality</h2>
<div class="section">
  <table>
    <tr><th>Goal</th><th>Target</th><th>Actual</th><th>Status</th><th>Note</th></tr>
    {goals_rows}
  </table>
</div>

<h2>Full timeline <small style="font-weight:400;color:#aaa;font-size:0.8rem">(click to expand)</small></h2>
<div class="section">
  <table>
    <tr><th>Time</th><th>Duration</th><th>Category</th><th>Description</th></tr>
    {timeline_rows}
  </table>
</div>

<h2>Drift triggers</h2>
<div class="section">
  <table>
    <tr><th>Time</th><th>What triggered it</th><th>Abandoned</th><th>Lost</th><th>Recovery</th></tr>
    {drift_rows}
  </table>
</div>

<h2>Time distribution</h2>
<div class="section">
  {bar_chart}
</div>

<h2>Run stats</h2>
<div class="section">
  {stats_panel}
</div>

</div>
</body>
</html>"""


def _score_color(score: float) -> str:
    if score >= 7:
        return "#1d9e75"
    if score >= 4:
        return "#ba7517"
    return "#e24b4a"


def _build_timeline_rows(timeline: list[dict]) -> str:
    rows = []
    for block in timeline:
        cat = block.get("category", "unknown")
        desc = block.get("description", "")
        drift_trigger = block.get("drift_trigger")
        detail = f"{desc}"
        if drift_trigger:
            detail += f"<br><small style='color:#e24b4a'>↳ triggered by: {drift_trigger}</small>"

        rows.append(f"""<tr>
          <td style="white-space:nowrap;color:#888">{block.get('start_time','')}</td>
          <td style="white-space:nowrap">{block.get('duration_minutes',0)}m</td>
          <td><span class="cat cat-{cat}">{cat.replace('_',' ')}</span></td>
          <td>{detail}</td>
        </tr>""")
    return "\n".join(rows)


def _build_drift_rows(drifts: list[dict]) -> str:
    rows = []
    for d in drifts:
        rows.append(f"""<tr>
          <td style="color:#888;white-space:nowrap">{d.get('time','')}</td>
          <td>{d.get('trigger','')}</td>
          <td style="color:#888">{d.get('what_was_abandoned','')}</td>
          <td style="white-space:nowrap;color:#e24b4a">{d.get('duration_minutes',0)}m</td>
          <td style="color:#888">{d.get('recovery','')}</td>
        </tr>""")
    return "\n".join(rows) if rows else "<tr><td colspan='5' style='color:#aaa'>No drift events recorded.</td></tr>"


def _build_goals_rows(goals: list[dict]) -> str:
    rows = []
    for g in goals:
        status = g.get("status", "")
        target = f"{g.get('target_minutes','?')}m" if g.get("target_minutes") else "—"
        rows.append(f"""<tr>
          <td>{g.get('goal','')}</td>
          <td style="white-space:nowrap">{target}</td>
          <td style="white-space:nowrap">{g.get('actual_minutes',0)}m</td>
          <td class="status-{status}">{status}</td>
          <td style="color:#888">{g.get('note','')}</td>
        </tr>""")
    return "\n".join(rows) if rows else "<tr><td colspan='5' style='color:#aaa'>No goals set.</td></tr>"


def _build_stats_panel(stats: dict[str, Any]) -> str:
    """
    Render token usage + capture stats. `stats` is the `_run_stats` dict
    populated during DailyAnalyser.run(). Missing keys render as zeros.
    """
    in_tok = stats.get("input_tokens", 0)
    out_tok = stats.get("output_tokens", 0)
    snaps_total = stats.get("snapshots_total", 0)
    with_text = stats.get("snapshots_with_text", 0)
    with_shot = stats.get("snapshots_with_screenshot", 0)
    shots_done = stats.get("screenshots_analysed", 0)
    shots_skip = stats.get("screenshots_skipped", 0)
    per_app = stats.get("per_app_capture", {}) or {}

    summary_rows = "".join([
        f"<tr><td>Input tokens</td><td style='text-align:right'>{in_tok:,}</td></tr>",
        f"<tr><td>Output tokens</td><td style='text-align:right'>{out_tok:,}</td></tr>",
        f"<tr><td>Snapshots recorded</td><td style='text-align:right'>{snaps_total}</td></tr>",
        f"<tr><td>Snapshots with text capture</td><td style='text-align:right'>{with_text}</td></tr>",
        f"<tr><td>Screenshots taken</td><td style='text-align:right'>{with_shot}</td></tr>",
        f"<tr><td>Screenshots analysed</td><td style='text-align:right'>{shots_done}</td></tr>",
        f"<tr><td>Screenshots skipped</td><td style='text-align:right'>{shots_skip}</td></tr>",
    ])

    # Sort apps by total snapshots descending, top 10 only
    top_apps = sorted(per_app.items(), key=lambda kv: -kv[1].get("total", 0))[:10]
    app_rows = "".join(
        f"<tr><td>{app}</td>"
        f"<td style='text-align:right'>{counts.get('total', 0)}</td>"
        f"<td style='text-align:right'>{counts.get('text', 0)}</td>"
        f"<td style='text-align:right'>{counts.get('screenshot', 0)}</td></tr>"
        for app, counts in top_apps
    ) or "<tr><td colspan='4' style='color:#aaa'>No capture data.</td></tr>"

    return f"""
    <table style="margin-bottom:1rem">
      {summary_rows}
    </table>
    <table>
      <tr><th>App</th><th style='text-align:right'>Snapshots</th>
          <th style='text-align:right'>Text capture</th>
          <th style='text-align:right'>Screenshots</th></tr>
      {app_rows}
    </table>
    """


def _build_bar_chart(analysis: dict[str, Any]) -> str:
    categories = [
        ("Deep work", analysis.get("deep_work_minutes", 0), "#1d9e75"),
        ("Light work", analysis.get("active_minutes", 0) - analysis.get("deep_work_minutes", 0)
         - analysis.get("drift_minutes", 0) - analysis.get("waste_of_time_minutes", 0)
         - analysis.get("rabbit_hole_minutes", 0)
         - analysis.get("meeting_minutes", 0) - analysis.get("break_minutes", 0), "#378add"),
        ("Meetings", analysis.get("meeting_minutes", 0), "#7f77dd"),
        ("Drift", analysis.get("drift_minutes", 0), "#e24b4a"),
        ("Waste of time", analysis.get("waste_of_time_minutes", 0), "#d97842"),
        ("Rabbit holes", analysis.get("rabbit_hole_minutes", 0), "#ba7517"),
        ("Breaks", analysis.get("break_minutes", 0), "#888780"),
    ]
    total = max(analysis.get("active_minutes", 1), 1)
    rows = []
    for label, mins, color in categories:
        if mins <= 0:
            continue
        pct = min(100, round(mins / total * 100))
        rows.append(f"""<div class="bar-row">
          <div class="bar-label">{label}</div>
          <div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>
          <div class="bar-mins">{mins}m</div>
        </div>""")
    return "\n".join(rows)
