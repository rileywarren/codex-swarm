from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from codex_swarm.config import load_config
from codex_swarm.models import Priority, SpawnAgentPayload, WorkerExecutionResult, WorkerResult, WorkerStatus
from codex_swarm.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_fan_out_runs_and_merges(git_repo, monkeypatch) -> None:
    config = load_config(cli_overrides={"ipc.method": "file_watch"})
    orch = Orchestrator(git_repo, config)
    await orch.start()

    merge_calls = {"count": 0}
    merge_order: list[str] = []

    async def fake_run_task(
        payload: SpawnAgentPayload,
        extra_context: str = "",
        worker_id: str | None = None,
        lifecycle_callback=None,
    ):
        wid = worker_id or payload.task.lower()
        delays = {"a": 0.03, "b": 0.01, "c": 0.02}
        if lifecycle_callback:
            await lifecycle_callback(wid, WorkerStatus.QUEUED, payload)
            await lifecycle_callback(wid, WorkerStatus.RUNNING, payload)
        await asyncio.sleep(delays[wid])
        now = datetime.now(timezone.utc)
        result = WorkerExecutionResult(
            worker_id=wid,
            branch=f"codex-swarm/worker-{wid}",
            worktree_path=f"/tmp/{wid}",
            task=payload,
            status=WorkerStatus.COMPLETED,
            result=WorkerResult(summary=f"done {payload.task}"),
            started_at=now,
            ended_at=now,
        )
        if lifecycle_callback:
            await lifecycle_callback(wid, WorkerStatus.COMPLETED, payload)
        return result

    def fake_merge_branch(worker_id: str, branch: str, task_summary: str, resolve_conflicts: str = "abort"):
        merge_calls["count"] += 1
        merge_order.append(worker_id)
        from codex_swarm.models import MergeOutcome

        return MergeOutcome(worker_id=worker_id, branch=branch, merged=True, conflict=False, message="ok")

    monkeypatch.setattr(orch.worker_manager, "run_task", fake_run_task)
    monkeypatch.setattr(orch.worker_manager, "release_worktree", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch.merge_manager, "merge_branch", fake_merge_branch)

    tasks = [
        SpawnAgentPayload(task="A", scope=[], context="", priority=Priority.NORMAL),
        SpawnAgentPayload(task="B", scope=[], context="", priority=Priority.NORMAL),
        SpawnAgentPayload(task="C", scope=[], context="", priority=Priority.NORMAL),
    ]
    results = await orch.run_strategy(tasks)

    assert len(results) == 3
    assert merge_calls["count"] == 3
    assert merge_order == ["b", "c", "a"]

    await orch.stop()
