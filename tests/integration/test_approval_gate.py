from __future__ import annotations

from datetime import datetime, timezone

import pytest

from codex_swarm.config import load_config
from codex_swarm.models import (
    DispatchRequest,
    MergeOutcome,
    Priority,
    ReturnFormat,
    SpawnAgentPayload,
    WorkerExecutionResult,
    WorkerResult,
    WorkerResultStatus,
    WorkerStatus,
)
from codex_swarm.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_out_of_scope_requires_explicit_merge(git_repo, monkeypatch) -> None:
    config = load_config(cli_overrides={"ipc.method": "file_watch"})
    orch = Orchestrator(git_repo, config)
    await orch.start()

    now = datetime.now(timezone.utc)
    worker_result = WorkerExecutionResult(
        worker_id="w-approval",
        branch="codex-swarm/worker-w-approval",
        worktree_path="/tmp/w-approval",
        task=SpawnAgentPayload(
            task="change",
            scope=["src/**"],
            context="",
            priority=Priority.NORMAL,
            return_format=ReturnFormat.SUMMARY,
        ),
        status=WorkerStatus.PENDING_APPROVAL,
        result=WorkerResult(
            status=WorkerResultStatus.PARTIAL,
            summary="changed files",
            warnings=["Out-of-scope edits require supervisor approval"],
        ),
        requires_approval=True,
        out_of_scope_files=["README.md"],
        started_at=now,
        ended_at=now,
    )

    merge_calls = {"count": 0}

    async def fake_run_task(payload, extra_context="", worker_id=None, lifecycle_callback=None):
        if lifecycle_callback:
            await lifecycle_callback(worker_result.worker_id, WorkerStatus.QUEUED, payload)
            await lifecycle_callback(worker_result.worker_id, WorkerStatus.RUNNING, payload)
            await lifecycle_callback(worker_result.worker_id, WorkerStatus.PENDING_APPROVAL, payload)
        return worker_result

    def fake_merge_branch(worker_id: str, branch: str, task_summary: str, resolve_conflicts: str = "abort"):
        merge_calls["count"] += 1
        return MergeOutcome(worker_id=worker_id, branch=branch, merged=True, conflict=False, message="ok")

    monkeypatch.setattr(orch.worker_manager, "run_task", fake_run_task)
    monkeypatch.setattr(orch.worker_manager, "release_worktree", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch.merge_manager, "merge_branch", fake_merge_branch)

    dispatch = DispatchRequest(
        tool="spawn_agent",
        payload={
            "task": "change",
            "scope": ["src/**"],
            "context": "",
            "priority": "normal",
            "return_format": "summary",
        },
    )

    await orch.handle_dispatch(dispatch)
    assert merge_calls["count"] == 0
    assert "w-approval" in orch.pending_approval

    merge_dispatch = DispatchRequest(tool="merge_results", payload={"worker_ids": ["w-approval"]})
    await orch.handle_dispatch(merge_dispatch)

    assert merge_calls["count"] == 1
    assert "w-approval" not in orch.pending_approval

    await orch.stop()
