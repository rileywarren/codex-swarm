from __future__ import annotations

import contextlib
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, Signal
import yaml
from yaml import YAMLError

from codex_swarm.model_catalog import list_available_models, read_codex_default_model, save_codex_swarm_default_models
from codex_swarm.models import AppConfig, DispatchRequest, MergeResultsPayload, SpawnAgentPayload, Strategy
from codex_swarm.orchestrator import Orchestrator

from .history_store import SwarmHistoryStore, WorkerRecord
from .session_state import SessionDashboardState


class SessionController(QObject):
    state_updated = Signal()
    run_finished = Signal(str, str)

    def __init__(self, repo_path: Path, base_config: AppConfig, session_id: str, history: SwarmHistoryStore) -> None:
        super().__init__()
        self.repo_path = repo_path
        self.session_id = session_id
        self.history = history
        self.config = self._build_session_config(session_id, base_config)
        self.orchestrator = Orchestrator(repo_path, self.config)
        self.event_queue = self.orchestrator.subscribe()
        self.state = SessionDashboardState(budget_cap=float(self.config.budget.max_total_cost))
        try:
            self.available_models = [item.slug for item in list_available_models()]
        except Exception:  # noqa: BLE001
            self.available_models = []
        self.current_model = self.config.swarm.supervisor_model or read_codex_default_model()

        self._run_task: asyncio.Task[Any] | None = None
        self._active = False
        self._run_id: str | None = None
        self._run_budget: dict[str, tuple[float, int]] = {}
        self._orchestrator_started = False
        self._event_consumer: asyncio.Task[Any] | None = None
        self.queue_paused = False

    @staticmethod
    def _build_session_config(session_id: str, base_config: AppConfig) -> AppConfig:
        payload = base_config.model_dump()
        payload["results"]["response_file"] = f".codex-swarm-response.{session_id}.md"
        payload["ipc"]["method"] = "file_watch"
        return AppConfig.model_validate(payload)

    @property
    def active(self) -> bool:
        return self._active

    def start_event_consumer(self) -> None:
        if self._event_consumer is not None and not self._event_consumer.done():
            return
        loop = asyncio.get_event_loop()
        self._event_consumer = loop.create_task(self._consume_events())

    async def ensure_started(self) -> None:
        if self._orchestrator_started:
            return
        await self.orchestrator.start()
        self._orchestrator_started = True

    def _set_active(self, active: bool, run_id: str | None = None, status: str = "running") -> None:
        self._active = active
        if active and run_id:
            self._run_id = run_id
        elif not active and (self._run_id == run_id or run_id is None):
            self._run_id = None
        self.state.supervisor_status = status

    async def start_supervisor(self, task_text: str) -> None:
        if self._run_task is not None and not self._run_task.done():
            return
        task_text = (task_text or "").strip()
        if not task_text:
            raise ValueError("Supervisor task cannot be empty")

        await self.ensure_started()

        run_id = str(uuid4())
        self._run_budget[run_id] = self._snapshot_budget()
        self._set_active(True, run_id)
        self.state.supervisor_line = "supervisor run started"
        self.history.create_run(
            run_id=run_id,
            mode="supervisor",
            strategy=None,
            task_payload=task_text,
            repo=str(self.repo_path),
            supervisor_model=self.config.swarm.supervisor_model,
            worker_model=self.config.swarm.worker_model,
            config_snapshot=json.dumps(self.config.model_dump()),
        )
        self.state.logs.append(f"Run started: {run_id}")
        self.state_updated.emit()

        async def runner() -> None:
            try:
                result = await self.orchestrator.run_supervisor(task_text)
                status = "completed" if result.exit_code == 0 else "failed"
                self._finalize_run(run_id, status, exit_code=result.exit_code)
            except Exception as exc:  # noqa: BLE001
                self._finalize_run(run_id, "failed", error=str(exc))

        self._run_task = asyncio.create_task(runner())

    async def start_strategy(self, strategy_value: str, tasks_text: str) -> None:
        if self._run_task is not None and not self._run_task.done():
            return
        tasks = self._parse_tasks(tasks_text)
        strategy = Strategy(strategy_value)

        await self.ensure_started()

        run_id = str(uuid4())
        self._run_budget[run_id] = self._snapshot_budget()
        self._set_active(True, run_id)
        self.state.supervisor_line = f"strategy run started: {strategy.value}"
        self.history.create_run(
            run_id=run_id,
            mode="strategy",
            strategy=strategy.value,
            task_payload=tasks_text,
            repo=str(self.repo_path),
            supervisor_model=self.config.swarm.supervisor_model,
            worker_model=self.config.swarm.worker_model,
            config_snapshot=json.dumps(self.config.model_dump()),
        )
        self.state.logs.append(f"Run started: {run_id}")
        self.state_updated.emit()

        async def runner() -> None:
            try:
                results = await self.orchestrator.run_strategy(tasks, strategy=strategy)
                status = "completed"
                if any(r.status.value in {"failed", "timed_out", "blocked"} for r in results):
                    status = "failed"
                self._finalize_run(
                    run_id,
                    status,
                    result_total_cost=sum(r.estimated_cost for r in results),
                    total_tokens=sum(r.usage.total_tokens for r in results),
                )
            except Exception as exc:  # noqa: BLE001
                self._finalize_run(run_id, "failed", error=str(exc))

        self._run_task = asyncio.create_task(runner())

    def _finalize_run(
        self,
        run_id: str,
        status: str,
        exit_code: int | None = None,
        result_total_cost: float | None = None,
        total_tokens: int | None = None,
        error: str | None = None,
    ) -> None:
        if self._run_id == run_id:
            self._run_id = None
        baseline = self._run_budget.pop(run_id, None)

        if result_total_cost is None or total_tokens is None:
            snapshot = self.orchestrator.budget_tracker.snapshot()
            base_cost, base_tokens = baseline or (0.0, 0)
            result_total_cost = float(snapshot.total_cost - base_cost)
            total_tokens = int(snapshot.total_tokens - base_tokens)
        else:
            # Caller already provided explicit totals. Record those values as-is.
            result_total_cost = float(result_total_cost)
            total_tokens = int(total_tokens)

        if exit_code is not None and exit_code != 0 and not error:
            error_message = f"Supervisor exited with {exit_code}"
        else:
            error_message = error

        self.history.finalize_run(
            run_id,
            status,
            float(result_total_cost),
            int(total_tokens),
            error_message,
        )
        self._set_active(False, run_id, status)
        if error_message:
            self.state.logs.append(f"Run {run_id} failed: {error_message}")
        else:
            self.state.logs.append(f"Run {run_id} finished: {status}")
        self.state_updated.emit()
        self.run_finished.emit(run_id, status)

    def _snapshot_worker_from_orchestrator(self, run_id: str, worker_id: str) -> WorkerRecord | None:
        result = self.orchestrator.worker_results.get(worker_id)
        if not result:
            return None

        state = self.orchestrator.worker_states.get(worker_id, {})
        return WorkerRecord(
            worker_id=result.worker_id,
            task=result.task.task,
            status=result.status.value,
            summary=result.result.summary,
            diff_text=result.diff_text,
            estimated_cost=float(result.estimated_cost),
            total_tokens=int(result.usage.total_tokens),
            requires_approval=bool(result.requires_approval),
            merged=bool(state.get("status") == "merged"),
        )

    def _parse_tasks(self, text: str) -> list[SpawnAgentPayload]:
        try:
            loaded = yaml.safe_load(text or "[]")
        except YAMLError as exc:
            raise ValueError(f"Strategy input must be valid JSON/YAML: {exc}") from exc
        if isinstance(loaded, dict):
            if "tasks" in loaded:
                loaded = loaded["tasks"]
        if not isinstance(loaded, list):
            raise ValueError("Strategy input must be a list of task objects")
        if not loaded:
            raise ValueError("Strategy input cannot be empty")
        return [SpawnAgentPayload.model_validate(item) for item in loaded]

    async def cancel_worker(self, worker_id: str) -> None:
        if worker_id:
            await self.orchestrator.worker_manager.cancel_worker(worker_id)

    async def merge_workers(self, worker_ids: list[str], resolve_conflicts: str = "abort") -> None:
        payload = MergeResultsPayload(worker_ids=worker_ids, resolve_conflicts=resolve_conflicts)
        request = DispatchRequest(tool="merge_results", payload=payload.model_dump())
        await self.orchestrator.handle_dispatch(request)

    async def pause_queue(self) -> bool:
        self.queue_paused = not self.queue_paused
        if self.queue_paused:
            self.orchestrator.strategy_engine.pause_queue()
            self.state.logs.append("Queue paused")
        else:
            self.orchestrator.strategy_engine.resume_queue()
            self.state.logs.append("Queue resumed")
        self.state_updated.emit()
        return self.queue_paused

    async def kill_supervisor(self) -> None:
        await self.orchestrator.kill_supervisor()

    async def set_default_model(self, model: str) -> str:
        path = save_codex_swarm_default_models(model, model)
        self.current_model = model
        self.orchestrator.config.swarm.supervisor_model = model
        self.orchestrator.config.swarm.worker_model = model
        self.state.logs.append(f"Default model set to {model}")
        self.state_updated.emit()
        return str(path)

    async def shutdown(self) -> None:
        run_id = self._run_id

        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
            self._run_task = None

        if run_id is not None:
            self._set_active(False, run_id, "failed")
            snapshot = self.orchestrator.budget_tracker.snapshot()
            base_cost, base_tokens = self._run_budget.pop(run_id, (0.0, 0))
            try:
                self.history.finalize_run(
                    run_id,
                    "failed",
                    float(snapshot.total_cost - base_cost),
                    int(snapshot.total_tokens - base_tokens),
                    "Session cancelled",
                )
            except Exception:  # noqa: BLE001
                pass

        await self.orchestrator.kill_supervisor()
        if self._orchestrator_started:
            await self.orchestrator.stop()
            self._orchestrator_started = False

        if self._event_consumer is not None:
            self._event_consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_consumer

    async def _consume_events(self) -> None:
        try:
            while True:
                event = await self.event_queue.get()
                self.state.apply(event.event_type, event.payload)
                current_run = self._run_id
                if current_run:
                    self.history.append_event(current_run, event.event_type, json.dumps(event.payload))

                if event.event_type == "worker.completed" and current_run:
                    worker_id = event.payload.get("worker_id")
                    if isinstance(worker_id, str):
                        record = self._snapshot_worker_from_orchestrator(current_run, worker_id)
                        if record:
                            self.history.upsert_worker(current_run, record)

                if event.event_type == "worker.merged" and current_run:
                    worker_id = event.payload.get("worker_id")
                    if isinstance(worker_id, str):
                        merged = bool(event.payload.get("merged"))
                        status = "merged" if merged else "merge_conflict"
                        self.history.update_worker_status(current_run, worker_id, status=status, merged=merged)
                self.state_updated.emit()
        except asyncio.CancelledError:
            return

    def _snapshot_budget(self) -> tuple[float, int]:
        snapshot = self.orchestrator.budget_tracker.snapshot()
        return snapshot.total_cost, snapshot.total_tokens
