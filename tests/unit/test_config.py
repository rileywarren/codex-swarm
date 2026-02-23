from __future__ import annotations

from pathlib import Path

from codex_swarm.config import load_config, response_file_path


def test_config_precedence(tmp_path: Path) -> None:
    user_cfg = tmp_path / "cfg.yaml"
    user_cfg.write_text(
        """
swarm:
  max_workers: 2
results:
  response_file: ".custom-response.md"
"""
    )

    config = load_config(
        config_path=user_cfg,
        cli_overrides={"swarm.max_workers": 7, "budget.max_total_cost": 9.5},
    )

    assert config.swarm.max_workers == 7
    assert config.budget.max_total_cost == 9.5
    assert config.results.response_file == ".custom-response.md"


def test_response_file_path(tmp_path: Path) -> None:
    config = load_config()
    path = response_file_path(tmp_path, config)
    assert path == tmp_path / ".codex-swarm-response.md"


def test_user_defaults_are_loaded(monkeypatch) -> None:
    import codex_swarm.config as config_module

    monkeypatch.setattr(
        config_module,
        "load_codex_swarm_user_defaults",
        lambda: {"swarm": {"supervisor_model": "gpt-default", "worker_model": "gpt-default"}},
    )

    config = config_module.load_config()
    assert config.swarm.supervisor_model == "gpt-default"
    assert config.swarm.worker_model == "gpt-default"
