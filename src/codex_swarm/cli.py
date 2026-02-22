from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import click
import yaml

from .config import load_config
from .logging import configure_logging
from .models import DispatchRequest, SpawnAgentPayload, Strategy
from .orchestrator import Orchestrator
from .patch_guide import generate_patch_guide
from .tui.app import SwarmTUIApp


class RuntimeContext:
    def __init__(
        self,
        repo: Path,
        config_path: Path | None,
        workers: int | None,
        model: str | None,
        worker_model: str | None,
        no_tui: bool,
    ):
        overrides: dict[str, Any] = {}
        if workers is not None:
            overrides["swarm.max_workers"] = workers
        if model:
            overrides["swarm.supervisor_model"] = model
        if worker_model:
            overrides["swarm.worker_model"] = worker_model
        if no_tui:
            overrides["tui.enabled"] = False

        self.repo = repo.resolve()
        self.config = load_config(config_path=config_path, cli_overrides=overrides)


@click.group()
@click.option("--repo", type=click.Path(path_type=Path, file_okay=False, exists=True), default=Path.cwd())
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False), default=None)
@click.option("--workers", type=int, default=None)
@click.option("--model", type=str, default=None)
@click.option("--worker-model", type=str, default=None)
@click.option("--no-tui", is_flag=True, default=False)
@click.option("--verbose", is_flag=True, default=False)
@click.pass_context
def main(
    ctx: click.Context,
    repo: Path,
    config_path: Path | None,
    workers: int | None,
    model: str | None,
    worker_model: str | None,
    no_tui: bool,
    verbose: bool,
) -> None:
    """Codex Swarm CLI."""
    configure_logging(verbose=verbose)
    ctx.obj = RuntimeContext(repo, config_path, workers, model, worker_model, no_tui)


@main.command()
@click.argument("task", nargs=1)
@click.pass_obj
def run(runtime: RuntimeContext, task: str) -> None:
    """Run in full supervisor mode."""

    async def _run() -> None:
        orchestrator = Orchestrator(runtime.repo, runtime.config)
        await orchestrator.start()
        try:
            if runtime.config.tui.enabled:
                await _run_with_tui(orchestrator, task)
            else:
                await orchestrator.run_supervisor(task)
        finally:
            await orchestrator.stop()

    asyncio.run(_run())


@main.command("fan-out")
@click.option("--tasks", "tasks_path", required=True, type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.pass_obj
def fan_out(runtime: RuntimeContext, tasks_path: Path) -> None:
    """Run tasks in fan-out headless mode."""

    async def _run() -> None:
        orchestrator = Orchestrator(runtime.repo, runtime.config)
        await orchestrator.start()
        try:
            tasks = _load_tasks_json(tasks_path)
            results = await orchestrator.run_strategy(tasks, Strategy.FAN_OUT)
            click.echo(_format_results(results))
        finally:
            await orchestrator.stop()

    asyncio.run(_run())


@main.command()
@click.option("--steps", "steps_path", required=True, type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.pass_obj
def pipeline(runtime: RuntimeContext, steps_path: Path) -> None:
    """Run tasks in pipeline headless mode."""

    async def _run() -> None:
        orchestrator = Orchestrator(runtime.repo, runtime.config)
        await orchestrator.start()
        try:
            tasks = _load_pipeline_yaml(steps_path)
            results = await orchestrator.run_strategy(tasks, Strategy.PIPELINE)
            click.echo(_format_results(results))
        finally:
            await orchestrator.stop()

    asyncio.run(_run())


@main.command()
@click.option("--tasks", "tasks_path", required=True, type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.option(
    "--strategy",
    "strategy_name",
    type=click.Choice([s.value for s in Strategy]),
    default=Strategy.FAN_OUT.value,
)
@click.pass_obj
def swarm(runtime: RuntimeContext, tasks_path: Path, strategy_name: str) -> None:
    """Run headless swarm with explicit strategy."""

    async def _run() -> None:
        orchestrator = Orchestrator(runtime.repo, runtime.config)
        await orchestrator.start()
        try:
            tasks = _load_tasks_json(tasks_path)
            strategy = Strategy(strategy_name)
            results = await orchestrator.run_strategy(tasks, strategy)
            click.echo(_format_results(results))
        finally:
            await orchestrator.stop()

    asyncio.run(_run())


@main.command()
@click.option("--output", "output_path", type=click.Path(path_type=Path, dir_okay=False), default=None)
def patch(output_path: Path | None) -> None:
    """Generate native Codex patch guide."""
    guide = generate_patch_guide()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(guide)
        click.echo(f"Patch guide written to {output_path}")
    else:
        click.echo(guide)


def _load_tasks_json(path: Path) -> list[SpawnAgentPayload]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise click.ClickException("tasks JSON must be an array")
    return [SpawnAgentPayload.model_validate(item) for item in data]


def _load_pipeline_yaml(path: Path) -> list[SpawnAgentPayload]:
    parsed = yaml.safe_load(path.read_text()) or {}
    steps = parsed.get("steps") if isinstance(parsed, dict) else parsed
    if not isinstance(steps, list):
        raise click.ClickException("pipeline YAML must contain a list of steps")
    return [SpawnAgentPayload.model_validate(item) for item in steps]


def _format_results(results: list[Any]) -> str:
    rows = []
    for result in results:
        rows.append(f"- {result.worker_id}: {result.status.value} :: {result.result.summary}")
    return "\n".join(rows)


async def _run_with_tui(orchestrator: Orchestrator, task: str) -> None:
    queue = orchestrator.subscribe()

    async def action_handler(action: str, payload: dict) -> None:
        if action == "merge_results":
            req = DispatchRequest(tool="merge_results", payload=payload)
            await orchestrator.handle_dispatch(req)
        elif action == "cancel_worker":
            worker_id = payload.get("worker_id", "")
            await orchestrator.worker_manager.cancel_worker(worker_id)
        elif action == "pause_queue":
            orchestrator.strategy_engine.pause_queue()
        elif action == "resume_queue":
            orchestrator.strategy_engine.resume_queue()
        elif action == "kill_supervisor":
            await orchestrator.kill_supervisor()

    app = SwarmTUIApp(queue, action_handler=action_handler, budget_cap=orchestrator.config.budget.max_total_cost)

    supervisor_task = asyncio.create_task(orchestrator.run_supervisor(task))
    tui_task = asyncio.create_task(app.run_async())

    done, pending = await asyncio.wait({supervisor_task, tui_task}, return_when=asyncio.FIRST_COMPLETED)

    if supervisor_task in done:
        app.exit()
    if tui_task in done and not supervisor_task.done():
        await orchestrator.kill_supervisor()
        supervisor_task.cancel()

    for task_obj in pending:
        task_obj.cancel()
        try:
            await task_obj
        except BaseException:
            pass
