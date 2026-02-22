from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .budget_tracker import BudgetTracker
from .config import response_file_path
from .ipc_server import IPCServer
from .logging import get_logger
from .merge_manager import MergeManager
from .models import (
    AppConfig,
    CheckWorkersPayload,
    DispatchRequest,
    IPCMessage,
    MergeResultsPayload,
    ReturnFormat,
    RuntimeEvent,
    SpawnAgentPayload,
    SpawnSwarmPayload,
    Strategy,
    SupervisorRunResult,
    TokenUsage,
    WorkerExecutionResult,
    WorkerStatus,
)
from .result_compressor import ResultCompressor
from .strategy_engine import StrategyEngine
from .supervisor_manager import SupervisorManager
from .worker_manager import WorkerManager
from .worktree_manager import WorktreeManager

logger = get_logger(__name__)


class Orchestrator:
    def __init__(self, repo_path: Path, config: AppConfig):
        self.repo_path = repo_path
        self.config = config

        self.worktree_manager = WorktreeManager(repo_path, config.worktree)
        self.budget_tracker = BudgetTracker(config.budget)
        self.worker_manager = WorkerManager(repo_path, config, self.worktree_manager, self.budget_tracker)
        self.strategy_engine = StrategyEngine(
            self.worker_manager,
            pipeline_continue_on_error=config.swarm.pipeline_continue_on_error,
        )
        self.supervisor_manager = SupervisorManager(repo_path, config)
        self.merge_manager = MergeManager(repo_path)
        self.compressor = ResultCompressor(
            max_summary_tokens=config.results.max_summary_tokens,
            max_diff_lines=config.results.max_diff_lines,
        )

        self.ipc_server = IPCServer(Path(config.ipc.socket_path), config.ipc.message_terminator)
        self.worker_results: dict[str, WorkerExecutionResult] = {}
        self.worker_states: dict[str, dict[str, Any]] = {}
        self.pending_approval: set[str] = set()
        self.subscribers: list[asyncio.Queue[RuntimeEvent]] = []
        self.background_tasks: set[asyncio.Task[Any]] = set()
        self._last_supervisor_usage = TokenUsage()

    async def start(self) -> None:
        self.worktree_manager.cleanup_stale()
        if self.config.ipc.method == "unix_socket":
            await self.ipc_server.start(self._handle_ipc_message)
        await self._emit("orchestrator.started", {"repo": str(self.repo_path)})

    async def stop(self) -> None:
        await self.kill_supervisor()
        for task in list(self.background_tasks):
            task.cancel()
        if self.config.ipc.method == "unix_socket":
            await self.ipc_server.stop()
        await self._emit("orchestrator.stopped", {})

    def subscribe(self) -> asyncio.Queue[RuntimeEvent]:
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self.subscribers.append(queue)
        return queue

    async def run_supervisor(self, task_description: str) -> SupervisorRunResult:
        prompt = self._build_supervisor_prompt(task_description)

        async def handle_dispatch(request: DispatchRequest) -> None:
            await self.handle_dispatch(request)

        result = await self.supervisor_manager.run(
            prompt,
            dispatch_handler=handle_dispatch,
            usage_handler=self._on_supervisor_usage,
            log_handler=self._on_supervisor_log,
        )
        await self._emit("supervisor.completed", {"exit_code": result.exit_code})
        return result

    async def run_strategy(
        self,
        tasks: list[SpawnAgentPayload],
        strategy: Strategy = Strategy.FAN_OUT,
        base_context: str = "",
    ) -> list[WorkerExecutionResult]:
        results = await self.strategy_engine.execute(
            strategy,
            tasks,
            base_context,
            lifecycle_callback=self._on_worker_lifecycle,
        )
        for res in results:
            await self._register_worker_result(res)
            await self._maybe_auto_merge(res)
        return results

    async def kill_supervisor(self) -> bool:
        killed = await self.supervisor_manager.kill()
        await self._emit("supervisor.killed", {"killed": killed})
        return killed

    async def handle_dispatch(self, request: DispatchRequest) -> dict[str, Any]:
        await self._emit("dispatch.received", {"tool": request.tool, "request_id": request.request_id})

        if request.tool == "spawn_agent":
            payload = SpawnAgentPayload.model_validate(request.payload)
            result = await self.worker_manager.run_task(payload, lifecycle_callback=self._on_worker_lifecycle)
            await self._register_worker_result(result)
            merge_outcome = await self._maybe_auto_merge(result)
            response_text = self._compose_worker_response(result, payload.return_format, merge_outcome)
            await self.write_response(response_text, request_id=request.request_id)
            return {"worker_id": result.worker_id, "status": result.status.value}

        if request.tool == "spawn_swarm":
            payload = SpawnSwarmPayload.model_validate(request.payload)
            if payload.wait:
                results = await self.run_strategy(payload.tasks, payload.strategy)
                text = self._compose_swarm_response(results)
                await self.write_response(text, request_id=request.request_id)
                return {"strategy": payload.strategy.value, "workers": [r.worker_id for r in results]}

            task = asyncio.create_task(self.run_strategy(payload.tasks, payload.strategy))
            self.background_tasks.add(task)
            task.add_done_callback(lambda fut: self.background_tasks.discard(fut))
            await self.write_response("Swarm launched in background.", request_id=request.request_id)
            return {"launched": True}

        if request.tool == "check_workers":
            payload = CheckWorkersPayload.model_validate(request.payload)
            data = self._check_workers(payload.worker_ids)
            await self.write_response(json.dumps(data, indent=2), request_id=request.request_id)
            return data

        if request.tool == "merge_results":
            payload = MergeResultsPayload.model_validate(request.payload)
            data = await self._merge_results(payload)
            await self.write_response(json.dumps(data, indent=2), request_id=request.request_id)
            return data

        raise ValueError(f"Unsupported tool: {request.tool}")

    async def _register_worker_result(self, result: WorkerExecutionResult) -> None:
        self.worker_results[result.worker_id] = result
        self.worker_states[result.worker_id] = {
            "worker_id": result.worker_id,
            "status": result.status.value,
            "task": result.task.task,
            "branch": result.branch,
            "requires_approval": result.requires_approval,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if result.requires_approval:
            self.pending_approval.add(result.worker_id)

        await self._emit(
            "worker.completed",
            {
                "worker_id": result.worker_id,
                "status": result.status.value,
                "task": result.task.task,
                "requires_approval": result.requires_approval,
                "out_of_scope_files": result.out_of_scope_files,
            },
        )

    async def _maybe_auto_merge(self, result: WorkerExecutionResult) -> dict[str, Any] | None:
        can_cleanup = bool(result.worktree_path and result.branch)
        if not self.config.worktree.auto_merge:
            if can_cleanup:
                self.worker_manager.release_worktree(
                    result.worker_id,
                    result.worktree_path,
                    result.branch,
                    remove_branch=False,
                )
            return None

        if result.requires_approval:
            if can_cleanup:
                self.worker_manager.release_worktree(
                    result.worker_id,
                    result.worktree_path,
                    result.branch,
                    remove_branch=False,
                )
            return {
                "merged": False,
                "reason": "pending_supervisor_approval",
                "worker_id": result.worker_id,
            }

        if result.status.value not in {"completed"}:
            if can_cleanup:
                self.worker_manager.release_worktree(
                    result.worker_id,
                    result.worktree_path,
                    result.branch,
                    remove_branch=False,
                )
            return {"merged": False, "reason": result.status.value, "worker_id": result.worker_id}

        outcome = self.merge_manager.merge_branch(result.worker_id, result.branch, result.task.task)
        self.worker_states[result.worker_id]["status"] = "merged" if outcome.merged else "merge_conflict"
        self.worker_states[result.worker_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._emit("worker.merged", outcome.model_dump())

        if can_cleanup:
            if outcome.merged:
                self.worker_manager.release_worktree(
                    result.worker_id,
                    result.worktree_path,
                    result.branch,
                    remove_branch=True,
                )
            else:
                self.worker_manager.release_worktree(
                    result.worker_id,
                    result.worktree_path,
                    result.branch,
                    remove_branch=False,
                )

        return outcome.model_dump()

    async def _merge_results(self, payload: MergeResultsPayload) -> dict[str, Any]:
        worker_ids = payload.worker_ids or sorted(self.pending_approval)
        outcomes: list[dict[str, Any]] = []

        for worker_id in worker_ids:
            result = self.worker_results.get(worker_id)
            if not result:
                outcomes.append({"worker_id": worker_id, "merged": False, "error": "unknown worker"})
                continue

            outcome = self.merge_manager.merge_branch(
                worker_id=worker_id,
                branch=result.branch,
                task_summary=result.task.task,
                resolve_conflicts=payload.resolve_conflicts,
            )
            self.worker_states[worker_id]["status"] = "merged" if outcome.merged else "merge_conflict"
            self.worker_states[worker_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            outcomes.append(outcome.model_dump())
            await self._emit("worker.merged", outcome.model_dump())

            if outcome.merged:
                self.pending_approval.discard(worker_id)
                if result.worktree_path and result.branch:
                    self.worker_manager.release_worktree(
                        worker_id,
                        result.worktree_path,
                        result.branch,
                        remove_branch=True,
                    )

        return {"outcomes": outcomes}

    def _check_workers(self, worker_ids: list[str]) -> dict[str, Any]:
        all_ids = sorted(
            set(self.worker_states.keys())
            | set(self.worker_results.keys())
            | set(self.worker_manager.running_processes.keys())
        )
        ids = worker_ids or all_ids
        workers = []
        for worker_id in ids:
            state = self.worker_states.get(worker_id)
            record = self.worker_results.get(worker_id)
            if not state and not record:
                workers.append({"worker_id": worker_id, "status": "unknown"})
            else:
                status = (
                    state.get("status")
                    if state
                    else record.status.value if record is not None else "unknown"
                )
                workers.append(
                    {
                        "worker_id": worker_id,
                        "status": status,
                        "task": (state or {}).get("task", record.task.task if record else ""),
                        "requires_approval": bool((state or {}).get("requires_approval", False)),
                        "branch": (state or {}).get("branch", record.branch if record else ""),
                        "running": worker_id in self.worker_manager.running_processes,
                        "updated_at": (state or {}).get("updated_at", ""),
                    }
                )

        return {
            "workers": workers,
            "pending_approval": sorted(self.pending_approval),
            "budget": self.budget_tracker.snapshot().model_dump(),
        }

    async def _handle_ipc_message(self, message: IPCMessage) -> dict[str, Any] | IPCMessage | None:
        if message.type in {"spawn_agent", "spawn_swarm", "check_workers", "merge_results"}:
            request = DispatchRequest(tool=message.type, payload=message.payload, request_id=message.id)
            data = await self.handle_dispatch(request)
            return {
                "type": "response",
                "id": str(uuid.uuid4()),
                "reply_to": message.id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": data,
            }

        if message.type == "pause_queue":
            self.strategy_engine.pause_queue()
            return {"type": "ack", "payload": {"paused": True}}

        if message.type == "resume_queue":
            self.strategy_engine.resume_queue()
            return {"type": "ack", "payload": {"paused": False}}

        if message.type == "cancel_worker":
            worker_id = message.payload.get("worker_id", "")
            cancelled = await self.worker_manager.cancel_worker(worker_id)
            return {"type": "ack", "payload": {"worker_id": worker_id, "cancelled": cancelled}}

        if message.type == "kill_supervisor":
            killed = await self.kill_supervisor()
            return {"type": "ack", "payload": {"killed": killed}}

        return {"type": "error", "payload": {"message": f"unsupported type {message.type}"}}

    async def _on_supervisor_usage(self, cumulative_usage: TokenUsage) -> None:
        delta = TokenUsage(
            input_tokens=max(0, cumulative_usage.input_tokens - self._last_supervisor_usage.input_tokens),
            cached_input_tokens=max(
                0,
                cumulative_usage.cached_input_tokens - self._last_supervisor_usage.cached_input_tokens,
            ),
            output_tokens=max(0, cumulative_usage.output_tokens - self._last_supervisor_usage.output_tokens),
        )
        self._last_supervisor_usage = cumulative_usage
        self.budget_tracker.add_usage(delta, self.config.swarm.supervisor_model)
        snapshot = self.budget_tracker.snapshot()

        await self._emit("budget.updated", snapshot.model_dump())
        if snapshot.warned:
            warning = IPCMessage(
                type="budget_warning",
                payload=snapshot.model_dump(),
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                reply_to=None,
            )
            if self.config.ipc.method == "unix_socket":
                await self.ipc_server.broadcast(warning)

    async def _on_supervisor_log(self, channel: str, line: str) -> None:
        await self._emit("log", {"channel": channel, "line": line})

    async def _on_worker_lifecycle(
        self,
        worker_id: str,
        status: WorkerStatus,
        task: SpawnAgentPayload,
    ) -> None:
        state = self.worker_states.get(worker_id, {"worker_id": worker_id})
        state["status"] = status.value
        state["task"] = task.task
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.worker_states[worker_id] = state
        await self._emit("worker.status", {"worker_id": worker_id, "status": status.value, "task": task.task})

    async def write_response(self, text: str, request_id: str | None = None) -> None:
        target = response_file_path(self.repo_path, self.config)
        target.parent.mkdir(parents=True, exist_ok=True)

        marker = request_id or str(uuid.uuid4())
        payload = (
            f"\n<!-- codex-swarm-response:{marker}:start -->\n"
            f"{text.strip()}\n"
            f"<!-- codex-swarm-response:{marker}:end -->\n"
        )
        await asyncio.to_thread(self._append_text, target, payload)
        await self._emit("response.written", {"path": str(target), "request_id": marker})

    def _append_text(self, path: Path, text: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(text)

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = RuntimeEvent.now(event_type, payload)
        for queue in self.subscribers:
            queue.put_nowait(event)

        if self.config.ipc.method == "unix_socket" and event_type != "log":
            msg = IPCMessage(
                type="event",
                payload={"event_type": event_type, "payload": payload},
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                reply_to=None,
            )
            await self.ipc_server.broadcast(msg)

    def _compose_worker_response(
        self,
        result: WorkerExecutionResult,
        fmt: ReturnFormat,
        merge_outcome: dict[str, Any] | None,
    ) -> str:
        body = self.compressor.compress(result, fmt)
        if merge_outcome:
            body += "\n\nMerge outcome:\n" + json.dumps(merge_outcome, indent=2)
        if result.requires_approval:
            body += "\n\nAction required: run `merge_results` to approve and merge this worker."
        return body

    def _compose_swarm_response(self, results: list[WorkerExecutionResult]) -> str:
        parts = ["Swarm completed."]
        for item in results:
            parts.append(f"- {item.worker_id}: {item.status.value} ({item.result.summary})")
        return "\n".join(parts)

    def _build_supervisor_prompt(self, task_description: str) -> str:
        response_file = response_file_path(self.repo_path, self.config)
        return f"""
You are the Codex Swarm supervisor.

Objective:
{task_description}

You may dispatch work with fenced tool blocks using EXACT tags:
- spawn_agent
- spawn_swarm
- check_workers
- merge_results

Examples:
```spawn_agent
{{
  "task": "Implement auth refactor",
  "scope": ["src/auth/**", "tests/auth/**"],
  "context": "Keep public interfaces stable",
  "priority": "high",
  "return_format": "summary"
}}
```

Rules:
1) After emitting a dispatch block, stop and wait.
2) Read `{response_file}` for responses before continuing.
3) Do not produce additional unrelated output while waiting.
4) Use merge_results explicitly for workers flagged as pending approval.
""".strip()
