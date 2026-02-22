from __future__ import annotations

from codex_swarm.tui.events import DashboardState


def test_tui_event_feed_smoke() -> None:
    state = DashboardState(budget_cap=5.0)
    state.apply("dispatch.received", {"tool": "spawn_agent"})
    state.apply("worker.completed", {"worker_id": "w1", "status": "completed", "task": "task"})
    state.apply("budget.updated", {"total_cost": 0.25, "total_tokens": 1234})
    state.apply("log", {"line": "worker done"})

    assert state.supervisor_status == "running"
    assert "w1" in state.workers
    assert state.budget_cost == 0.25
    assert state.total_tokens == 1234
    assert state.logs[-1] == "worker done"
