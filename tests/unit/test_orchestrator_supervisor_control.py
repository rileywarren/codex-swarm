from __future__ import annotations

import pytest

from codex_swarm.config import load_config
from codex_swarm.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_orchestrator_kill_supervisor_delegates(git_repo, monkeypatch) -> None:
    orch = Orchestrator(git_repo, load_config(cli_overrides={"ipc.method": "file_watch"}))

    calls = {"count": 0}

    async def fake_kill() -> bool:
        calls["count"] += 1
        return True

    monkeypatch.setattr(orch.supervisor_manager, "kill", fake_kill)

    killed = await orch.kill_supervisor()
    assert killed is True
    assert calls["count"] == 1
