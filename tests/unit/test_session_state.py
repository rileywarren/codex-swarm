from __future__ import annotations

from codex_swarm.gui.session_state import SessionDashboardState


def test_session_state_tracks_worker_lifecycle() -> None:
    state = SessionDashboardState()
    state.apply("dispatch.received", {"tool": "spawn_agent"})
    assert state.supervisor_status == "running"

    state.apply("worker.status", {"worker_id": "w1", "status": "queued", "task": "first"})
    state.apply(
        "worker.completed",
        {"worker_id": "w1", "status": "completed", "task": "first", "elapsed": "00:00:01"},
    )
    worker = state.workers["w1"]
    assert worker.task == "first"
    assert worker.status == "completed"
    assert worker.elapsed == "00:00:01"

    state.apply("worker.merged", {"worker_id": "w1", "merged": True})
    assert state.workers["w1"].status == "merged"
    assert state.workers["w1"].merged is True


def test_session_state_budget_and_logs() -> None:
    state = SessionDashboardState()
    state.apply("budget.updated", {"total_cost": 3.5, "total_tokens": 25})
    assert state.budget_cost == 3.5
    assert state.total_tokens == 25

    state.apply("log", {"line": "first line"})
    state.apply("log", {"line": "second line"})
    assert len(state.logs) == 2
    assert state.logs[-1] == "second line"
