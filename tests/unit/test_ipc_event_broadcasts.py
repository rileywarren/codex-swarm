from __future__ import annotations

import pytest

from codex_swarm.config import load_config
from codex_swarm.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_orchestrator_broadcasts_unsolicited_events(git_repo, monkeypatch) -> None:
    config = load_config(
        cli_overrides={
            "ipc.method": "unix_socket",
            "ipc.socket_path": "/tmp/codex-swarm-test-broadcast.sock",
        }
    )
    orch = Orchestrator(git_repo, config)

    seen = []

    async def fake_broadcast(msg):
        seen.append(msg)

    monkeypatch.setattr(orch.ipc_server, "broadcast", fake_broadcast)

    await orch._emit("worker.status", {"worker_id": "w1", "status": "running"})
    await orch._emit("log", {"line": "ignore"})

    assert len(seen) == 1
    assert seen[0].type == "event"
    assert seen[0].payload["event_type"] == "worker.status"
