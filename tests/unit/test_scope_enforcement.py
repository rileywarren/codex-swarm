from __future__ import annotations

from codex_swarm.budget_tracker import BudgetTracker
from codex_swarm.config import load_config
from codex_swarm.worker_manager import WorkerManager
from codex_swarm.worktree_manager import WorktreeManager


def test_scope_checker_detects_out_of_scope(git_repo) -> None:
    config = load_config()
    wt = WorktreeManager(git_repo, config.worktree)
    budget = BudgetTracker(config.budget)
    manager = WorkerManager(git_repo, config, wt, budget)

    out = manager._out_of_scope_files(["src/a.py", "docs/notes.md"], ["src/**"])
    assert out == ["docs/notes.md"]


def test_scope_checker_allows_when_empty_scope(git_repo) -> None:
    config = load_config()
    wt = WorktreeManager(git_repo, config.worktree)
    budget = BudgetTracker(config.budget)
    manager = WorkerManager(git_repo, config, wt, budget)

    out = manager._out_of_scope_files(["a.txt"], [])
    assert out == []
