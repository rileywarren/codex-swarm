from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    slug: str
    display_name: str
    priority: int


def codex_home() -> Path:
    return Path.home() / ".codex"


def codex_swarm_user_config_path() -> Path:
    return Path.home() / ".codex-swarm" / "config.yaml"


def read_codex_default_model() -> str | None:
    config_path = codex_home() / "config.toml"
    if not config_path.exists():
        return None

    try:
        data = tomllib.loads(config_path.read_text())
    except Exception:  # noqa: BLE001
        return None

    model = data.get("model")
    return model if isinstance(model, str) and model.strip() else None


def list_available_models() -> list[ModelDescriptor]:
    cache_path = codex_home() / "models_cache.json"
    models: list[ModelDescriptor] = []

    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text())
            for row in payload.get("models", []):
                if not isinstance(row, dict):
                    continue
                slug = row.get("slug")
                display = row.get("display_name") or slug
                visibility = row.get("visibility", "list")
                if not isinstance(slug, str) or not slug:
                    continue
                if visibility == "hide":
                    continue
                priority = int(row.get("priority", 999))
                models.append(ModelDescriptor(slug=slug, display_name=str(display), priority=priority))
        except Exception:  # noqa: BLE001
            models = []

    if not models:
        models = [
            ModelDescriptor(slug="gpt-5.3-codex", display_name="gpt-5.3-codex", priority=0),
            ModelDescriptor(slug="gpt-5.2-codex", display_name="gpt-5.2-codex", priority=1),
            ModelDescriptor(slug="gpt-5.1-codex-mini", display_name="gpt-5.1-codex-mini", priority=2),
        ]

    models = sorted(models, key=lambda item: (item.priority, item.slug))

    current = read_codex_default_model()
    if current and all(item.slug != current for item in models):
        models.insert(0, ModelDescriptor(slug=current, display_name=current, priority=-1))

    return models


def load_codex_swarm_user_defaults() -> dict[str, Any]:
    path = codex_swarm_user_config_path()
    if not path.exists():
        return {}

    try:
        loaded = yaml.safe_load(path.read_text())
    except Exception:  # noqa: BLE001
        return {}

    return loaded if isinstance(loaded, dict) else {}


def save_codex_swarm_default_models(supervisor_model: str, worker_model: str) -> Path:
    path = codex_swarm_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = load_codex_swarm_user_defaults()
    swarm = data.get("swarm")
    if not isinstance(swarm, dict):
        swarm = {}
        data["swarm"] = swarm

    swarm["supervisor_model"] = supervisor_model
    swarm["worker_model"] = worker_model

    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path
