from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, OptionList, Static

from .events import DashboardState

ActionCallback = Callable[[str, dict], Awaitable[None]]
ModelDefaultCallback = Callable[[str], Awaitable[str]]


class ModelPickerScreen(ModalScreen[str | None]):
    CSS = """
    ModelPickerScreen {
        align: center middle;
    }

    #model-dialog {
        width: 72;
        height: 22;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #model-options {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "confirm", "Set Default"),
    ]

    def __init__(self, models: list[str], current_model: str | None):
        super().__init__()
        self.models = models
        self.current_model = current_model

    def compose(self) -> ComposeResult:
        with Container(id="model-dialog"):
            yield Static("Select default model (applies to supervisor and workers)")
            yield Static("Enter to save default, Esc to cancel")
            yield OptionList(*self.models, id="model-options")

    def on_mount(self) -> None:
        options = self.query_one("#model-options", OptionList)
        if self.current_model and self.current_model in self.models:
            options.highlighted = self.models.index(self.current_model)
        elif self.models:
            options.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.models[event.index])

    def action_confirm(self) -> None:
        options = self.query_one("#model-options", OptionList)
        idx = options.highlighted
        if idx is None:
            self.dismiss(None)
            return
        self.dismiss(self.models[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)


class SwarmTUIApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #top {
        height: 5;
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
        ("d", "pick_default_model", "Set Default Model"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        event_queue: "asyncio.Queue",
        action_handler: ActionCallback,
        budget_cap: float,
        available_models: list[str],
        current_model: str | None,
        model_default_handler: ModelDefaultCallback,
    ):
        super().__init__()
        self.event_queue = event_queue
        self.action_handler = action_handler
        self.state = DashboardState(budget_cap=budget_cap)
        self.queue_paused = False
        self.available_models = available_models
        self.current_model = current_model
        self.model_default_handler = model_default_handler
        self.last_model_message = "Press d to choose"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="top"):
            yield Static("Supervisor: idle", id="supervisor")
            yield Static("Budget: $0.00", id="budget")
            yield Static("Default Model: account-default | Press d to choose", id="model")

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
        model = self.query_one("#model", Static)
        workers = self.query_one("#workers", DataTable)
        logs = self.query_one("#logs", Static)

        supervisor.update(f"Supervisor: {self.state.supervisor_status} | {self.state.supervisor_line}")
        budget.update(
            f"Budget: ${self.state.budget_cost:.2f} / ${self.state.budget_cap:.2f} | Tokens: {self.state.total_tokens}"
        )
        display_model = self.current_model or "account-default"
        model.update(f"Default Model: {display_model} | {self.last_model_message}")

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

    async def action_pick_default_model(self) -> None:
        if not self.available_models:
            self.last_model_message = "No model list available"
            return

        selected = await self.push_screen_wait(ModelPickerScreen(self.available_models, self.current_model))
        if not selected:
            self.last_model_message = "Model selection canceled"
            return

        saved_path = await self.model_default_handler(selected)
        self.current_model = selected
        self.last_model_message = f"Saved default to {saved_path}"
        self.state.logs.append(f"Default model updated: {selected}")
