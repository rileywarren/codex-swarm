from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from .dispatch_parser import parse_agent_message_from_json_line, parse_dispatch_blocks, parse_usage_from_json_line
from .logging import get_logger
from .models import AppConfig, DispatchRequest, SupervisorRunResult, TokenUsage

logger = get_logger(__name__)

DispatchHandler = Callable[[DispatchRequest], Awaitable[None]]
LogHandler = Callable[[str, str], Awaitable[None]]
UsageHandler = Callable[[TokenUsage], Awaitable[None]]


class SupervisorManager:
    def __init__(self, repo_path: Path, config: AppConfig):
        self.repo_path = repo_path
        self.config = config
        self._active_process: asyncio.subprocess.Process | None = None

    async def run(
        self,
        prompt: str,
        dispatch_handler: DispatchHandler,
        usage_handler: UsageHandler | None = None,
        log_handler: LogHandler | None = None,
    ) -> SupervisorRunResult:
        cmd = [
            self.config.swarm.codex_binary,
            "-a",
            self.config.swarm.approval_mode,
            "exec",
            "--json",
        ]
        if self.config.swarm.supervisor_model:
            cmd.extend(["-m", self.config.swarm.supervisor_model])
        cmd.extend(["--cd", str(self.repo_path), prompt])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active_process = process

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        usage = TokenUsage()

        async def read_stdout() -> None:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                stdout_lines.append(decoded)
                if log_handler:
                    await log_handler("supervisor_stdout", decoded.rstrip())

                usage_dict = parse_usage_from_json_line(decoded)
                if usage_dict:
                    usage.input_tokens += usage_dict["input_tokens"]
                    usage.cached_input_tokens += usage_dict["cached_input_tokens"]
                    usage.output_tokens += usage_dict["output_tokens"]
                    if usage_handler:
                        await usage_handler(usage)

                message = parse_agent_message_from_json_line(decoded)
                if not message:
                    continue

                try:
                    requests = parse_dispatch_blocks(message)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to parse dispatch blocks: %s", exc)
                    continue

                for req in requests:
                    try:
                        await dispatch_handler(req)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Dispatch handler failed for %s: %s", req.tool, exc)

        async def read_stderr() -> None:
            assert process.stderr is not None
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                stderr_lines.append(decoded)
                if log_handler:
                    await log_handler("supervisor_stderr", decoded.rstrip())

        readers = [asyncio.create_task(read_stdout()), asyncio.create_task(read_stderr())]
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=self.config.swarm.supervisor_timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            exit_code = -9
            stderr_lines.append("Supervisor timed out")

        await asyncio.gather(*readers)
        self._active_process = None

        logger.info("Supervisor exited with code %s", exit_code)
        return SupervisorRunResult(
            exit_code=exit_code,
            usage=usage,
            raw_stdout="".join(stdout_lines),
            raw_stderr="".join(stderr_lines),
        )

    async def kill(self) -> bool:
        process = self._active_process
        if process is None:
            return False

        if process.returncode is not None:
            self._active_process = None
            return False

        process.kill()
        await process.wait()
        self._active_process = None
        logger.warning("Supervisor killed by operator")
        return True
