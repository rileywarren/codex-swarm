from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

from click.testing import CliRunner


def _load_cli() -> ModuleType:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual not installed")
    return importlib.import_module("codex_swarm.cli")


def test_gui_command_disabled_via_config(tmp_path: Path) -> None:
    config = tmp_path / "gui-disabled.yaml"
    config.write_text("gui:\n  enabled: false\n")

    runner = CliRunner()
    cli = _load_cli()
    result = runner.invoke(cli.main, ["--config", str(config), "gui"])

    assert result.exit_code != 0
    assert "GUI is disabled by configuration" in result.output


def test_gui_command_invocation_without_dependencies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    marker: dict[str, bool] = {"called": False}

    def fake_run_gui(runtime: object) -> None:
        marker["called"] = True

    monkeypatch.setattr("codex_swarm.gui.app.run_gui", fake_run_gui)

    config = tmp_path / "gui-enabled.yaml"
    config.write_text("gui:\n  enabled: true\n")

    runner = CliRunner()
    cli = _load_cli()
    result = runner.invoke(cli.main, ["--config", str(config), "gui"])

    assert result.exit_code == 0
    assert marker["called"] is True
