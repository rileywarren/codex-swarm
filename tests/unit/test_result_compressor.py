from __future__ import annotations

from datetime import datetime, timezone

from codex_swarm.models import (
    Priority,
    ReturnFormat,
    SpawnAgentPayload,
    WorkerExecutionResult,
    WorkerResult,
    WorkerStatus,
)
from codex_swarm.result_compressor import ResultCompressor


def _sample_result() -> WorkerExecutionResult:
    now = datetime.now(timezone.utc)
    return WorkerExecutionResult(
        worker_id="w1",
        branch="codex-swarm/worker-w1",
        worktree_path="/tmp/worker-w1",
        task=SpawnAgentPayload(task="demo", scope=["src/**"], context="", priority=Priority.NORMAL),
        status=WorkerStatus.COMPLETED,
        result=WorkerResult(
            summary="Implemented the change",
            files_modified=["src/main.py"],
            key_decisions=["Used approach A"],
            tests_status="passed",
            confidence=0.9,
        ),
        diff_text="\n".join([f"+ line {i}" for i in range(300)]),
        started_at=now,
        ended_at=now,
    )


def test_summary_compression() -> None:
    compressor = ResultCompressor(max_summary_tokens=50, max_diff_lines=10)
    text = compressor.compress(_sample_result(), ReturnFormat.SUMMARY)
    assert "Worker: w1" in text
    assert "Implemented the change" in text


def test_diff_truncation() -> None:
    compressor = ResultCompressor(max_summary_tokens=500, max_diff_lines=5)
    text = compressor.compress(_sample_result(), ReturnFormat.DIFF)
    assert "```diff" in text
    assert "truncated" in text
