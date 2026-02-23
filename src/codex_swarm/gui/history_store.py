from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class WorkerRecord:
    worker_id: str
    task: str
    status: str
    summary: str
    diff_text: str
    estimated_cost: float
    total_tokens: int
    requires_approval: bool
    merged: bool


class SwarmHistoryStore:
    def __init__(self, db_path: Path | str, max_runs: int = 200):
        self.db_path = Path(db_path).expanduser()
        self.max_runs = max_runs
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        self._conn.close()

    def _init_db(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                strategy TEXT,
                task_payload TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                repo TEXT,
                supervisor_model TEXT,
                worker_model TEXT,
                total_cost REAL DEFAULT 0.0,
                total_tokens INTEGER DEFAULT 0,
                error_message TEXT,
                config_snapshot TEXT
            );

            CREATE TABLE IF NOT EXISTS workers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                task TEXT,
                status TEXT,
                summary TEXT,
                diff_text TEXT,
                estimated_cost REAL DEFAULT 0.0,
                total_tokens INTEGER DEFAULT 0,
                requires_approval INTEGER DEFAULT 0,
                merged INTEGER DEFAULT 0,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            """
        )
        self._conn.commit()

    def create_run(
        self,
        run_id: str,
        mode: str,
        strategy: str | None,
        task_payload: str,
        repo: str,
        supervisor_model: str | None,
        worker_model: str | None,
        config_snapshot: str,
    ) -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO runs (
                run_id, mode, strategy, task_payload, status, started_at, repo,
                supervisor_model, worker_model, config_snapshot
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (run_id, mode, strategy, task_payload, started_at, repo, supervisor_model, worker_model, config_snapshot),
        )
        self._conn.commit()

    def append_worker(self, run_id: str, record: WorkerRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO workers (
                run_id, worker_id, task, status, summary, diff_text, estimated_cost,
                total_tokens, requires_approval, merged
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record.worker_id,
                record.task,
                record.status,
                record.summary,
                record.diff_text,
                record.estimated_cost,
                record.total_tokens,
                1 if record.requires_approval else 0,
                1 if record.merged else 0,
            ),
        )
        self._conn.commit()

    def worker_exists(self, run_id: str, worker_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM workers WHERE run_id = ? AND worker_id = ?",
            (run_id, worker_id),
        ).fetchone()
        return row is not None

    def update_worker_status(self, run_id: str, worker_id: str, status: str, merged: bool | None = None) -> None:
        query = "UPDATE workers SET status = ? WHERE run_id = ? AND worker_id = ?"
        params: list[Any] = [status, run_id, worker_id]
        if merged is not None:
            query = "UPDATE workers SET status = ?, merged = ? WHERE run_id = ? AND worker_id = ?"
            params = [status, 1 if merged else 0, run_id, worker_id]

        self._conn.execute(query, params)
        self._conn.commit()

    def upsert_worker(self, run_id: str, record: WorkerRecord) -> None:
        if self.worker_exists(run_id, record.worker_id):
            self._conn.execute(
                """
                UPDATE workers
                   SET task = ?, status = ?, summary = ?, diff_text = ?,
                       estimated_cost = ?, total_tokens = ?, requires_approval = ?, merged = ?
                 WHERE run_id = ? AND worker_id = ?
                """,
                (
                    record.task,
                    record.status,
                    record.summary,
                    record.diff_text,
                    record.estimated_cost,
                    record.total_tokens,
                    1 if record.requires_approval else 0,
                    1 if record.merged else 0,
                    run_id,
                    record.worker_id,
                ),
            )
        else:
            self.append_worker(run_id, record)
        self._conn.commit()

    def append_event(self, run_id: str, event_type: str, payload: str) -> None:
        self._conn.execute(
            "INSERT INTO events (run_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (run_id, event_type, payload, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def finalize_run(
        self,
        run_id: str,
        status: str,
        total_cost: float,
        total_tokens: int,
        error_message: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE runs
               SET status = ?, ended_at = ?, total_cost = ?, total_tokens = ?, error_message = ?
             WHERE run_id = ?
            """,
            (status, datetime.now(timezone.utc).isoformat(), total_cost, total_tokens, error_message, run_id),
        )
        self._conn.commit()
        self._prune_runs()

    def list_runs(
        self,
        status: str | None = None,
        mode: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if since:
            clauses.append("(ended_at IS NOT NULL AND ended_at >= ?)")
            params.append(since)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        q = f"SELECT * FROM runs{where} ORDER BY started_at DESC"
        if limit:
            q += f" LIMIT {int(limit)}"

        rows = list(self._conn.execute(q, params))
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run_row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run_row:
            return None

        workers = self._conn.execute("SELECT * FROM workers WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        events = self._conn.execute("SELECT * FROM events WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        data = dict(run_row)
        data["workers"] = [dict(row) for row in workers]
        data["events"] = [dict(row) for row in events]
        return data

    def _prune_runs(self) -> None:
        rows = self._conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC").fetchall()
        if len(rows) <= self.max_runs:
            return

        remove_ids = [row[0] for row in rows[self.max_runs :]]
        if not remove_ids:
            return
        placeholders = ",".join("?" * len(remove_ids))
        self._conn.execute(f"DELETE FROM workers WHERE run_id IN ({placeholders})", remove_ids)
        self._conn.execute(f"DELETE FROM events WHERE run_id IN ({placeholders})", remove_ids)
        self._conn.execute(f"DELETE FROM runs WHERE run_id IN ({placeholders})", remove_ids)
        self._conn.commit()
