from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from codex_swarm.config import load_config
from codex_swarm.models import (
    MergeOutcome,
    Priority,
    SpawnAgentPayload,
    WorkerExecutionResult,
    WorkerResult,
    WorkerStatus,
)
from codex_swarm.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_check_workers_reports_running_state(git_repo, monkeypatch) -> None:
    config = load_config(cli_overrides={"ipc.method": "file_watch"})
    orch = Orchestrator(git_repo, config)
    await orch.start()

    async def fake_run_task(payload, extra_context="", worker_id=None, lifecycle_callback=None):
        wid = worker_id or "live1"
        if lifecycle_callback:
            await lifecycle_callback(wid, WorkerStatus.QUEUED, payload)
            await lifecycle_callback(wid, WorkerStatus.RUNNING, payload)
        await asyncio.sleep(0.05)
        now = datetime.now(timezone.utc)
        result = WorkerExecutionResult(
            worker_id=wid,
            branch=f"codex-swarm/worker-{wid}",
            worktree_path=f"/tmp/{wid}",
            task=payload,
            status=WorkerStatus.COMPLETED,
            result=WorkerResult(summary="done"),
            started_at=now,
            ended_at=now,
        )
        if lifecycle_callback:
            await lifecycle_callback(wid, WorkerStatus.COMPLETED, payload)
        return result

    monkeypatch.setattr(orch.worker_manager, "run_task", fake_run_task)
    monkeypatch.setattr(orch.worker_manager, "release_worktree", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        orch.merge_manager,
        "merge_branch",
        lambda worker_id, branch, task_summary, resolve_conflicts="abort": MergeOutcome(
            worker_id=worker_id,
            branch=branch,
            merged=True,
            conflict=False,
            message="ok",
        ),
    )

    tasks = [SpawnAgentPayload(task="A", priority=Priority.NORMAL)]
    strategy_task = asyncio.create_task(orch.run_strategy(tasks))

    await asyncio.sleep(0.01)
    live = orch._check_workers([])
    assert any(worker["status"] in {"queued", "running"} for worker in live["workers"])

    await strategy_task
    done = orch._check_workers([])
    assert any(worker["status"] in {"completed", "merged"} for worker in done["workers"])

    await orch.stop()
