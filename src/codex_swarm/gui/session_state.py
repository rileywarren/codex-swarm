from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SessionWorkerRow:
    worker_id: str
    task: str
    status: str
    elapsed: str
    requires_approval: bool = False
    merged: bool | None = None


@dataclass(slots=True)
class SessionDashboardState:
    supervisor_status: str = "idle"
    supervisor_line: str = ""
    workers: dict[str, SessionWorkerRow] = field(default_factory=dict)
    budget_cost: float = 0.0
    budget_cap: float = 0.0
    total_tokens: int = 0
    logs: list[str] = field(default_factory=list)

    def apply(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "supervisor.completed":
            self.supervisor_status = "completed"
            self.supervisor_line = f"exit_code={payload.get('exit_code')}"
        elif event_type == "supervisor.killed":
            self.supervisor_status = "killed"
            self.supervisor_line = f"killed={payload.get('killed')}"
        elif event_type == "dispatch.received":
            self.supervisor_status = "running"
            self.supervisor_line = f"dispatch: {payload.get('tool')}"
        elif event_type == "worker.status":
            worker_id = payload.get("worker_id", "unknown")
            existing = self.workers.get(
                worker_id,
                SessionWorkerRow(
                    worker_id=worker_id,
                    task="",
                    status="queued",
                    elapsed="-",
                ),
            )
            existing.status = payload.get("status", existing.status)
            if "task" in payload:
                existing.task = payload["task"]
            self.workers[worker_id] = existing
        elif event_type == "worker.completed":
            worker_id = payload.get("worker_id", "unknown")
            self.workers[worker_id] = SessionWorkerRow(
                worker_id=worker_id,
                task=payload.get("task", ""),
                status=payload.get("status", "unknown"),
                elapsed=payload.get("elapsed", "-"),
                requires_approval=bool(payload.get("requires_approval", False)),
            )
        elif event_type == "worker.merged":
            worker_id = payload.get("worker_id", "unknown")
            row = self.workers.get(worker_id)
            if row:
                row.status = "merged" if payload.get("merged") else "merge_conflict"
                row.merged = payload.get("merged")

        elif event_type == "budget.updated":
            self.budget_cost = float(payload.get("total_cost", 0.0))
            self.total_tokens = int(payload.get("total_tokens", 0))

        line = payload.get("line") if isinstance(payload, dict) else None
        if line:
            self.logs.append(str(line))
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]
