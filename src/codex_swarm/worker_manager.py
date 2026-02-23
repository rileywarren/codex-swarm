from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import pathspec

from .budget_tracker import BudgetTracker
from .dispatch_parser import parse_agent_message_from_json_line, parse_usage_from_json_line
from .logging import get_logger
from .models import (
    AppConfig,
    SpawnAgentPayload,
    TokenUsage,
    WorkerExecutionResult,
    WorkerResult,
    WorkerResultStatus,
    WorkerStatus,
)
from .worktree_manager import WorktreeManager

logger = get_logger(__name__)
WorkerLifecycleCallback = Callable[[str, WorkerStatus, SpawnAgentPayload], Awaitable[None]]


class WorkerManager:
    def __init__(
        self,
        repo_path: Path,
        config: AppConfig,
        worktree_manager: WorktreeManager,
        budget_tracker: BudgetTracker,
    ):
        self.repo_path = repo_path
        self.config = config
        self.worktree_manager = worktree_manager
        self.budget_tracker = budget_tracker
        self.semaphore = asyncio.Semaphore(config.swarm.max_workers)
        self.running_processes: dict[str, asyncio.subprocess.Process] = {}
        self.worker_branches: dict[str, str] = {}

    async def cancel_worker(self, worker_id: str) -> bool:
        process = self.running_processes.get(worker_id)
        if not process:
            return False
        process.kill()
        await process.wait()
        return True

    async def run_task(
        self,
        payload: SpawnAgentPayload,
        extra_context: str = "",
        worker_id: str | None = None,
        lifecycle_callback: WorkerLifecycleCallback | None = None,
    ) -> WorkerExecutionResult:
        wid = worker_id or str(uuid.uuid4())[:8]
        if lifecycle_callback:
            await lifecycle_callback(wid, WorkerStatus.QUEUED, payload)

        allowed, reason = self.budget_tracker.can_spawn_worker()
        if not allowed:
            now = datetime.now(timezone.utc)
            if lifecycle_callback:
                await lifecycle_callback(wid, WorkerStatus.BLOCKED, payload)
            return WorkerExecutionResult(
                worker_id=wid,
                branch="",
                worktree_path="",
                task=payload,
                status=WorkerStatus.BLOCKED,
                result=WorkerResult(
                    status=WorkerResultStatus.BLOCKED,
                    summary=f"Worker blocked by budget policy: {reason}",
                    warnings=[reason],
                ),
                usage=TokenUsage(),
                started_at=now,
                ended_at=now,
                error=reason,
            )

        started_at = datetime.now(timezone.utc)

        async with self.semaphore:
            if lifecycle_callback:
                await lifecycle_callback(wid, WorkerStatus.RUNNING, payload)
            info = await asyncio.to_thread(self.worktree_manager.create, wid)
            self.worker_branches[wid] = info.branch

            result_path = Path(info.path) / ".codex-worker-result.json"
            prompt = self._build_prompt(payload, result_path, extra_context)

            cmd = [
                self.config.swarm.codex_binary,
                "-a",
                self.config.swarm.approval_mode,
                "exec",
                "--json",
            ]
            if self.config.swarm.worker_model:
                cmd.extend(["-m", self.config.swarm.worker_model])
            cmd.extend(["--cd", str(info.path), prompt])

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.running_processes[wid] = process

            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            usage = TokenUsage()
            assistant_messages: list[str] = []

            async def read_stdout() -> None:
                assert process.stdout is not None
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    stdout_lines.append(decoded)

                    usage_dict = parse_usage_from_json_line(decoded)
                    if usage_dict:
                        usage.input_tokens += usage_dict["input_tokens"]
                        usage.cached_input_tokens += usage_dict["cached_input_tokens"]
                        usage.output_tokens += usage_dict["output_tokens"]

                    msg = parse_agent_message_from_json_line(decoded)
                    if msg:
                        assistant_messages.append(msg)

            async def read_stderr() -> None:
                assert process.stderr is not None
                while True:
                    line = await process.stderr.readline()
                    if not line:
                        break
                    stderr_lines.append(line.decode("utf-8", errors="replace"))

            readers = [asyncio.create_task(read_stdout()), asyncio.create_task(read_stderr())]
            timed_out = False
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=self.config.swarm.worker_timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                timed_out = True
                exit_code = -9

            await asyncio.gather(*readers)
            self.running_processes.pop(wid, None)

            if usage.total_tokens == 0:
                usage = self.budget_tracker.estimate_usage_from_text("".join(stdout_lines))

            self.budget_tracker.add_usage(usage, self.config.swarm.worker_model, worker_id=wid)

            await asyncio.to_thread(self._auto_commit_worktree, info.path, wid, payload.task)
            files_changed = await asyncio.to_thread(self._changed_files, info.branch)
            diff_text = await asyncio.to_thread(self._diff_text, info.branch)

            worker_result = self._load_worker_result(result_path)
            if not worker_result:
                fallback_summary = assistant_messages[-1] if assistant_messages else "Worker completed without result file"
                worker_result = WorkerResult(
                    status=WorkerResultStatus.PARTIAL,
                    summary=fallback_summary,
                    files_modified=files_changed,
                    tests_status="skipped",
                    warnings=["Missing or invalid worker result file"],
                    confidence=0.4,
                )

            out_of_scope = self._out_of_scope_files(files_changed, payload.scope)
            requires_approval = len(out_of_scope) > 0

            status = WorkerStatus.COMPLETED
            error_message = ""
            if timed_out:
                status = WorkerStatus.TIMED_OUT
                error_message = "Worker timed out"
                worker_result.status = WorkerResultStatus.FAILED
                worker_result.warnings.append(error_message)
            elif exit_code != 0:
                status = WorkerStatus.FAILED
                error_message = f"Worker exited with code {exit_code}"
                if worker_result.status == WorkerResultStatus.SUCCESS:
                    worker_result.status = WorkerResultStatus.PARTIAL
                worker_result.warnings.append(error_message)
            elif requires_approval:
                status = WorkerStatus.PENDING_APPROVAL
                if worker_result.status == WorkerResultStatus.SUCCESS:
                    worker_result.status = WorkerResultStatus.PARTIAL
                worker_result.warnings.append("Out-of-scope edits require supervisor approval")

            ended_at = datetime.now(timezone.utc)
            if lifecycle_callback:
                await lifecycle_callback(wid, status, payload)

            return WorkerExecutionResult(
                worker_id=wid,
                branch=info.branch,
                worktree_path=str(info.path),
                task=payload,
                status=status,
                result=worker_result,
                usage=usage,
                estimated_cost=self.budget_tracker.worker_costs.get(wid, 0.0),
                requires_approval=requires_approval,
                out_of_scope_files=out_of_scope,
                diff_text=diff_text,
                raw_stdout="".join(stdout_lines),
                raw_stderr="".join(stderr_lines),
                error=error_message,
                started_at=started_at,
                ended_at=ended_at,
            )

    def release_worktree(self, worker_id: str, worktree_path: str, branch: str, remove_branch: bool) -> None:
        from .models import WorktreeInfo

        self.worktree_manager.cleanup(
            WorktreeInfo(worker_id=worker_id, branch=branch, path=Path(worktree_path)),
            force=True,
            remove_branch=remove_branch,
        )

    def _build_prompt(self, payload: SpawnAgentPayload, result_path: Path, extra_context: str) -> str:
        scope = payload.scope or ["**/*"]
        scope_lines = "\n".join(f"- {pattern}" for pattern in scope)
        context = payload.context.strip()
        if extra_context.strip():
            context = f"{context}\n\nAdditional context:\n{extra_context}".strip()

        contract = {
            "status": "success | partial | failed | blocked",
            "summary": "2-3 sentence description of what was done",
            "files_modified": ["path/to/file"],
            "files_created": [],
            "files_deleted": [],
            "key_decisions": ["decision and rationale"],
            "warnings": ["out-of-scope warnings"],
            "tests_status": "passed | failed | skipped",
            "confidence": 0.0,
        }

        return f"""
You are a focused worker agent. Complete your task and nothing else.

Task:
{payload.task}

Allowed scope patterns:
{scope_lines}

Context:
{context or "(none)"}

Constraints:
- Only modify files matching allowed scope patterns.
- If you find important issues outside scope, report them in warnings and do not fix them.
- Run relevant tests when feasible.

Result contract:
- Write a JSON file to {result_path} with this shape:
{json.dumps(contract, indent=2)}
- Then provide a brief final message.
""".strip()

    def _load_worker_result(self, result_path: Path) -> WorkerResult | None:
        if not result_path.exists():
            return None
        try:
            data = json.loads(result_path.read_text())
            return WorkerResult.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Invalid worker result file: %s", exc)
            return None

    def _auto_commit_worktree(self, worktree_path: Path, worker_id: str, task: str) -> None:
        def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args],
                cwd=worktree_path,
                check=check,
                text=True,
                capture_output=True,
            )

        status = run(["status", "--porcelain"]).stdout.strip()
        if not status:
            return

        run(["add", "-A"])
        run(
            [
                "-c",
                "user.name=Codex Swarm",
                "-c",
                "user.email=codex-swarm@local",
                "commit",
                "-m",
                f"feat(worker): {worker_id} {task[:60]}",
            ],
            check=False,
        )

    def _changed_files(self, branch: str) -> list[str]:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD.." + branch],
            cwd=self.repo_path,
            check=False,
            text=True,
            capture_output=True,
        )
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def _diff_text(self, branch: str) -> str:
        proc = subprocess.run(
            ["git", "diff", "HEAD.." + branch],
            cwd=self.repo_path,
            check=False,
            text=True,
            capture_output=True,
        )
        return proc.stdout

    def _out_of_scope_files(self, files: list[str], scope_patterns: list[str]) -> list[str]:
        if not files or not scope_patterns:
            return []

        spec = pathspec.PathSpec.from_lines("gitignore", scope_patterns)
        return [path for path in files if not spec.match_file(path)]
