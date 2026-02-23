from __future__ import annotations

from pathlib import Path

from codex_swarm.config import load_config


def test_gui_config_defaults() -> None:
    config = load_config()
    assert config.gui.enabled is True
    assert config.gui.history_db_path == "~/.codex-swarm/history.db"
    assert config.gui.history_max_runs == 200
    assert config.gui.max_concurrent_sessions == 6


def test_gui_config_overrides() -> None:
    config = load_config(
        cli_overrides={
            "gui.enabled": False,
            "gui.history_db_path": str(Path("/tmp/custom-history.db")),
            "gui.history_max_runs": 50,
            "gui.max_concurrent_sessions": 3,
        }
    )
    assert config.gui.enabled is False
    assert config.gui.history_db_path == "/tmp/custom-history.db"
    assert config.gui.history_max_runs == 50
    assert config.gui.max_concurrent_sessions == 3
