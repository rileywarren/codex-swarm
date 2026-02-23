from __future__ import annotations

import json
import re
from typing import Any

from .logging import get_logger
from .models import (
    CheckWorkersPayload,
    DispatchRequest,
    MergeResultsPayload,
    ReturnFormat,
    Strategy,
    SpawnAgentPayload,
    SpawnSwarmPayload,
)

logger = get_logger(__name__)

DISPATCH_BLOCK_RE = re.compile(
    r"```(?P<tool>spawn_agent|spawn_swarm|check_workers|merge_results)\s*\n(?P<body>.*?)```",
    re.DOTALL,
)


def _repair_json(raw: str) -> str:
    repaired = raw.strip()
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    if "'" in repaired and '"' not in repaired:
        repaired = repaired.replace("'", '"')
    return repaired


def _parse_json_payload(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        repaired = _repair_json(raw)
        parsed = json.loads(repaired)
        logger.warning("Used fuzzy JSON repair for dispatch payload")

    if not isinstance(parsed, dict):
        raise ValueError("Dispatch payload must be a JSON object")
    return parsed


def _coerce_scope(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _coerce_priority(value: Any) -> str:
    if not isinstance(value, str):
        return "normal"
    lowered = value.strip().lower()
    if lowered in {"high", "normal", "low"}:
        return lowered
    return "normal"


def _coerce_return_format(value: Any) -> str:
    if not isinstance(value, str):
        return ReturnFormat.SUMMARY.value
    lowered = value.strip().lower()
    if lowered in {fmt.value for fmt in ReturnFormat}:
        return lowered
    if lowered in {"summary+test-results", "summary_and_tests"}:
        return ReturnFormat.SUMMARY.value
    if "diff" in lowered:
        return ReturnFormat.DIFF.value
    return ReturnFormat.SUMMARY.value


def _normalize_spawn_agent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    task_value = payload.get("task") or payload.get("objective") or payload.get("description")
    if not isinstance(task_value, str) or not task_value.strip():
        raise ValueError("spawn_agent payload requires task/objective")

    scope_value = payload.get("scope")
    if scope_value is None:
        scope_value = payload.get("files")
    if scope_value is None:
        scope_value = payload.get("paths")

    context_value = payload.get("context")
    if context_value is None:
        context_value = payload.get("notes")
    if context_value is None:
        context_value = payload.get("constraints")

    normalized = {
        "task": task_value.strip(),
        "scope": _coerce_scope(scope_value),
        "context": str(context_value or ""),
        "priority": _coerce_priority(payload.get("priority")),
        "return_format": _coerce_return_format(payload.get("return_format")),
    }
    return SpawnAgentPayload.model_validate(normalized).model_dump()


def _coerce_strategy(value: Any) -> str:
    if not isinstance(value, str):
        return Strategy.FAN_OUT.value
    candidate = value.strip().lower().replace("_", "-").replace(" ", "-")
    if candidate in {item.value for item in Strategy}:
        return candidate
    return Strategy.FAN_OUT.value


def _normalize_spawn_swarm_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized_tasks: list[dict[str, Any]] = []

    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        for task_payload in tasks:
            if isinstance(task_payload, dict):
                normalized_tasks.append(_normalize_spawn_agent_payload(task_payload))

    workers = payload.get("workers")
    if not normalized_tasks and isinstance(workers, list):
        for worker_payload in workers:
            if isinstance(worker_payload, dict):
                normalized_tasks.append(_normalize_spawn_agent_payload(worker_payload))

    if not normalized_tasks and any(key in payload for key in ("task", "objective", "description")):
        normalized_tasks.append(_normalize_spawn_agent_payload(payload))

    if not normalized_tasks:
        raise ValueError("spawn_swarm payload requires tasks/workers or task/objective")

    normalized = {
        "tasks": normalized_tasks,
        "strategy": _coerce_strategy(payload.get("strategy")),
        "wait": bool(payload.get("wait", True)),
    }
    return SpawnSwarmPayload.model_validate(normalized).model_dump()


def _validate_dispatch(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool == "spawn_agent":
        return _normalize_spawn_agent_payload(payload)
    if tool == "spawn_swarm":
        return _normalize_spawn_swarm_payload(payload)
    if tool == "check_workers":
        return CheckWorkersPayload.model_validate(payload).model_dump()
    if tool == "merge_results":
        return MergeResultsPayload.model_validate(payload).model_dump()
    raise ValueError(f"Unknown dispatch tool: {tool}")


def parse_dispatch_blocks(text: str) -> list[DispatchRequest]:
    requests: list[DispatchRequest] = []
    for match in DISPATCH_BLOCK_RE.finditer(text):
        tool = match.group("tool")
        body = match.group("body")
        try:
            payload = _parse_json_payload(body)
            validated_payload = _validate_dispatch(tool, payload)
            request_id = validated_payload.get("request_id") or payload.get("request_id")
            requests.append(DispatchRequest(tool=tool, payload=validated_payload, request_id=request_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping invalid dispatch block for %s: %s", tool, exc)
    return requests


def parse_agent_message_from_json_line(line: str) -> str | None:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    if event.get("type") != "item.completed":
        return None

    item = event.get("item", {})
    if item.get("type") != "agent_message":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


def parse_usage_from_json_line(line: str) -> dict[str, int] | None:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    if event.get("type") != "turn.completed":
        return None

    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None

    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "cached_input_tokens": int(usage.get("cached_input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
    }
