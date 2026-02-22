from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from codex_swarm.ipc_server import IPCServer
from codex_swarm.models import IPCMessage


@pytest.mark.asyncio
async def test_ipc_server_roundtrip(tmp_path) -> None:
    socket_path = Path("/tmp") / f"codex-swarm-test-{uuid.uuid4().hex[:8]}.sock"
    terminator = "\n---MSG_END---\n"

    async def handler(msg: IPCMessage):
        return {
            "type": "response",
            "id": str(uuid.uuid4()),
            "reply_to": msg.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {"ok": True},
        }

    server = IPCServer(socket_path, terminator)
    await server.start(handler)

    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    request = {
        "type": "check_workers",
        "payload": {},
        "id": "abc",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reply_to": None,
    }
    writer.write((json.dumps(request) + terminator).encode("utf-8"))
    await writer.drain()

    raw = await reader.readuntil(terminator.encode("utf-8"))
    body = raw[: -len(terminator)].decode("utf-8")
    parsed = json.loads(body)

    assert parsed["type"] == "response"
    assert parsed["reply_to"] == "abc"
    assert parsed["payload"]["ok"] is True

    writer.close()
    await writer.wait_closed()
    await server.stop()
