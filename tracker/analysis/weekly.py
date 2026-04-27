"""
tracker/analysis/weekly.py

Weekly analysis pipeline.
Aggregates seven daily reports + observations → LLM → weekly HTML report + CLAUDE.md update.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any

from tracker.config import Config
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)


class WeeklyAnalyser:
    """
    Runs the end-of-week analysis.

    Flow:
    1. Load daily reports for the past 7 days
    2. Load micro-observations and corrections
    3. Ask user 3-4 interactive questions
    4. Call Claude API
    5. Render weekly HTML report
    6. Update CLAUDE.md
    """

    def __init__(self, config: Config, db: Database) -> None:
        self._config = config
        self._db = db
        self._prompt_template = self._load_prompt("weekly-analysis.txt")

    def run(self, ask_questions_fn: callable) -> str:
        """
        Run the full weekly analysis.
        ask_questions_fn: callable that takes list of questions, returns dict of answers.
        Returns path to weekly HTML report.
        Raises ValueError if API key not configured.
        """
        if not self._config.api.anthropic_api_key:
            raise ValueError("Anthropic API key not configured")

        today = datetime.date.today()
        week_start = today - datetime.timedelta(days=6)

        # Load data
        recent_days = self._db.sessions.get_recent_days(7)
        daily_summaries = self._load_daily_summaries(recent_days)
        observations = self._db.observations.get_unused_for_weekly(week_start)
        corrections = self._db.corrections.get_unused_weekly(week_start)

        # Interactive questions based on patterns
        questions = self._generate_questions(daily_summaries, corrections)
        user_answers = ask_questions_fn(questions)

        prompt = self._build_prompt(
            daily_summaries, observations, corrections, user_answers
        )

        logger.info("Calling Claude API for weekly analysis")
        raw_response = self._call_claude(prompt)
        analysis = self._parse_response(raw_response)

        report_path = self._render_weekly_report(analysis, today)
        self._update_claude_md(analysis)

        # Mark observations as used
        obs_ids = [o.id for o in observations if o.id is not None]
        if obs_ids:
            self._db.observations.mark_used_in_weekly(obs_ids)

        return report_path

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_daily_summaries(
        self, days: list[datetime.date]
    ) -> list[dict[str, Any]]:
        """Load existing daily report data for each day."""
        summaries = []
        for day in days:
            sessions = self._db.sessions.get_by_day(day)
            if not sessions:
                continue
            snapshots = []
            for s in sessions:
                if s.id is not None:
                    snapshots.extend(self._db.snapshots.get_by_session(s.id))

            goals = self._db.goals.get_for_day(day)
            corrections = self._db.corrections.get_by_day(day)

            summaries.append({
                "date": day.isoformat(),
                "day_name": day.strftime("%A"),
                "snapshot_count": len(snapshots),
                "goals": goals.raw_input if goals else None,
                "corrections": [c.correction_note for c in corrections],
                "report_path": sessions[0].report_path if sessions else None,
            })

        return summaries

    def _generate_questions(
        self,
        summaries: list[dict],
        corrections: list[Any],
    ) -> list[str]:
        """Generate 3-4 contextual questions for the user."""
        questions = []

        if len(corrections) > 3:
            questions.append(
                f"The tracker made {len(corrections)} corrections this week. "
                "Are there systematic misclassifications we should fix in the config?"
            )

        questions.append(
            "What was the biggest blocker or distraction pattern you noticed this week "
            "that the tracker might have missed or misunderstood?"
        )

        questions.append(
            "Did you make any intentional changes to how you worked this week? "
            "(new routines, different tools, etc.)"
        )

        questions.append(
            "What would you most want the weekly report to highlight differently next week?"
        )

        return questions[:4]

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        daily_summaries: list[dict],
        observations: list[Any],
        corrections: list[Any],
        user_answers: dict[str, str],
    ) -> str:
        observations_json = json.dumps(
            [{"type": o.observation_type.value, "detail": o.detail, "day": o.day_date.isoformat()}
             for o in observations],
            indent=2,
        )
        corrections_json = json.dumps(
            [{"note": c.correction_note, "day": c.day_date.isoformat()}
             for c in corrections],
            indent=2,
        )
        from tracker.analysis.daily import _build_user_context_section
        user_context_section = _build_user_context_section(self._config)

        return (
            self._prompt_template
            .replace("{user_context_section}", user_context_section)
            .replace("{daily_summaries_json}", json.dumps(daily_summaries, indent=2))
            .replace("{observations_json}", observations_json)
            .replace("{corrections_json}", corrections_json)
            .replace("{user_answers}", json.dumps(user_answers, indent=2))
        )

    # ------------------------------------------------------------------
    # API call
    # ------------------------------------------------------------------

    def _call_claude(self, prompt: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc

        client = anthropic.Anthropic(api_key=self._config.api.anthropic_api_key)
        try:
            message = client.messages.create(
                model=self._config.api.anthropic_model,
                max_tokens=6000,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            raise RuntimeError(f"Anthropic API error: {exc}") from exc

        return message.content[0].text

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Weekly LLM returned invalid JSON: {exc}") from exc

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _render_weekly_report(
        self, analysis: dict[str, Any], today: datetime.date
    ) -> str:
        self._config.paths.reports_dir.mkdir(parents=True, exist_ok=True)
        week_num = today.isocalendar()[1]
        report_path = self._config.paths.reports_dir / f"week-{today.year}-{week_num:02d}.html"

        html = _build_weekly_html(analysis, today)
        report_path.write_text(html, encoding="utf-8")
        logger.info("Weekly report written to %s", report_path)
        return str(report_path)

    def _update_claude_md(self, analysis: dict[str, Any]) -> None:
        """Write updated CLAUDE.md based on weekly analysis."""
        claude_data = analysis.get("claude_md_update", {})
        if not claude_data:
            return

        # Build About-me + Team sections from the user's config so we never
        # overwrite their CLAUDE.md with someone else's personal context.
        project_lines = "\n".join(
            f"- **{p.name}** — {p.description or 'no description'}"
            for p in self._config.projects
        ) or "_No projects configured. Run `track setup` to add them._"

        people_lines = "\n".join(
            f"- {p.name} ({p.role})" + (f" [{p.project}]" if p.project else "")
            for p in self._config.people
        ) or "_No team configured._"

        distraction_kw = ", ".join(self._config.distractions.keywords) or "_none specified_"

        content = f"""# CLAUDE.md — Personal context for AI assistants
<!-- Auto-updated weekly by tracker -->
<!-- Last updated: {datetime.date.today().isoformat()} -->

## Projects

{project_lines}

## Team / collaborators

{people_lines}

## Known distractions during work hours

{distraction_kw}

## Behavioural patterns (from time tracking)

{claude_data.get('behavioral_patterns', '')}

**Best focus hours:** {claude_data.get('best_focus_hours', 'unknown')}

**Common drift triggers:** {', '.join(claude_data.get('common_drift_triggers', []))}

**Deep work capacity:** {claude_data.get('typical_deep_work_capacity', 'unknown')}

**Project time distribution:** {claude_data.get('project_time_distribution', 'unknown')}

## How to work with me effectively

- Be direct and specific. Name the problem, not a vague observation.
- If I'm consuming content from the distractions list above during work hours, redirect me.
- Push back if I'm planning too many tasks for the day.
- Don't suggest things I already know — get to the actionable part.
"""

        self._config.paths.claude_md_path.write_text(content, encoding="utf-8")
        logger.info("CLAUDE.md updated at %s", self._config.paths.claude_md_path)

    def _load_prompt(self, filename: str) -> str:
        path = self._config.paths.prompts_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Weekly HTML builder
# ------------------------------------------------------------------

def _build_weekly_html(analysis: dict[str, Any], today: datetime.date) -> str:
    week_score = analysis.get("week_score", 0)
    score_color = "#1d9e75" if week_score >= 7 else "#ba7517" if week_score >= 4 else "#e24b4a"
    narrative = analysis.get("weekly_narrative", "")
    patterns = analysis.get("patterns", [])
    changes = analysis.get("three_changes_for_next_week", [])
    triggers = analysis.get("top_drift_triggers", [])

    pattern_html = "\n".join(
        f'<li style="margin-bottom:0.5rem"><strong>{p.get("severity","").upper()}</strong>: '
        f'{p.get("pattern","")} <span style="color:#aaa;font-size:0.8rem">— {p.get("evidence","")}</span></li>'
        for p in patterns
    )
    changes_html = "\n".join(f"<li>{c}</li>" for c in changes)
    triggers_html = "\n".join(
        f'<tr><td>{t.get("trigger","")}</td><td style="text-align:center">{t.get("occurrences",0)}×</td>'
        f'<td style="text-align:right;color:#e24b4a">{t.get("total_minutes_lost",0)}m</td></tr>'
        for t in triggers
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Tracker — Week of {today.isoformat()}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f0; color: #1a1a1a; line-height: 1.6; padding: 2rem; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; font-weight: 500; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1rem; font-weight: 500; color: #555; margin: 1.5rem 0 0.75rem; }}
  .section {{ background: #fff; border: 0.5px solid #e0e0d8; border-radius: 8px;
              padding: 1.25rem 1.5rem; margin-bottom: 1rem; }}
  .metrics {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 0.75rem; margin-bottom: 1.5rem; }}
  .metric {{ background: #fff; border: 0.5px solid #e0e0d8; border-radius: 8px; padding: 1rem; }}
  .metric .label {{ font-size: 0.7rem; color: #999; text-transform: uppercase; letter-spacing:.04em; margin-bottom:4px; }}
  .metric .value {{ font-size: 1.4rem; font-weight: 500; }}
  .narrative {{ background: #fff8f0; border-left: 3px solid #e24b4a; border-radius: 8px;
                padding: 1rem 1.25rem; font-size: 0.9rem; line-height: 1.7; margin-bottom: 1rem; }}
  ul {{ padding-left: 1.25rem; }}
  li {{ margin-bottom: 0.25rem; font-size: 0.9rem; }}
  table {{ width: 100%; font-size: 0.85rem; border-collapse: collapse; }}
  th {{ text-align: left; font-size: 0.75rem; color: #999; text-transform: uppercase;
       letter-spacing:.04em; padding: 0 0 0.5rem; border-bottom: 0.5px solid #eee; }}
  td {{ padding: 0.5rem 0.25rem; border-bottom: 0.5px solid #f5f5f0; }}
</style>
</head>
<body>
<div class="wrap">
<h1>Week ending {today.strftime('%d %B %Y')}</h1>
<p style="font-size:0.85rem;color:#888;margin-bottom:1.5rem">Weekly tracker report</p>

<div class="metrics">
  <div class="metric">
    <div class="label">Week score</div>
    <div class="value" style="color:{score_color}">{week_score:.1f}/10</div>
  </div>
  <div class="metric">
    <div class="label">Deep work</div>
    <div class="value">{analysis.get('total_deep_work_hours',0):.1f}h</div>
  </div>
  <div class="metric">
    <div class="label">Drift total</div>
    <div class="value">{analysis.get('total_drift_hours',0):.1f}h</div>
  </div>
  <div class="metric">
    <div class="label">Avg day score</div>
    <div class="value">{analysis.get('average_day_score',0):.1f}/10</div>
  </div>
</div>

<div class="narrative">{narrative}</div>

<h2>Patterns identified</h2>
<div class="section"><ul>{pattern_html}</ul></div>

<h2>Top drift triggers this week</h2>
<div class="section">
  <table>
    <tr><th>Trigger</th><th style="text-align:center">Times</th><th style="text-align:right">Minutes lost</th></tr>
    {triggers_html}
  </table>
</div>

<h2>Three changes for next week</h2>
<div class="section"><ol style="padding-left:1.25rem">{changes_html}</ol></div>

</div></body></html>"""
