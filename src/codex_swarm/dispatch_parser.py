from __future__ import annotations

import json
import re
from typing import Any

from .logging import get_logger
from .models import (
    CheckWorkersPayload,
    DispatchRequest,
    MergeResultsPayload,
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


def _validate_dispatch(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool == "spawn_agent":
        return SpawnAgentPayload.model_validate(payload).model_dump()
    if tool == "spawn_swarm":
        return SpawnSwarmPayload.model_validate(payload).model_dump()
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
        payload = _parse_json_payload(body)
        validated_payload = _validate_dispatch(tool, payload)
        request_id = validated_payload.get("request_id") or payload.get("request_id")
        requests.append(DispatchRequest(tool=tool, payload=validated_payload, request_id=request_id))
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
