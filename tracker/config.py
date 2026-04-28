"""
tracker/config.py

Single source of truth for all configuration.
All other modules receive a Config object — they never read files or env vars directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Sub-config dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectConfig:
    name: str
    description: str
    keywords: list[str]


@dataclass(frozen=True)
class PersonConfig:
    name: str
    role: str
    project: str


@dataclass(frozen=True)
class DistractionConfig:
    keywords: list[str]
    domains: list[str]


@dataclass(frozen=True)
class ScheduleConfig:
    work_start_hour: int        # 10
    work_end_hour: int          # 19
    late_session_start_hour: int  # 22
    late_session_end_hour: int    # 25  (1am next day = 25)


@dataclass(frozen=True)
class DaemonConfig:
    poll_interval_seconds: int      # 30
    screenshot_interval_seconds: int  # 90
    text_field_sample_chars: int    # 300
    doc_poll_interval_seconds: int  # 60
    websocket_port: int             # 27182


@dataclass(frozen=True)
class PathsConfig:
    data_dir: Path
    db_path: Path
    screenshots_dir: Path
    reports_dir: Path
    goals_dir: Path
    claude_md_path: Path
    prompts_dir: Path


@dataclass(frozen=True)
class ApiConfig:
    anthropic_api_key: str
    anthropic_model: str            # claude-sonnet-4-5-20250929 — main analysis
    screenshot_model: str           # claude-3-5-haiku-20241022 — cheap vision model
    aw_base_url: str                # http://localhost:5600


# ---------------------------------------------------------------------------
# Root Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    projects: list[ProjectConfig]
    people: list[PersonConfig]
    distractions: DistractionConfig
    schedule: ScheduleConfig
    daemon: DaemonConfig
    paths: PathsConfig
    api: ApiConfig


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_DATA_DIR = Path.home() / ".tracker"
_DEFAULT_CONFIG_PATH = Path.home() / ".tracker" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from a JSON file.
    Falls back to defaults for any missing fields.
    Raises FileNotFoundError if config_path is explicitly provided but missing.
    Raises ValueError if required fields (API key) are absent.
    """
    path = config_path or _DEFAULT_CONFIG_PATH

    if path.exists():
        with path.open() as fh:
            raw = json.load(fh)
    elif config_path is not None:
        raise FileNotFoundError(f"Config file not found: {path}")
    else:
        raw = {}

    # API key: config file → env var → error
    api_key = (
        raw.get("anthropic_api_key")
        or os.environ.get("ANTHROPIC_API_KEY")
        or ""
    )

    data_dir = Path(raw.get("data_dir", str(_DEFAULT_DATA_DIR)))

    return Config(
        projects=_parse_projects(raw.get("projects", _default_projects())),
        people=_parse_people(raw.get("people", _default_people())),
        distractions=_parse_distractions(raw.get("distractions", {})),
        schedule=_parse_schedule(raw.get("schedule", {})),
        daemon=_parse_daemon(raw.get("daemon", {})),
        paths=_build_paths(data_dir, raw),
        api=ApiConfig(
            anthropic_api_key=api_key,
            anthropic_model=raw.get("anthropic_model", "claude-sonnet-4-5-20250929"),
            screenshot_model=raw.get("screenshot_model", "claude-3-5-haiku-20241022"),
            aw_base_url=raw.get("aw_base_url", "http://localhost:5600"),
        ),
    )


def write_default_config(config_path: Path | None = None) -> Path:
    """Write a default config.json to disk. Safe to call on first run."""
    path = config_path or _DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    default: dict = {
        "anthropic_api_key": "",
        "anthropic_model": "claude-sonnet-4-5-20250929",
        "screenshot_model": "claude-3-5-haiku-20241022",
        "aw_base_url": "http://localhost:5600",
        "data_dir": str(path.parent),
        "projects": _default_projects(),
        "people": _default_people(),
        "distractions": {
            "keywords": [],
            "domains": [],
        },
        "schedule": {
            "work_start_hour": 10,
            "work_end_hour": 19,
            "late_session_start_hour": 22,
            "late_session_end_hour": 25,
        },
        "daemon": {
            "poll_interval_seconds": 30,
            "screenshot_interval_seconds": 90,
            "text_field_sample_chars": 300,
            "doc_poll_interval_seconds": 60,
            "websocket_port": 27182,
        },
    }

    with path.open("w") as fh:
        json.dump(default, fh, indent=2)

    return path


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _parse_projects(raw: list[dict]) -> list[ProjectConfig]:
    return [
        ProjectConfig(
            name=p["name"],
            description=p.get("description", ""),
            keywords=p.get("keywords", []),
        )
        for p in raw
    ]


def _parse_people(raw: list[dict]) -> list[PersonConfig]:
    return [
        PersonConfig(
            name=p["name"],
            role=p.get("role", ""),
            project=p.get("project", ""),
        )
        for p in raw
    ]


def _parse_distractions(raw: dict) -> DistractionConfig:
    return DistractionConfig(
        keywords=raw.get("keywords", []),
        domains=raw.get("domains", []),
    )


def _parse_schedule(raw: dict) -> ScheduleConfig:
    return ScheduleConfig(
        work_start_hour=raw.get("work_start_hour", 10),
        work_end_hour=raw.get("work_end_hour", 19),
        late_session_start_hour=raw.get("late_session_start_hour", 22),
        late_session_end_hour=raw.get("late_session_end_hour", 25),
    )


def _parse_daemon(raw: dict) -> DaemonConfig:
    return DaemonConfig(
        poll_interval_seconds=raw.get("poll_interval_seconds", 30),
        screenshot_interval_seconds=raw.get("screenshot_interval_seconds", 90),
        text_field_sample_chars=raw.get("text_field_sample_chars", 300),
        doc_poll_interval_seconds=raw.get("doc_poll_interval_seconds", 60),
        websocket_port=raw.get("websocket_port", 27182),
    )


def _build_paths(data_dir: Path, raw: dict) -> PathsConfig:
    return PathsConfig(
        data_dir=data_dir,
        db_path=data_dir / "tracker.db",
        screenshots_dir=data_dir / "screenshots",
        reports_dir=data_dir / "reports",
        goals_dir=data_dir / "goals",
        claude_md_path=data_dir / "CLAUDE.md",
        prompts_dir=Path(__file__).parent / "analysis" / "prompts",
    )


def _default_projects() -> list[dict]:
    """Empty by default — populated by the first-run questionnaire."""
    return []


def _default_people() -> list[dict]:
    """Empty by default — populated by the first-run questionnaire."""
    return []
