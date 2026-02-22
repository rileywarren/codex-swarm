from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from codex_swarm.config import load_config
from codex_swarm.models import WorkerExecutionResult, WorkerResult, WorkerStatus
from codex_swarm.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_e2e_supervisor_dispatch_writes_response(git_repo, monkeypatch) -> None:
    if os.getenv("CODEX_SWARM_RUN_E2E") != "1":
        pytest.skip("Set CODEX_SWARM_RUN_E2E=1 to run live Codex e2e")

    config = load_config(
        cli_overrides={
            "ipc.method": "file_watch",
            "worktree.auto_merge": False,
            "tui.enabled": False,
        }
    )
    orch = Orchestrator(git_repo, config)
    await orch.start()
    try:
        async def fake_run_task(payload, extra_context="", worker_id=None, lifecycle_callback=None):
            wid = worker_id or "e2e-worker"
            if lifecycle_callback:
                await lifecycle_callback(wid, WorkerStatus.QUEUED, payload)
                await lifecycle_callback(wid, WorkerStatus.RUNNING, payload)
            now = datetime.now(timezone.utc)
            result = WorkerExecutionResult(
                worker_id=wid,
                branch=f"codex-swarm/worker-{wid}",
                worktree_path=f"/tmp/{wid}",
                task=payload,
                status=WorkerStatus.COMPLETED,
                result=WorkerResult(summary="e2e simulated worker execution"),
                started_at=now,
                ended_at=now,
            )
            if lifecycle_callback:
                await lifecycle_callback(wid, WorkerStatus.COMPLETED, payload)
            return result

        monkeypatch.setattr(orch.worker_manager, "run_task", fake_run_task)
        monkeypatch.setattr(orch.worker_manager, "release_worktree", lambda *args, **kwargs: None)

        task = (
            "Emit exactly one spawn_agent fenced block with JSON payload. "
            "Use task='E2E noop', scope=['README.md'], context='e2e', priority='normal', "
            "return_format='summary'. Output only the fenced block and nothing else."
        )
        result = await orch.run_supervisor(task)

        if result.exit_code != 0:
            pytest.skip(f"Live Codex supervisor failed (exit_code={result.exit_code}). Check auth/network setup.")

        response_file = git_repo / config.results.response_file
        assert response_file.exists()
        content = response_file.read_text()
        assert "codex-swarm-response" in content
        assert orch.worker_results
    finally:
        await orch.stop()
