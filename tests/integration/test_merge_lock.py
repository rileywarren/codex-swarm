from __future__ import annotations

import asyncio
import time

import pytest

from codex_swarm.config import load_config
from codex_swarm.models import MergeOutcome
from codex_swarm.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_merge_results_are_serialized(git_repo, monkeypatch) -> None:
    config = load_config(cli_overrides={"ipc.method": "file_watch"})
    orch = Orchestrator(git_repo, config)
    await orch.start()

    events: list[tuple[str, float]] = []

    def fake_merge_branch(worker_id: str, branch: str, task_summary: str, resolve_conflicts: str = "abort"):
        events.append(("start", time.perf_counter()))
        time.sleep(0.05)
        events.append(("end", time.perf_counter()))
        return MergeOutcome(worker_id=worker_id, branch=branch, merged=True, conflict=False, message="ok")

    monkeypatch.setattr(orch.merge_manager, "merge_branch", fake_merge_branch)

    await asyncio.gather(
        orch._merge_worker_branch("w1", "branch-w1", "task-1"),
        orch._merge_worker_branch("w2", "branch-w2", "task-2"),
    )

    assert len(events) == 4
    assert events[0][0] == "start"
    assert events[1][0] == "end"
    assert events[2][0] == "start"
    assert events[3][0] == "end"
    assert events[1][1] <= events[2][1]

    await orch.stop()
