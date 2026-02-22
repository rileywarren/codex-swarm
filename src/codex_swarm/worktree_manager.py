from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .logging import get_logger
from .models import WorktreeConfig, WorktreeInfo

logger = get_logger(__name__)


class WorktreeManager:
    def __init__(self, repo_path: Path, config: WorktreeConfig):
        self.repo_path = repo_path
        self.config = config
        self.base_dir = Path(config.base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _run_git(self, args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = ["git", *args]
        return subprocess.run(
            cmd,
            cwd=cwd or self.repo_path,
            check=check,
            text=True,
            capture_output=True,
        )

    def create(self, worker_id: str) -> WorktreeInfo:
        branch = f"codex-swarm/worker-{worker_id}"
        path = self.base_dir / f"worker-{worker_id}"

        if path.exists():
            self.cleanup(WorktreeInfo(worker_id=worker_id, branch=branch, path=path), force=True)

        self._run_git(["worktree", "add", "-b", branch, str(path), "HEAD"])
        logger.info("Created worktree for %s at %s", worker_id, path)
        return WorktreeInfo(worker_id=worker_id, branch=branch, path=path)

    def cleanup(self, info: WorktreeInfo, force: bool = False, remove_branch: bool = True) -> None:
        remove_args = ["worktree", "remove"]
        if force:
            remove_args.append("--force")
        remove_args.append(str(info.path))

        try:
            self._run_git(remove_args, check=False)
        finally:
            if info.path.exists():
                shutil.rmtree(info.path, ignore_errors=True)

        if remove_branch:
            self._run_git(["branch", "-D", info.branch], check=False)
        logger.info("Cleaned worktree for %s", info.worker_id)

    def cleanup_stale(self) -> None:
        prefix = "codex-swarm/worker-"
        for path in self.base_dir.glob("worker-*"):
            branch = f"{prefix}{path.name.split('worker-', 1)[-1]}"
            info = WorktreeInfo(worker_id=path.name.replace("worker-", ""), branch=branch, path=path)
            self.cleanup(info, force=True)

    def list_worktrees(self) -> list[Path]:
        proc = self._run_git(["worktree", "list", "--porcelain"])
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        worktrees: list[Path] = []
        for line in lines:
            if line.startswith("worktree "):
                worktrees.append(Path(line.split(" ", 1)[1]))
        return worktrees
