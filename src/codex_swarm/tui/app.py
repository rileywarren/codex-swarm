from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import DataTable, Footer, Header, Static

from .events import DashboardState

ActionCallback = Callable[[str, dict], Awaitable[None]]


class SwarmTUIApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #top {
        height: 3;
    }

    #workers {
        height: 14;
    }

    #logs {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("c", "cancel_worker", "Cancel Worker"),
        ("m", "force_merge", "Force Merge"),
        ("k", "kill_supervisor", "Kill Supervisor"),
        ("p", "toggle_queue", "Pause/Resume Queue"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        event_queue: "asyncio.Queue",
        action_handler: ActionCallback,
        budget_cap: float,
    ):
        super().__init__()
        self.event_queue = event_queue
        self.action_handler = action_handler
        self.state = DashboardState(budget_cap=budget_cap)
        self.queue_paused = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="top"):
            yield Static("Supervisor: idle", id="supervisor")
            yield Static("Budget: $0.00", id="budget")

        with Horizontal():
            table = DataTable(id="workers")
            table.add_columns("Worker", "Status", "Task", "Elapsed")
            yield table

        yield Static("", id="logs")
        yield Footer()

    async def on_mount(self) -> None:
        self.set_interval(0.2, self._refresh_ui)
        self.run_worker(self._consume_events(), exclusive=True)

    async def _consume_events(self) -> None:
        while True:
            event = await self.event_queue.get()
            self.state.apply(event.event_type, event.payload)

    def _refresh_ui(self) -> None:
        supervisor = self.query_one("#supervisor", Static)
        budget = self.query_one("#budget", Static)
        workers = self.query_one("#workers", DataTable)
        logs = self.query_one("#logs", Static)

        supervisor.update(f"Supervisor: {self.state.supervisor_status} | {self.state.supervisor_line}")
        budget.update(
            f"Budget: ${self.state.budget_cost:.2f} / ${self.state.budget_cap:.2f} | Tokens: {self.state.total_tokens}"
        )

        workers.clear()
        for row in self.state.workers.values():
            workers.add_row(row.worker_id, row.status, row.task, row.elapsed)

        logs.update("\n".join(self.state.logs[-20:]))

    async def action_cancel_worker(self) -> None:
        workers = list(self.state.workers.keys())
        if not workers:
            return
        await self.action_handler("cancel_worker", {"worker_id": workers[-1]})

    async def action_force_merge(self) -> None:
        workers = list(self.state.workers.keys())
        if not workers:
            return
        await self.action_handler("merge_results", {"worker_ids": [workers[-1]]})

    async def action_kill_supervisor(self) -> None:
        await self.action_handler("kill_supervisor", {})

    async def action_toggle_queue(self) -> None:
        self.queue_paused = not self.queue_paused
        action = "pause_queue" if self.queue_paused else "resume_queue"
        await self.action_handler(action, {})
