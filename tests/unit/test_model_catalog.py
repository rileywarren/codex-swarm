from __future__ import annotations

import json
from pathlib import Path

from codex_swarm.model_catalog import (
    list_available_models,
    load_codex_swarm_user_defaults,
    read_codex_default_model,
    save_codex_swarm_default_models,
)


def test_model_catalog_reads_cache_and_codex_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text('model = "gpt-hidden"\n')
    (codex_dir / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-a", "display_name": "A", "visibility": "list", "priority": 2},
                    {"slug": "gpt-b", "display_name": "B", "visibility": "list", "priority": 1},
                    {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide", "priority": 0},
                ]
            }
        )
    )

    current = read_codex_default_model()
    assert current == "gpt-hidden"

    models = list_available_models()
    slugs = [m.slug for m in models]
    assert slugs[0] == "gpt-hidden"
    assert "gpt-b" in slugs
    assert "gpt-a" in slugs


def test_model_catalog_persists_user_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    path = save_codex_swarm_default_models("gpt-1", "gpt-2")
    assert path.exists()

    loaded = load_codex_swarm_user_defaults()
    assert loaded["swarm"]["supervisor_model"] == "gpt-1"
    assert loaded["swarm"]["worker_model"] == "gpt-2"
