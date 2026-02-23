from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strategy(str, Enum):
    FAN_OUT = "fan-out"
    PIPELINE = "pipeline"
    MAP_REDUCE = "map-reduce"
    DEBATE = "debate"


class Priority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ReturnFormat(str, Enum):
    SUMMARY = "summary"
    DIFF = "diff"
    FULL = "full"


class WorkerStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMED_OUT = "timed_out"
    PENDING_APPROVAL = "pending_approval"


class WorkerResultStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class SwarmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_workers: int = 4
    supervisor_model: str | None = None
    worker_model: str | None = None
    worker_timeout: int = 300
    supervisor_timeout: int = 600
    approval_mode: str = "on-request"
    codex_binary: str = "codex"
    pipeline_continue_on_error: bool = False


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_total_cost: float = 5.0
    max_worker_cost: float = 1.5
    max_total_tokens: int = 200_000
    warn_at_percent: int = 80


class WorktreeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_dir: str = "/tmp/codex-swarm"
    cleanup: bool = True
    auto_merge: bool = True
    merge_strategy: str = "no-ff"


class ResultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_summary_tokens: int = 500
    include_diff: bool = False
    max_diff_lines: int = 200
    response_file: str = ".codex-swarm-response.md"


class IPCConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["unix_socket", "file_watch"] = "unix_socket"
    socket_path: str = "/tmp/codex-swarm.sock"
    message_terminator: str = "\n---MSG_END---\n"


class TUIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    refresh_rate: float = 0.5
    show_worker_output: bool = False
    interactive_controls: bool = True


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    swarm: SwarmConfig = Field(default_factory=SwarmConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    results: ResultsConfig = Field(default_factory=ResultsConfig)
    ipc: IPCConfig = Field(default_factory=IPCConfig)
    tui: TUIConfig = Field(default_factory=TUIConfig)


class SpawnAgentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    scope: list[str] = Field(default_factory=list)
    context: str = ""
    priority: Priority = Priority.NORMAL
    return_format: ReturnFormat = ReturnFormat.SUMMARY


class SpawnSwarmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[SpawnAgentPayload]
    strategy: Strategy = Strategy.FAN_OUT
    wait: bool = True


class CheckWorkersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_ids: list[str] = Field(default_factory=list)


class MergeResultsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_ids: list[str] = Field(default_factory=list)
    resolve_conflicts: Literal["abort", "ours", "theirs"] = "abort"


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal["spawn_agent", "spawn_swarm", "check_workers", "merge_results"]
    payload: dict[str, Any]
    request_id: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WorkerResultStatus = WorkerResultStatus.SUCCESS
    summary: str = ""
    files_modified: list[str] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    files_deleted: list[str] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tests_status: Literal["passed", "failed", "skipped"] = "skipped"
    confidence: float = 0.5

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value


class WorkerExecutionResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    worker_id: str
    branch: str
    worktree_path: str
    task: SpawnAgentPayload
    status: WorkerStatus
    result: WorkerResult
    usage: TokenUsage = Field(default_factory=TokenUsage)
    estimated_cost: float = 0.0
    requires_approval: bool = False
    out_of_scope_files: list[str] = Field(default_factory=list)
    diff_text: str = ""
    raw_stdout: str = ""
    raw_stderr: str = ""
    error: str = ""
    started_at: datetime
    ended_at: datetime


class MergeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    branch: str
    merged: bool
    conflict: bool = False
    message: str = ""


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    warned: bool = False


class IPCMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    id: str
    timestamp: str
    reply_to: str | None = None


class SupervisorRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_code: int
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw_stdout: str = ""
    raw_stderr: str = ""


@dataclass(slots=True)
class WorktreeInfo:
    worker_id: str
    branch: str
    path: Path


@dataclass(slots=True)
class RuntimeEvent:
    event_type: str
    payload: dict[str, Any]
    timestamp: datetime

    @staticmethod
    def now(event_type: str, payload: dict[str, Any]) -> "RuntimeEvent":
        return RuntimeEvent(event_type=event_type, payload=payload, timestamp=datetime.now(timezone.utc))
