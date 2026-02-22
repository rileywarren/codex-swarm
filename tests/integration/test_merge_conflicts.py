from __future__ import annotations

import subprocess

from codex_swarm.merge_manager import MergeManager


def test_merge_conflict_abort(git_repo) -> None:
    target = git_repo / "conflict.txt"
    target.write_text("base\n")
    subprocess.run(["git", "add", "conflict.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add conflict file"], cwd=git_repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "codex-swarm/worker-w1"], cwd=git_repo, check=True, capture_output=True)
    target.write_text("worker change\n")
    subprocess.run(["git", "add", "conflict.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "worker change"], cwd=git_repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)
    target.write_text("main change\n")
    subprocess.run(["git", "add", "conflict.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main change"], cwd=git_repo, check=True, capture_output=True)

    merge_manager = MergeManager(git_repo)
    outcome = merge_manager.merge_branch("w1", "codex-swarm/worker-w1", "task")

    assert outcome.merged is False
    assert outcome.conflict is True

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert status == ""
