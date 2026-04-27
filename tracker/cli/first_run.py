"""
tracker/cli/first_run.py

Handles first-run experience: creates config, asks personalization questions,
checks permissions, bootstraps CLAUDE.md with the user's answers.

Called automatically by `track start` when no config exists.
Also callable directly: `track setup`.
"""

from __future__ import annotations

import datetime
import json
import platform
from pathlib import Path

from rich.console import Console

from tracker.cli.output import (
    print_error,
    print_header,
    print_info,
    print_separator,
    print_success,
    print_warning,
)
from tracker.cli.prompts import ask_confirmation, ask_single_line
from tracker.config import write_default_config

console = Console()


def run_first_time_setup(config_path: Path | None = None) -> bool:
    """
    Interactive first-run setup.
    Returns True if setup completed, False if user aborted.
    """
    print_header("Tracker — first-time setup")
    console.print()
    console.print("This takes about 5 minutes. Your answers personalize the daily reports.")
    console.print("[dim]Press Ctrl-C anytime to abort. You can re-run `track setup` later.[/dim]")
    console.print()

    # 1. Write default config
    path = write_default_config(config_path)
    print_success(f"Config created at: {path}")

    # 2. API key
    print_separator()
    console.print()
    console.print("[bold]Step 1 of 7 — Anthropic API key[/bold]")
    console.print("[dim]Required for daily reports. Get one at:[/dim]")
    console.print("[dim]  https://console.anthropic.com/api-keys[/dim]")
    console.print()
    api_key = ask_single_line("Paste your Anthropic API key (or Enter to skip)")
    if api_key.strip():
        _update_config_value(path, "anthropic_api_key", api_key.strip())
        print_success("API key saved.")
    else:
        print_warning("Skipped. Add it later to ~/.tracker/config.json")

    # 3. Personalization questionnaire
    answers = _run_questionnaire()
    _save_questionnaire_to_config(path, answers)

    # 4. macOS permissions
    if platform.system() == "Darwin":
        print_separator()
        console.print()
        console.print("[bold]Step 6 of 7 — macOS permissions[/bold]")
        console.print()
        console.print("The tracker needs 3 permissions granted to Terminal:")
        console.print("  1. [yellow]Accessibility[/yellow] — reads window titles + text fields")
        console.print("  2. [yellow]Screen Recording[/yellow] — captures screenshots")
        console.print("  3. [yellow]Full Disk Access[/yellow] — reads document files")
        console.print()
        console.print("Open: [bold]System Settings → Privacy & Security[/bold]")
        console.print("Add Terminal to each section above.")
        console.print()
        input("Press Enter once permissions are granted (or Enter to skip for now)... ")

    # 5. ActivityWatch + Chrome extension reminder
    print_separator()
    console.print()
    console.print("[bold]Step 7 of 7 — ActivityWatch + Chrome extension[/bold]")
    _check_activitywatch()
    console.print()
    extension_path = Path(__file__).parent.parent.parent / "chrome_extension"
    console.print(f"Chrome extension folder: [bold]{extension_path}[/bold]")
    console.print("To load it:")
    console.print("  1. Open Chrome → chrome://extensions")
    console.print("  2. Toggle 'Developer mode' on (top right)")
    console.print("  3. Click 'Load unpacked' → select the folder above")

    # 6. Bootstrap CLAUDE.md
    print_separator()
    console.print()
    _bootstrap_claude_md(answers)

    # 7. Done
    print_separator()
    console.print()
    print_success("Setup complete! Run 'track start' to begin your first session.")
    console.print()
    console.print("Quick reference:")
    console.print("  [bold]track start[/bold]      — begin tracking")
    console.print("  [bold]track plan[/bold]       — set tomorrow's goals")
    console.print("  [bold]track status[/bold]     — mid-day summary")
    console.print("  [bold]track dashboard[/bold]  — live web dashboard")
    console.print("  [bold]track end[/bold]        — stop and generate report")
    console.print("  [bold]track sleep[/bold]      — pause for the night")
    console.print("  [bold]track break 30[/bold]   — mark a 30-min break")
    console.print("  [bold]track week[/bold]       — weekly report (run Sunday)")
    console.print()

    return True


# ---------------------------------------------------------------------------
# Questionnaire
# ---------------------------------------------------------------------------

def _run_questionnaire() -> dict:
    """Ask personalization questions. Returns a dict of answers."""
    print_separator()
    console.print()
    console.print("[bold]Step 2 of 7 — About you[/bold]")
    console.print("[dim]These answers personalize the daily reports. Skip anything you don't want.[/dim]")
    console.print()

    name = ask_single_line("Your first name")
    role = ask_single_line("In one line, what do you do? (e.g. 'product manager building a fintech app')")

    print_separator()
    console.print()
    console.print("[bold]Step 3 of 7 — Your projects[/bold]")
    console.print("[dim]The tracker uses these names + keywords to detect what you're working on.[/dim]")
    console.print()
    projects = _ask_projects()

    print_separator()
    console.print()
    console.print("[bold]Step 4 of 7 — People you work with[/bold]")
    console.print("[dim]Optional. Helps the tracker recognize when you're reviewing a teammate's work.[/dim]")
    console.print()
    people = _ask_people(projects)

    print_separator()
    console.print()
    console.print("[bold]Step 5 of 7 — Distractions + work hours[/bold]")
    console.print("[dim]Topics/sites the tracker should flag aggressively when they appear during work.[/dim]")
    console.print()
    distractions_kw = ask_single_line(
        "Distraction keywords, comma-separated (e.g. 'football, twitter, news')",
    )
    distractions_dom = ask_single_line(
        "Distraction domains, comma-separated (e.g. 'youtube.com, reddit.com')",
    )

    work_start = ask_single_line("Work day start hour, 24h (default 10)", default="10")
    work_end = ask_single_line("Work day end hour, 24h (default 19)", default="19")

    tone = ask_single_line(
        "Feedback tone — 'harsh', 'encouraging', or 'neutral' (default neutral)",
        default="neutral",
    ).strip().lower()
    if tone not in ("harsh", "encouraging", "neutral"):
        tone = "neutral"

    flag_aggressively = ask_single_line(
        "What should the daily report call out aggressively? (free text, optional)",
    )
    sleep_pattern = ask_single_line(
        "Briefly describe your work rhythm — when do you focus best? (free text, optional)",
    )

    return {
        "name": name.strip(),
        "role": role.strip(),
        "projects": projects,
        "people": people,
        "distraction_keywords": _split_csv(distractions_kw),
        "distraction_domains": _split_csv(distractions_dom),
        "work_start_hour": _parse_hour(work_start, 10),
        "work_end_hour": _parse_hour(work_end, 19),
        "tone": tone,
        "flag_aggressively": flag_aggressively.strip(),
        "sleep_pattern": sleep_pattern.strip(),
    }


def _ask_projects() -> list[dict]:
    """Loop asking for projects until the user is done."""
    projects: list[dict] = []
    while True:
        idx = len(projects) + 1
        suffix = " (or Enter to finish)" if projects else ""
        name = ask_single_line(f"Project #{idx} — name{suffix}").strip()
        if not name:
            if projects:
                break
            print_warning("Add at least one project so the tracker has something to compare against.")
            continue

        description = ask_single_line(
            f"  Short description of {name}",
        ).strip()
        keywords_raw = ask_single_line(
            f"  Keywords for {name}, comma-separated (words/people that signal this project)",
        )
        keywords = _split_csv(keywords_raw)
        projects.append({
            "name": name,
            "description": description,
            "keywords": keywords,
        })
        if not ask_confirmation("Add another project?", default=False):
            break
    return projects


def _ask_people(projects: list[dict]) -> list[dict]:
    """Loop asking for team members. Skip-friendly."""
    if not ask_confirmation("Add team members or collaborators?", default=False):
        return []

    people: list[dict] = []
    project_names = [p["name"] for p in projects]
    while True:
        idx = len(people) + 1
        suffix = " (or Enter to finish)" if people else ""
        name = ask_single_line(f"Person #{idx} — name{suffix}").strip()
        if not name:
            break
        role = ask_single_line(f"  {name}'s role").strip()
        if project_names:
            project = ask_single_line(
                f"  Which project? ({', '.join(project_names)}; or Enter for none)",
            ).strip()
            if project not in project_names:
                project = ""
        else:
            project = ""
        people.append({"name": name, "role": role, "project": project})
        if not ask_confirmation("Add another person?", default=False):
            break
    return people


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_questionnaire_to_config(config_path: Path, answers: dict) -> None:
    """Merge the questionnaire answers into config.json."""
    try:
        with config_path.open() as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print_error(f"Could not read config to save answers: {exc}")
        return

    data["projects"] = answers["projects"]
    data["people"] = answers["people"]
    data["distractions"] = {
        "keywords": answers["distraction_keywords"],
        "domains": answers["distraction_domains"],
    }
    data.setdefault("schedule", {})
    data["schedule"]["work_start_hour"] = answers["work_start_hour"]
    data["schedule"]["work_end_hour"] = answers["work_end_hour"]

    try:
        with config_path.open("w") as fh:
            json.dump(data, fh, indent=2)
        print_success("Personal context saved to config.json")
    except OSError as exc:
        print_error(f"Could not write config: {exc}")


def _bootstrap_claude_md(answers: dict) -> None:
    """Write CLAUDE.md from the questionnaire answers."""
    data_dir = Path.home() / ".tracker"
    data_dir.mkdir(parents=True, exist_ok=True)
    claude_md_path = data_dir / "CLAUDE.md"

    if claude_md_path.exists():
        if not ask_confirmation("CLAUDE.md already exists. Overwrite with new answers?", default=False):
            return

    name = answers.get("name") or "the user"
    role = answers.get("role") or "(role not provided)"
    tone = answers.get("tone", "neutral")
    flag = answers.get("flag_aggressively") or "(none specified)"
    sleep_pattern = answers.get("sleep_pattern") or "(none specified)"

    project_lines = "\n".join(
        f"- **{p['name']}** — {p.get('description') or 'no description'}"
        + (f" (keywords: {', '.join(p['keywords'])})" if p.get("keywords") else "")
        for p in answers.get("projects", [])
    ) or "_No projects configured._"

    people_lines = "\n".join(
        f"- {p['name']} — {p.get('role') or 'role not given'}"
        + (f" [{p['project']}]" if p.get("project") else "")
        for p in answers.get("people", [])
    ) or "_No team configured._"

    distraction_kw = ", ".join(answers.get("distraction_keywords") or []) or "_none specified_"
    distraction_dom = ", ".join(answers.get("distraction_domains") or []) or "_none specified_"

    tone_instruction = {
        "harsh": "Be brutally honest. Call out failures specifically. No softening.",
        "encouraging": "Be encouraging but specific. Acknowledge wins. Frame failures as patterns to fix.",
        "neutral": "Be specific and direct. Neither harsh nor sugarcoated.",
    }[tone]

    content = f"""# CLAUDE.md — Personal context for {name}
<!-- Auto-updated weekly by tracker -->
<!-- Initially created: {datetime.date.today().isoformat()} -->

## About {name}

**Role:** {role}

**Work rhythm:** {sleep_pattern}

## Projects

{project_lines}

## Team / collaborators

{people_lines}

## Known distractions during work hours

- **Topics:** {distraction_kw}
- **Domains:** {distraction_dom}

## Feedback preferences

**Tone:** {tone}. {tone_instruction}

**Things to flag aggressively:** {flag}

## Behavioural patterns (auto-updated weekly)

_Will be populated after the first weekly report._

## How to work with {name} effectively

- Be direct and specific. Name the problem, not a vague observation.
- If they're consuming content from their listed distractions, redirect to stated goals.
- Push back if they're planning too many tasks for the day.
- Don't suggest things they already know — get to the actionable part.
"""

    claude_md_path.write_text(content, encoding="utf-8")
    print_success(f"CLAUDE.md written to {claude_md_path}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_activitywatch() -> None:
    """Check if AW is running and print guidance if not."""
    try:
        import httpx
        resp = httpx.get("http://localhost:5600/api/0/info", timeout=2.0)
        if resp.status_code == 200:
            data = resp.json()
            print_success(f"ActivityWatch running (v{data.get('version', '?')})")
            return
    except Exception:
        pass

    print_warning("ActivityWatch is not running.")
    console.print("[dim]Download from: https://activitywatch.net[/dim]")
    console.print("[dim]Install and launch it before running 'track start'.[/dim]")


def _split_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def _parse_hour(raw: str, default: int) -> int:
    try:
        v = int(raw.strip())
        if 0 <= v <= 24:
            return v
    except ValueError:
        pass
    return default


def _update_config_value(config_path: Path, key: str, value: str) -> None:
    """Update a single key in config.json."""
    try:
        with config_path.open() as fh:
            data = json.load(fh)
        data[key] = value
        with config_path.open("w") as fh:
            json.dump(data, fh, indent=2)
    except (OSError, json.JSONDecodeError) as exc:
        print_error(f"Could not update config: {exc}")
