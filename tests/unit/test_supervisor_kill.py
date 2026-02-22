from __future__ import annotations

import pytest

from codex_swarm.config import load_config
from codex_swarm.supervisor_manager import SupervisorManager


class FakeProcess:
    def __init__(self) -> None:
        self.killed = False
        self.returncode = None

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = -9
        return self.returncode


@pytest.mark.asyncio
async def test_supervisor_kill_active_process(git_repo) -> None:
    manager = SupervisorManager(git_repo, load_config())
    fake = FakeProcess()
    manager._active_process = fake

    killed = await manager.kill()
    assert killed is True
    assert fake.killed is True
    assert manager._active_process is None

    killed_again = await manager.kill()
    assert killed_again is False
