from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

from .logging import get_logger
from .models import IPCMessage

logger = get_logger(__name__)

Handler = Callable[[IPCMessage], Awaitable[dict | IPCMessage | None]]


class IPCServer:
    def __init__(self, socket_path: Path, terminator: str):
        self.socket_path = socket_path
        self.terminator = terminator.encode("utf-8")
        self._server: asyncio.AbstractServer | None = None
        self._handler: Handler | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self, handler: Handler) -> None:
        self._handler = handler
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self.socket_path))
        logger.info("IPC server listening on %s", self.socket_path)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for writer in list(self._clients):
            writer.close()
            await writer.wait_closed()

        if self.socket_path.exists():
            self.socket_path.unlink()

    async def broadcast(self, message: IPCMessage) -> None:
        payload = message.model_dump_json() + self.terminator.decode("utf-8")
        data = payload.encode("utf-8")
        stale: list[asyncio.StreamWriter] = []
        for writer in self._clients:
            try:
                writer.write(data)
                await writer.drain()
            except ConnectionError:
                stale.append(writer)

        for writer in stale:
            self._clients.discard(writer)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            while True:
                raw = await reader.readuntil(self.terminator)
                chunk = raw[: -len(self.terminator)].decode("utf-8").strip()
                if not chunk:
                    continue

                try:
                    msg = IPCMessage.model_validate(json.loads(chunk))
                except Exception as exc:  # noqa: BLE001
                    await self._send_error(writer, f"invalid message: {exc}")
                    continue

                if self._handler is None:
                    await self._send_error(writer, "no handler")
                    continue

                response = await self._handler(msg)
                if response is None:
                    continue

                if isinstance(response, IPCMessage):
                    body = response.model_dump_json()
                else:
                    body = json.dumps(response)

                writer.write(body.encode("utf-8") + self.terminator)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def _send_error(self, writer: asyncio.StreamWriter, message: str) -> None:
        body = json.dumps({"type": "error", "payload": {"message": message}})
        writer.write(body.encode("utf-8") + self.terminator)
        await writer.drain()
