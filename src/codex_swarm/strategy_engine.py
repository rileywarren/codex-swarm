from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from .models import Priority, ReturnFormat, SpawnAgentPayload, Strategy, WorkerExecutionResult, WorkerStatus
from .worker_manager import WorkerManager

WorkerLifecycleCallback = Callable[[str, WorkerStatus, SpawnAgentPayload], Awaitable[None]]


class StrategyEngine:
    def __init__(self, worker_manager: WorkerManager, pipeline_continue_on_error: bool = False):
        self.worker_manager = worker_manager
        self.pipeline_continue_on_error = pipeline_continue_on_error
        self.queue_paused = False
        self._queue_event = asyncio.Event()
        self._queue_event.set()

    def pause_queue(self) -> None:
        self.queue_paused = True
        self._queue_event.clear()

    def resume_queue(self) -> None:
        self.queue_paused = False
        self._queue_event.set()

    async def execute(
        self,
        strategy: Strategy,
        tasks: list[SpawnAgentPayload],
        base_context: str = "",
        lifecycle_callback: WorkerLifecycleCallback | None = None,
    ) -> list[WorkerExecutionResult]:
        if strategy == Strategy.FAN_OUT:
            return await self.execute_fan_out(tasks, base_context, lifecycle_callback)
        if strategy == Strategy.PIPELINE:
            return await self.execute_pipeline(tasks, base_context, lifecycle_callback)
        if strategy == Strategy.MAP_REDUCE:
            return await self.execute_map_reduce(tasks, base_context, lifecycle_callback)
        if strategy == Strategy.DEBATE:
            return await self.execute_debate(tasks, base_context, lifecycle_callback)
        raise ValueError(f"Unsupported strategy: {strategy}")

    async def execute_fan_out(
        self,
        tasks: list[SpawnAgentPayload],
        base_context: str = "",
        lifecycle_callback: WorkerLifecycleCallback | None = None,
    ) -> list[WorkerExecutionResult]:
        ordered = sorted(tasks, key=self._priority_rank)

        async def run(payload: SpawnAgentPayload) -> WorkerExecutionResult:
            await self._queue_event.wait()
            return await self.worker_manager.run_task(
                payload,
                extra_context=base_context,
                lifecycle_callback=lifecycle_callback,
            )

        scheduled = [asyncio.create_task(run(task)) for task in ordered]
        results: list[WorkerExecutionResult] = []
        for pending in asyncio.as_completed(scheduled):
            results.append(await pending)
        return results

    async def execute_pipeline(
        self,
        tasks: list[SpawnAgentPayload],
        base_context: str = "",
        lifecycle_callback: WorkerLifecycleCallback | None = None,
    ) -> list[WorkerExecutionResult]:
        results: list[WorkerExecutionResult] = []
        rolling_context = base_context

        for task in tasks:
            await self._queue_event.wait()
            result = await self.worker_manager.run_task(
                task,
                extra_context=rolling_context,
                lifecycle_callback=lifecycle_callback,
            )
            results.append(result)
            rolling_context = (
                f"{rolling_context}\n\nPrevious step {result.worker_id} summary:\n"
                f"{result.result.summary}"
            ).strip()

            if not self.pipeline_continue_on_error and result.status.value in {"failed", "timed_out"}:
                break

        return results

    async def execute_map_reduce(
        self,
        tasks: list[SpawnAgentPayload],
        base_context: str = "",
        lifecycle_callback: WorkerLifecycleCallback | None = None,
    ) -> list[WorkerExecutionResult]:
        map_results = await self.execute_fan_out(tasks, base_context, lifecycle_callback)
        reducer_context_parts = [base_context, "Map worker results:"]
        for item in map_results:
            reducer_context_parts.append(f"- {item.worker_id}: {item.result.summary}")

        reducer_task = SpawnAgentPayload(
            task="Produce a consolidated summary of all map results and list final recommendations.",
            scope=[],
            context="\n".join(reducer_context_parts).strip(),
            priority=Priority.NORMAL,
            return_format=ReturnFormat.SUMMARY,
        )
        reducer_result = await self.worker_manager.run_task(reducer_task, lifecycle_callback=lifecycle_callback)
        return map_results + [reducer_result]

    async def execute_debate(
        self,
        tasks: list[SpawnAgentPayload],
        base_context: str = "",
        lifecycle_callback: WorkerLifecycleCallback | None = None,
    ) -> list[WorkerExecutionResult]:
        results = await self.execute_fan_out(tasks, base_context, lifecycle_callback)
        successful = [r for r in results if r.status.value in {"completed", "pending_approval"}]
        if successful:
            winner = max(successful, key=lambda item: item.result.confidence)
            winner.result.key_decisions.append("debate_winner")
        return results

    @staticmethod
    def _priority_rank(task: SpawnAgentPayload) -> int:
        if task.priority.value == "high":
            return 0
        if task.priority.value == "normal":
            return 1
        return 2
