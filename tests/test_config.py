"""tests/test_config.py"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tracker.config import (
    Config,
    load_config,
    write_default_config,
)


class TestLoadConfig:
    def test_load_from_minimal_json(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "anthropic_api_key": "sk-test-123",
            "data_dir": str(tmp_path),
        }))
        cfg = load_config(cfg_file)
        assert cfg.api.anthropic_api_key == "sk-test-123"
        assert cfg.paths.data_dir == tmp_path

    def test_load_defaults_when_no_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"
        # No explicit path → uses default path which also won't exist in test
        # We test the explicit-path-missing case
        with pytest.raises(FileNotFoundError):
            load_config(missing)

    def test_api_key_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-xyz")
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"data_dir": str(tmp_path)}))
        cfg = load_config(cfg_file)
        assert cfg.api.anthropic_api_key == "env-key-xyz"

    def test_config_file_key_takes_precedence_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "anthropic_api_key": "file-key",
            "data_dir": str(tmp_path),
        }))
        cfg = load_config(cfg_file)
        assert cfg.api.anthropic_api_key == "file-key"

    def test_no_hardcoded_projects_default(self, tmp_path: Path) -> None:
        """Defaults must be empty so the tool isn't shipped pre-personalized to one user."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"data_dir": str(tmp_path)}))
        cfg = load_config(cfg_file)
        assert cfg.projects == []

    def test_no_hardcoded_people_default(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"data_dir": str(tmp_path)}))
        cfg = load_config(cfg_file)
        assert cfg.people == []

    def test_projects_populated_from_config(self, tmp_path: Path) -> None:
        """Projects come from the user's config.json (written by `track setup`)."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "data_dir": str(tmp_path),
            "projects": [
                {"name": "MyApp", "description": "test", "keywords": ["foo"]},
            ],
        }))
        cfg = load_config(cfg_file)
        assert [p.name for p in cfg.projects] == ["MyApp"]

    def test_paths_derived_from_data_dir(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"data_dir": str(tmp_path)}))
        cfg = load_config(cfg_file)
        assert cfg.paths.db_path == tmp_path / "tracker.db"
        assert cfg.paths.screenshots_dir == tmp_path / "screenshots"
        assert cfg.paths.reports_dir == tmp_path / "reports"

    def test_daemon_defaults(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"data_dir": str(tmp_path)}))
        cfg = load_config(cfg_file)
        assert cfg.daemon.poll_interval_seconds == 30
        assert cfg.daemon.screenshot_interval_seconds == 90
        assert cfg.daemon.text_field_sample_chars == 300
        assert cfg.daemon.websocket_port == 27182

    def test_schedule_defaults(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"data_dir": str(tmp_path)}))
        cfg = load_config(cfg_file)
        assert cfg.schedule.work_start_hour == 10
        assert cfg.schedule.work_end_hour == 19

    def test_custom_daemon_config(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "data_dir": str(tmp_path),
            "daemon": {"poll_interval_seconds": 60},
        }))
        cfg = load_config(cfg_file)
        assert cfg.daemon.poll_interval_seconds == 60


class TestWriteDefaultConfig:
    def test_writes_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        result = write_default_config(path)
        assert result == path
        assert path.exists()

    def test_written_file_is_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        write_default_config(path)
        data = json.loads(path.read_text())
        assert "anthropic_api_key" in data
        assert "projects" in data
        assert "daemon" in data

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "c" / "config.json"
        write_default_config(deep_path)
        assert deep_path.exists()

    def test_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        write_default_config(path)
        write_default_config(path)
        assert path.exists()
