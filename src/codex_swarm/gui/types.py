from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from codex_swarm.models import SpawnAgentPayload, Strategy


class SessionMode(str, Enum):
    SUPERVISOR = "supervisor"
    STRATEGY = "strategy"


@dataclass(slots=True)
class RunRequest:
    mode: SessionMode
    task_text: str = ""
    strategy: Strategy = Strategy.FAN_OUT
    tasks: list[SpawnAgentPayload] = field(default_factory=list)
    strategy_payload: dict[str, Any] | None = None


@dataclass(slots=True)
class SessionMetadata:
    run_id: str
    mode: SessionMode
    strategy: str | None
    status: str
    started_at: str
    ended_at: str | None = None
    total_cost: float = 0.0
    total_tokens: int = 0
    error_message: str | None = None
