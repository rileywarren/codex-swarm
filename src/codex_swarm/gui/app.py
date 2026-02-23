from __future__ import annotations

import asyncio
import sys

import click

from .history_store import SwarmHistoryStore


class SwarmGUIApp:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def run(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
            from qasync import QEventLoop
            from .main_window import SwarmMainWindow
        except ModuleNotFoundError as exc:
            raise click.ClickException(
                "PySide6 and qasync are required to run `codex-swarm gui`."
            ) from exc

        app = QApplication(sys.argv)
        loop = QEventLoop(app)
        asyncio.set_event_loop(loop)

        history_store = SwarmHistoryStore(
            self.runtime.config.gui.history_db_path,
            max_runs=int(self.runtime.config.gui.history_max_runs),
        )
        self.runtime.history_store = history_store

        window = SwarmMainWindow(self.runtime)
        window.show()
        try:
            loop.run_forever()
        finally:
            history_store.close()


def run_gui(runtime) -> None:
    SwarmGUIApp(runtime).run()
