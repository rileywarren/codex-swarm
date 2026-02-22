from __future__ import annotations

from datetime import datetime, timezone

import pytest

from codex_swarm.models import (
    Priority,
    SpawnAgentPayload,
    WorkerExecutionResult,
    WorkerResult,
    WorkerStatus,
)
from codex_swarm.strategy_engine import StrategyEngine


class FakeWorkerManager:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.counter = 0

    async def run_task(
        self,
        payload: SpawnAgentPayload,
        extra_context: str = "",
        worker_id: str | None = None,
        lifecycle_callback=None,
    ):
        self.counter += 1
        wid = worker_id or f"w{self.counter}"
        self.calls.append((payload.task, extra_context))
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
            result=WorkerResult(summary=f"done {payload.task}", confidence=0.1 * self.counter),
            started_at=now,
            ended_at=now,
        )
        if lifecycle_callback:
            await lifecycle_callback(wid, WorkerStatus.COMPLETED, payload)
        return result


@pytest.mark.asyncio
async def test_strategy_fan_out() -> None:
    fake = FakeWorkerManager()
    engine = StrategyEngine(fake)
    tasks = [
        SpawnAgentPayload(task="A", scope=[], context="", priority=Priority.NORMAL),
        SpawnAgentPayload(task="B", scope=[], context="", priority=Priority.HIGH),
        SpawnAgentPayload(task="C", scope=[], context="", priority=Priority.LOW),
    ]

    results = await engine.execute_fan_out(tasks, "base")
    assert len(results) == 3
    # High priority should be scheduled first.
    assert fake.calls[0][0] == "B"


@pytest.mark.asyncio
async def test_strategy_pipeline() -> None:
    fake = FakeWorkerManager()
    engine = StrategyEngine(fake)
    tasks = [SpawnAgentPayload(task="A"), SpawnAgentPayload(task="B")]

    results = await engine.execute_pipeline(tasks, "seed")
    assert len(results) == 2
    assert "Previous step" in fake.calls[1][1]


@pytest.mark.asyncio
async def test_pipeline_continue_on_error_modes() -> None:
    class FailingWorkerManager(FakeWorkerManager):
        async def run_task(self, payload, extra_context="", worker_id=None, lifecycle_callback=None):
            result = await super().run_task(payload, extra_context, worker_id, lifecycle_callback)
            if payload.task == "A":
                result.status = WorkerStatus.FAILED
            return result

    tasks = [SpawnAgentPayload(task="A"), SpawnAgentPayload(task="B")]

    stop_engine = StrategyEngine(FailingWorkerManager(), pipeline_continue_on_error=False)
    stop_results = await stop_engine.execute_pipeline(tasks, "seed")
    assert len(stop_results) == 1

    continue_engine = StrategyEngine(FailingWorkerManager(), pipeline_continue_on_error=True)
    continue_results = await continue_engine.execute_pipeline(tasks, "seed")
    assert len(continue_results) == 2


@pytest.mark.asyncio
async def test_strategy_map_reduce_and_debate() -> None:
    fake = FakeWorkerManager()
    engine = StrategyEngine(fake)
    tasks = [SpawnAgentPayload(task="A"), SpawnAgentPayload(task="B")]

    map_reduce = await engine.execute_map_reduce(tasks, "ctx")
    assert len(map_reduce) == 3

    debate = await engine.execute_debate(tasks, "ctx")
    assert len(debate) == 2
    assert any("debate_winner" in r.result.key_decisions for r in debate)
