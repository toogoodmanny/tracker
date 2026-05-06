"""
tracker/cli/main.py

All CLI commands. Entry point is `cli` (registered as `track` in pyproject.toml).
Command handlers are thin — they delegate to session_manager, db, daemon.
No business logic lives here.
"""

from __future__ import annotations

import datetime
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
from pathlib import Path

import click

from tracker.aw_client import ActivityWatchClient
from tracker.cli.output import (
    print_error,
    print_header,
    print_info,
    print_key_value,
    print_success,
    print_warning,
)
from tracker.cli.prompts import ask_confirmation, ask_multiline, ask_single_line
from tracker.cli.session_manager import SessionManager
from tracker.config import Config, load_config, write_default_config
from tracker.core.models import (
    Correction,
    Goal,
    Note,
    Observation,
    ObservationType,
    Session,
    SessionType,
)
from tracker.daemon import Daemon
from tracker.db.connection import open_database
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)


def _load_config_or_init() -> Config:
    """Load config, running first-time setup on first run."""
    default_path = Path.home() / ".tracker" / "config.json"
    if not default_path.exists():
        from tracker.cli.first_run import run_first_time_setup
        run_first_time_setup()
    return load_config()


def _open_db(config: Config) -> Database:
    conn = open_database(config.paths.db_path)
    return Database(conn)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging")
def cli(debug: bool) -> None:
    """Personal time tracker. Run 'track --help' for commands."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Silence noisy library and internal loggers — not useful to end users
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("tracker.db.connection").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# track setup
# ---------------------------------------------------------------------------

@cli.command()
def setup() -> None:
    """First-time setup wizard. Run once after installation."""
    from tracker.cli.first_run import run_first_time_setup
    run_first_time_setup()


# ---------------------------------------------------------------------------
# track start
# ---------------------------------------------------------------------------

@cli.command()
def start() -> None:
    """Start tracking. Checks for goals, launches daemon."""
    config = _load_config_or_init()
    db = _open_db(config)
    sm = SessionManager(config.paths.data_dir)

    if sm.has_active_session():
        state = sm.load_active_session()
        if state is not None:
            print_warning(
                f"A session is already active (session #{state.session_id}, "
                f"started on {state.day_date})"
            )
            if not ask_confirmation("Start a new session anyway?", default=False):
                db.close()
                return

    today = datetime.date.today()

    # Check for goals
    existing_goals = db.goals.get_for_day(today)
    if existing_goals is None:
        print_warning("No goals set for today.")
        if ask_confirmation("Set goals now?", default=True):
            _run_plan_flow(db, today)
    else:
        print_info(f"Goals loaded for today: {existing_goals.raw_input[:80]}...")

    # Check AW is running — auto-launch it if not
    aw = ActivityWatchClient(config.api.aw_base_url)
    aw_status = aw.check_connectivity()
    if not aw_status.reachable:
        print_info("ActivityWatch not running — launching it now…")
        import subprocess, time as _time
        subprocess.Popen(["open", "-a", "ActivityWatch"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Give AW up to 15 seconds to start its HTTP server
        for _ in range(15):
            _time.sleep(1)
            aw_status = aw.check_connectivity()
            if aw_status.reachable:
                break
        if not aw_status.reachable:
            print_warning(
                "ActivityWatch didn't start in time. "
                "Open it manually from Applications and re-run 'track start'."
            )
            db.close()
            return

    print_info(f"ActivityWatch connected (v{aw_status.version})")

    # Create session
    session = Session(
        day_date=today,
        session_type=SessionType.PRIMARY,
        start_time=datetime.datetime.now(),
    )
    session_id = db.sessions.insert(session)
    assert session_id is not None

    # Launch daemon in background process
    daemon_pid = _launch_daemon_process(config, session_id)

    sm.save_active_session(
        session_id=session_id,
        day_date=today,
        daemon_pid=daemon_pid,
        session_type=SessionType.PRIMARY,
    )

    print_success(
        f"Tracking started — session #{session_id} "
        f"(daemon PID {daemon_pid or 'unknown'})"
    )
    db.close()


# ---------------------------------------------------------------------------
# track end
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--no-analysis",
    is_flag=True,
    default=False,
    help="Stop tracking without generating the daily report",
)
def end(no_analysis: bool) -> None:
    """Stop tracking and generate the daily report."""
    config = _load_config_or_init()
    db = _open_db(config)
    sm = SessionManager(config.paths.data_dir)

    state = sm.load_active_session()
    if state is None:
        print_error("No active session. Run 'track start' first.")
        db.close()
        return

    # Stop daemon
    if state.daemon_pid is not None:
        _stop_daemon_process(state.daemon_pid)

    # Close session in DB
    now = datetime.datetime.now()
    db.sessions.close_session(session_id=state.session_id, end_time=now)
    sm.clear_active_session()

    day_date = datetime.date.fromisoformat(state.day_date)
    snapshots = db.snapshots.get_by_session(state.session_id)

    print_success(
        f"Session #{state.session_id} closed — "
        f"{len(snapshots)} snapshots recorded"
    )

    if not no_analysis:
        if not config.api.anthropic_api_key:
            print_warning(
                "No Anthropic API key configured. "
                "Add it to ~/.tracker/config.json to generate reports."
            )
        else:
            print_info("Generating daily report...")
            _run_daily_analysis(config, db, state.session_id, day_date)

    db.close()


# ---------------------------------------------------------------------------
# track sleep
# ---------------------------------------------------------------------------

@cli.command()
def sleep() -> None:
    """Pause tracking (longer break or end of night). Resume tomorrow with 'track start'."""
    config = _load_config_or_init()
    sm = SessionManager(config.paths.data_dir)

    state = sm.load_active_session()
    if state is None:
        print_error("No active session.")
        return

    if state.daemon_pid is not None:
        _stop_daemon_process(state.daemon_pid)
        print_success(f"Daemon stopped (PID {state.daemon_pid})")

    # Don't close the session — leave it open so late-night work can append
    # Update state to reflect daemon is paused
    sm.save_active_session(
        session_id=state.session_id,
        day_date=datetime.date.fromisoformat(state.day_date),
        daemon_pid=None,
        session_type=SessionType(state.session_type),
    )

    print_success("Tracking paused. Run 'track start' to resume or start fresh tomorrow.")


# ---------------------------------------------------------------------------
# track break
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("minutes", type=int)
def break_(minutes: int) -> None:
    """Mark an intentional break of N minutes. Excluded from distraction score."""
    if minutes <= 0:
        print_error("Minutes must be a positive integer.")
        return

    config = _load_config_or_init()
    db = _open_db(config)
    sm = SessionManager(config.paths.data_dir)

    state = sm.load_active_session()
    if state is None:
        print_error("No active session.")
        db.close()
        return

    note = Note(
        session_id=state.session_id,
        note_text=f"INTENTIONAL_BREAK:{minutes}",
        day_date=datetime.date.fromisoformat(state.day_date),
    )
    db.notes.insert(note)
    print_success(f"Marked {minutes}-minute intentional break.")
    db.close()


# break_ is named with _ to avoid clash with Python keyword
cli.add_command(break_, name="break")


# ---------------------------------------------------------------------------
# track plan
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--date",
    default=None,
    help="Plan for a specific date (YYYY-MM-DD). Defaults to tomorrow.",
)
def plan(date: str | None) -> None:
    """Set goals for tomorrow (or today if no session is running)."""
    config = _load_config_or_init()
    db = _open_db(config)

    if date:
        try:
            target_date = datetime.date.fromisoformat(date)
        except ValueError:
            print_error(f"Invalid date format: {date!r}. Use YYYY-MM-DD.")
            db.close()
            return
    else:
        sm = SessionManager(config.paths.data_dir)
        if sm.has_active_session():
            target_date = datetime.date.today() + datetime.timedelta(days=1)
        else:
            target_date = datetime.date.today()

    print_header(f"Setting goals for {target_date.strftime('%A, %d %B %Y')}")
    print_info("Be specific. Include times if you have them.")
    print_info("Example: '10:30 - 1hr on the project intro screen. Then 2hrs writing.'")
    print_info("")

    _run_plan_flow(db, target_date)
    db.close()


# ---------------------------------------------------------------------------
# track correct
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("note_text")
def correct(note_text: str) -> None:
    """Correct a misclassification. E.g.: track correct \"the figma at 2pm was a design review, not drift\""""
    config = _load_config_or_init()
    db = _open_db(config)
    sm = SessionManager(config.paths.data_dir)

    state = sm.load_active_session()
    today = datetime.date.today()
    if state:
        day_date = datetime.date.fromisoformat(state.day_date)
    else:
        day_date = today

    correction = Correction(
        day_date=day_date,
        correction_note=note_text,
        corrected_classification="user_correction",
    )
    db.corrections.insert(correction)

    obs = Observation(
        day_date=day_date,
        observation_type=ObservationType.MISCLASSIFICATION,
        detail=f"User correction: {note_text}",
    )
    db.observations.insert(obs)

    print_success(f"Correction recorded: {note_text!r}")
    db.close()


# ---------------------------------------------------------------------------
# track note
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("note_text")
def note(note_text: str) -> None:
    """Add a freeform note to the current session."""
    config = _load_config_or_init()
    db = _open_db(config)
    sm = SessionManager(config.paths.data_dir)

    state = sm.load_active_session()
    if state is None:
        print_error("No active session. Start one with 'track start'.")
        db.close()
        return

    n = Note(
        session_id=state.session_id,
        note_text=note_text,
        day_date=datetime.date.fromisoformat(state.day_date),
    )
    db.notes.insert(n)
    print_success(f"Note saved: {note_text!r}")
    db.close()


# ---------------------------------------------------------------------------
# track status
# ---------------------------------------------------------------------------

@cli.command()
def status() -> None:
    """Show current session status."""
    config = _load_config_or_init()
    db = _open_db(config)
    sm = SessionManager(config.paths.data_dir)

    state = sm.load_active_session()
    if state is None:
        print_warning("No active session.")
        db.close()
        return

    print_header("Current session")
    from tracker.cli.status import render_status

    day_date = datetime.date.fromisoformat(state.day_date)
    snapshots = db.snapshots.get_by_session(state.session_id)
    sessions = db.sessions.get_by_day(day_date)
    active_session = next((s for s in sessions if s.id == state.session_id), None)
    goals = db.goals.get_for_day(day_date)

    if active_session is None:
        print_error("Session record not found in DB.")
        db.close()
        return

    render_status(
        session=active_session,
        snapshots=snapshots,
        goals_text=goals.raw_input if goals else None,
        day_date=day_date,
    )

    if not goals:
        print_warning("No goals set for today. Run 'track plan'.")
    if not state.daemon_pid:
        print_warning("Daemon is paused. Run 'track start' to resume.")

    db.close()


# ---------------------------------------------------------------------------
# track week
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--port", default=27183, type=int, help="Localhost port to bind")
@click.option("--no-open", is_flag=True, default=False, help="Don't auto-open the browser")
def dashboard(port: int, no_open: bool) -> None:
    """Launch the live web dashboard at http://127.0.0.1:<port>."""
    config = _load_config_or_init()
    from tracker.dashboard.server import serve

    url = f"http://127.0.0.1:{port}"
    print_success(f"Dashboard starting at {url}")
    if not no_open:
        try:
            subprocess.run(["open", url], check=False, timeout=3)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    print_info("Ctrl+C to stop")
    try:
        serve(config, port=port)
    except OSError as exc:
        print_error(f"Could not bind port {port}: {exc}")


@cli.command()
def week() -> None:
    """Generate the weekly report and update CLAUDE.md."""
    config = _load_config_or_init()
    db = _open_db(config)

    if not config.api.anthropic_api_key:
        print_error("No Anthropic API key configured. Add it to ~/.tracker/config.json.")
        db.close()
        return

    print_info("Generating weekly report... (this may take 20-30 seconds)")
    _run_weekly_analysis(config, db)
    db.close()


# ---------------------------------------------------------------------------
# Internal helpers (not commands)
# ---------------------------------------------------------------------------

def _run_plan_flow(db: Database, target_date: datetime.date, config: Config | None = None) -> None:
    """Shared goal-input flow used by both `plan` and `start`."""
    raw_input = ask_multiline(f"What are your goals for {target_date}?")
    goal = Goal(day_date=target_date, raw_input=raw_input)

    # Try to parse goals with LLM if API key available
    if config and config.api.anthropic_api_key:
        from tracker.analysis.daily import GoalParser
        parser = GoalParser(config)
        parsed = parser.parse(raw_input)
        if parsed:
            import json as _json
            from dataclasses import asdict
            goal.parsed_json = _json.dumps([
                {
                    "description": g.description,
                    "project": g.project,
                    "estimated_minutes": g.estimated_minutes,
                    "target_start_time": g.target_start_time.isoformat() if g.target_start_time else None,
                }
                for g in parsed
            ])
            print_info(f"Parsed {len(parsed)} goals.")

    db.goals.upsert(goal)
    print_success(f"Goals saved for {target_date}.")


def _launch_daemon_process(config: Config, session_id: int) -> int | None:
    """
    Launch the daemon as a background subprocess.
    Returns the PID, or None if launch failed.
    """
    python = sys.executable
    cmd = [python, "-m", "tracker._daemon_runner", str(session_id)]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).parent.parent.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc.pid
    except OSError as exc:
        logger.error("Failed to launch daemon process: %s", exc)
        return None


def _stop_daemon_process(pid: int) -> None:
    """
    Send SIGTERM to the daemon process and wait briefly.
    Raises nothing — stopping an already-dead daemon is fine.
    """
    try:
        os.kill(pid, signal.SIGTERM)
        logger.debug("Sent SIGTERM to daemon PID %d", pid)
    except ProcessLookupError:
        logger.debug("Daemon PID %d already gone", pid)
    except PermissionError as exc:
        logger.warning("Cannot stop daemon PID %d: %s", pid, exc)


def _collect_post_report_corrections(db: Database, day_date: datetime.date) -> None:
    """
    After the report opens, give the user a chance to note anything the
    tracker got wrong. Each non-empty line becomes a Correction record,
    which feeds into the weekly analysis and future CLAUDE.md updates.
    """
    print()
    print_info("Anything the tracker got wrong? (one correction per line, blank line to finish)")
    while True:
        try:
            line = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        correction = Correction(
            day_date=day_date,
            correction_note=line,
            corrected_classification="post_report",
        )
        db.corrections.insert(correction)
        print_info("  Noted ✓")
    print()


def _run_daily_analysis(
    config: Config,
    db: Database,
    session_id: int,
    day_date: datetime.date,
) -> None:
    """Run the LLM daily analysis and open the HTML report."""
    from tracker.analysis.daily import DailyAnalyser
    import subprocess as _sp
    import sys as _sys

    try:
        analyser = DailyAnalyser(config=config, db=db)
        report_path = analyser.run(session_id=session_id, day_date=day_date)
        db.sessions.close_session(session_id, datetime.datetime.now(), report_path)
        print_success(f"Report saved: {report_path}")
        # Open in default browser
        try:
            _sp.run(["open", report_path], check=True, timeout=5)
        except (FileNotFoundError, _sp.CalledProcessError, _sp.TimeoutExpired):
            print_info(f"Open manually: {report_path}")

        # Post-report feedback — let the user correct misclassifications inline
        _collect_post_report_corrections(db, day_date)
    except ValueError as exc:
        print_error(f"Analysis failed: {exc}")
        print_info("Your session data is safe in ~/.tracker/tracker.db")
        print_info("Re-run: track end --no-analysis, or try again when the issue is fixed")
    except RuntimeError as exc:
        print_error(f"LLM call failed: {exc}")
        raw_dir = config.paths.data_dir / "raw_responses"
        if raw_dir.exists():
            raws = sorted(raw_dir.glob(f"daily-{day_date}*.txt"))
            if raws:
                print_info(f"Partial response saved: {raws[-1]}")
        print_info("Your session data is safe. Re-run 'track end' to retry.")
    except Exception as exc:  # noqa: BLE001
        print_error(f"Unexpected error during analysis: {exc}")
        print_info("Your session data is safe. Re-run 'track end' to retry.")


def _run_weekly_analysis(config: Config, db: Database) -> None:
    """Run the weekly LLM analysis."""
    from tracker.analysis.weekly import WeeklyAnalyser
    import subprocess as _sp

    def ask_questions(questions: list[str]) -> dict[str, str]:
        answers = {}
        print_header("Weekly review questions")
        for i, q in enumerate(questions, 1):
            print_info(f"Q{i}: {q}")
            answer = ask_single_line("Your answer (or press Enter to skip)")
            answers[f"q{i}"] = answer
        return answers

    try:
        analyser = WeeklyAnalyser(config=config, db=db)
        report_path = analyser.run(ask_questions_fn=ask_questions)
        print_success(f"Weekly report saved: {report_path}")
        print_success(f"CLAUDE.md updated: {config.paths.claude_md_path}")
        try:
            _sp.run(["open", report_path], check=True, timeout=5)
        except (FileNotFoundError, _sp.CalledProcessError, _sp.TimeoutExpired):
            print_info(f"Open manually: {report_path}")
    except ValueError as exc:
        print_error(f"Weekly analysis failed: {exc}")
    except RuntimeError as exc:
        print_error(f"LLM call failed: {exc}")
