from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import AppConfig


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _set_dotted(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cursor = data
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    content = yaml.safe_load(path.read_text())
    return content or {}


def _default_config_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    candidate = project_root / "config" / "defaults.yaml"
    if candidate.exists():
        return candidate
    return Path("config/defaults.yaml")


def load_config(
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    base = _load_yaml(_default_config_path())
    if config_path:
        base = _deep_merge(base, _load_yaml(config_path))

    overrides = cli_overrides or {}
    if overrides:
        nested: dict[str, Any] = {}
        for key, value in overrides.items():
            _set_dotted(nested, key, value)
        base = _deep_merge(base, nested)

    return AppConfig.model_validate(base)


def response_file_path(repo_path: Path, config: AppConfig) -> Path:
    return repo_path / config.results.response_file
