from __future__ import annotations

import time

from codex_swarm.gui.history_store import SwarmHistoryStore, WorkerRecord


def test_history_store_round_trip(tmp_path) -> None:
    store = SwarmHistoryStore(tmp_path / "history.db", max_runs=3)
    store.create_run(
        run_id="run-1",
        mode="supervisor",
        strategy=None,
        task_payload="task",
        repo=".",
        supervisor_model=None,
        worker_model=None,
        config_snapshot="{}",
    )
    store.append_worker(
        "run-1",
        WorkerRecord(
            worker_id="w1",
            task="task 1",
            status="completed",
            summary="ok",
            diff_text="-a",
            estimated_cost=1.5,
            total_tokens=10,
            requires_approval=False,
            merged=True,
        ),
    )
    store.finalize_run("run-1", "completed", total_cost=1.5, total_tokens=10)

    data = store.get_run("run-1")
    assert data is not None
    assert data["run_id"] == "run-1"
    assert data["status"] == "completed"
    assert len(data["workers"]) == 1
    assert data["workers"][0]["worker_id"] == "w1"
    assert data["workers"][0]["merged"] == 1


def test_history_store_worker_upsert_updates_existing(tmp_path) -> None:
    store = SwarmHistoryStore(tmp_path / "history.db")
    store.create_run(
        run_id="run-1",
        mode="supervisor",
        strategy=None,
        task_payload="task",
        repo=".",
        supervisor_model=None,
        worker_model=None,
        config_snapshot="{}",
    )

    store.append_worker(
        "run-1",
        WorkerRecord(
            worker_id="w1",
            task="initial",
            status="queued",
            summary="old",
            diff_text="-a",
            estimated_cost=1.0,
            total_tokens=2,
            requires_approval=False,
            merged=False,
        ),
    )
    store.upsert_worker(
        "run-1",
        WorkerRecord(
            worker_id="w1",
            task="updated",
            status="completed",
            summary="done",
            diff_text="-b",
            estimated_cost=2.0,
            total_tokens=3,
            requires_approval=False,
            merged=True,
        ),
    )
    data = store.get_run("run-1")
    assert data is not None
    assert len(data["workers"]) == 1
    assert data["workers"][0]["task"] == "updated"
    assert data["workers"][0]["status"] == "completed"
    assert data["workers"][0]["merged"] == 1


def test_history_store_updates_worker_status(tmp_path) -> None:
    store = SwarmHistoryStore(tmp_path / "history.db")
    store.create_run(
        run_id="run-1",
        mode="strategy",
        strategy="fan-out",
        task_payload="[]",
        repo=".",
        supervisor_model=None,
        worker_model=None,
        config_snapshot="{}",
    )
    store.append_worker(
        "run-1",
        WorkerRecord(
            worker_id="w1",
            task="task",
            status="completed",
            summary="ok",
            diff_text="-a",
            estimated_cost=1.0,
            total_tokens=2,
            requires_approval=False,
            merged=False,
        ),
    )
    store.update_worker_status("run-1", "w1", status="merged", merged=True)
    data = store.get_run("run-1")
    assert data is not None
    assert data["workers"][0]["status"] == "merged"
    assert data["workers"][0]["merged"] == 1


def test_history_store_prunes_runs(tmp_path) -> None:
    store = SwarmHistoryStore(tmp_path / "history.db", max_runs=2)

    def create_and_finalize(run_id: str) -> None:
        store.create_run(
            run_id=run_id,
            mode="supervisor",
            strategy=None,
            task_payload="task",
            repo=".",
            supervisor_model=None,
            worker_model=None,
            config_snapshot="{}",
        )
        time.sleep(0.01)
        store.finalize_run(run_id, "completed", total_cost=1.0, total_tokens=1)

    create_and_finalize("run-1")
    create_and_finalize("run-2")
    create_and_finalize("run-3")

    runs = store.list_runs(limit=None)
    assert len(runs) == 2
    run_ids = {run["run_id"] for run in runs}
    assert "run-1" not in run_ids
    assert run_ids == {"run-2", "run-3"}
