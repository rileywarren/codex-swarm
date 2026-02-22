from __future__ import annotations

import subprocess
from pathlib import Path

from .logging import get_logger
from .models import MergeOutcome

logger = get_logger(__name__)


class MergeManager:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            check=check,
            text=True,
            capture_output=True,
        )

    def merge_branch(
        self,
        worker_id: str,
        branch: str,
        task_summary: str,
        resolve_conflicts: str = "abort",
    ) -> MergeOutcome:
        message = f"chore(codex-swarm): merge {worker_id} - {task_summary[:72]}"
        merge_args = ["merge", "--no-ff", "-m", message]
        if resolve_conflicts == "ours":
            merge_args.extend(["-X", "ours"])
        if resolve_conflicts == "theirs":
            merge_args.extend(["-X", "theirs"])
        merge_args.append(branch)

        proc = self._run_git(merge_args, check=False)
        if proc.returncode == 0:
            logger.info("Merged branch %s for worker %s", branch, worker_id)
            return MergeOutcome(worker_id=worker_id, branch=branch, merged=True, message=proc.stdout.strip())

        self._run_git(["merge", "--abort"], check=False)
        logger.warning("Merge conflict on branch %s for worker %s", branch, worker_id)
        details = (proc.stderr or proc.stdout or "merge conflict").strip()
        return MergeOutcome(worker_id=worker_id, branch=branch, merged=False, conflict=True, message=details)
