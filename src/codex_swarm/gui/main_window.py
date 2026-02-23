from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from codex_swarm.models import Strategy

from .history_store import SwarmHistoryStore
from .session_controller import SessionController


class SessionPanel(QWidget):
    def __init__(self, controller: SessionController) -> None:
        super().__init__()
        self.controller = controller
        self._build_ui()
        self.controller.state_updated.connect(self.refresh)
        self.controller.run_finished.connect(self._on_run_finished)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Supervisor", "Strategy"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_change)
        mode_row.addWidget(self.mode_combo)

        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self._start)
        mode_row.addWidget(self.start_button)
        layout.addLayout(mode_row)

        self.task_editor = QPlainTextEdit()
        self.task_editor.setPlaceholderText("Enter a supervisor task")
        layout.addWidget(self.task_editor)

        strategy_row = QHBoxLayout()
        strategy_row.addWidget(QLabel("Strategy"))
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems([value.value for value in Strategy])
        self.strategy_combo.setEnabled(False)
        strategy_row.addWidget(self.strategy_combo)

        self.import_button = QPushButton("Import")
        self.import_button.clicked.connect(self._import_file)
        strategy_row.addWidget(self.import_button)
        layout.addLayout(strategy_row)

        self.budget_label = QLabel("Budget: $0.00 / $0.00")
        self.model_label = QLabel("Default Model: account-default")
        layout.addWidget(self.budget_label)
        layout.addWidget(self.model_label)

        controls = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel Worker")
        self.cancel_button.clicked.connect(self._cancel_workers)
        self.merge_button = QPushButton("Merge Selected")
        self.merge_button.clicked.connect(self._merge_selected)
        self.pause_button = QPushButton("Pause Queue")
        self.pause_button.clicked.connect(self._toggle_queue)
        self.kill_button = QPushButton("Kill Supervisor")
        self.kill_button.clicked.connect(self._kill_supervisor)
        self.model_button = QPushButton("Set Model")
        self.model_button.clicked.connect(self._set_model)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.merge_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.kill_button)
        controls.addWidget(self.model_button)
        layout.addLayout(controls)

        self.workers_table = QTableWidget(0, 4)
        self.workers_table.setHorizontalHeaderLabels(["Worker", "Status", "Task", "Elapsed"])
        self.workers_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.workers_table)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        self.refresh()

    def _on_mode_change(self, value: str) -> None:
        self.strategy_combo.setEnabled(value == "Strategy")
        self.task_editor.setPlaceholderText(
            "Enter JSON/YAML task list" if value == "Strategy" else "Enter a supervisor task"
        )

    def refresh(self) -> None:
        self.budget_label.setText(
            f"Budget: ${self.controller.state.budget_cost:.2f} / ${self.controller.state.budget_cap:.2f} | "
            f"Tokens: {self.controller.state.total_tokens}"
        )
        if self.controller.current_model:
            self.model_label.setText(f"Default Model: {self.controller.current_model}")

        self.workers_table.setRowCount(0)
        for row, worker in enumerate(self.controller.state.workers.values()):
            self.workers_table.insertRow(row)
            self.workers_table.setItem(row, 0, QTableWidgetItem(worker.worker_id))
            self.workers_table.setItem(row, 1, QTableWidgetItem(worker.status))
            self.workers_table.setItem(row, 2, QTableWidgetItem(worker.task))
            self.workers_table.setItem(row, 3, QTableWidgetItem(worker.elapsed))

        self.log_view.setPlainText("\n".join(self.controller.state.logs[-300:]))

    def _on_run_finished(self, run_id: str, status: str) -> None:
        self.refresh()

    def _selected_worker_ids(self) -> list[str]:
        ids: list[str] = []
        selection = self.workers_table.selectionModel()
        if not selection:
            return ids
        for model_index in selection.selectedRows():
            item = self.workers_table.item(model_index.row(), 0)
            if item:
                ids.append(item.text())
        return ids

    def _start(self) -> None:
        asyncio.create_task(self._start_run())

    async def _start_run(self) -> None:
        try:
            if self.mode_combo.currentText() == "Supervisor":
                await self.controller.start_supervisor(self.task_editor.toPlainText())
            else:
                await self.controller.start_strategy(
                    self.strategy_combo.currentText(),
                    self.task_editor.toPlainText(),
                )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Run failed", str(exc))

    def _cancel_workers(self) -> None:
        for worker_id in self._selected_worker_ids():
            asyncio.create_task(self.controller.cancel_worker(worker_id))

    def _merge_selected(self) -> None:
        worker_ids = self._selected_worker_ids()
        if not worker_ids:
            choice = QMessageBox.question(
                self,
                "Merge workers",
                "No workers selected. Merge all pending workers?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        asyncio.create_task(self.controller.merge_workers(worker_ids))

    def _toggle_queue(self) -> None:
        asyncio.create_task(self._toggle_queue_task())

    async def _toggle_queue_task(self) -> None:
        paused = await self.controller.pause_queue()
        self.pause_button.setText("Resume Queue" if paused else "Pause Queue")

    def _kill_supervisor(self) -> None:
        asyncio.create_task(self.controller.kill_supervisor())

    def _set_model(self) -> None:
        if not self.controller.available_models:
            QMessageBox.information(self, "Model", "No available models detected")
            return

        current = self.controller.current_model or self.controller.available_models[0]
        try:
            index = self.controller.available_models.index(current)
        except ValueError:
            index = 0
        model, ok = QInputDialog.getItem(
            self,
            "Default model",
            "Model",
            self.controller.available_models,
            index,
            False,
        )
        if ok:
            asyncio.create_task(self.controller.set_default_model(model))

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import tasks",
            str(Path.home()),
            "Task files (*.json *.yml *.yaml)",
        )
        if not path:
            return
        self.task_editor.setPlainText(Path(path).read_text())


class SwarmMainWindow(QMainWindow):
    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.history: SwarmHistoryStore = runtime.history_store
        self._next_session = 0
        self._controllers: dict[str, SessionController] = {}
        self._panels: dict[str, SessionPanel] = {}
        self._is_closing = False
        self._build_ui()
        self._add_session()

    def _build_ui(self) -> None:
        self.setWindowTitle("Codex Swarm")
        self.resize(1280, 800)

        split = QSplitter()
        self.setCentralWidget(split)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        filter_row = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "running", "completed", "failed"])
        self.mode_filter = QComboBox()
        self.mode_filter.addItems(["All", "supervisor", "strategy"])
        self.date_filter = QComboBox()
        self.date_filter.addItems(["All", "Last 24h", "Last 7d", "Last 30d"])
        self.add_session_button = QPushButton("New Session")
        self.add_session_button.clicked.connect(self._add_session)
        for widget in (self.status_filter, self.mode_filter, self.date_filter):
            widget.currentTextChanged.connect(self._refresh_history)
        filter_row.addWidget(self.status_filter)
        filter_row.addWidget(self.mode_filter)
        filter_row.addWidget(self.date_filter)
        filter_row.addWidget(self.add_session_button)
        right_layout.addLayout(filter_row)

        self.history_table = QTableWidget(0, 8)
        self.history_table.setHorizontalHeaderLabels(
            [
                "Run ID",
                "Mode",
                "Strategy",
                "Status",
                "Started",
                "Ended",
                "Cost",
                "Tokens",
            ]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history_table.itemSelectionChanged.connect(self._load_run_detail)
        right_layout.addWidget(self.history_table)

        self.history_detail = QPlainTextEdit()
        self.history_detail.setReadOnly(True)
        right_layout.addWidget(self.history_detail)

        split.addWidget(self.tabs)
        split.addWidget(right)

        self._refresh_history()

    def _add_session(self) -> None:
        if len(self._controllers) >= int(self.runtime.config.gui.max_concurrent_sessions):
            QMessageBox.information(self, "Session limit", "Maximum concurrent sessions reached")
            return

        self._next_session += 1
        session_id = f"session-{self._next_session}"
        controller = SessionController(
            repo_path=self.runtime.repo,
            base_config=self.runtime.config,
            session_id=session_id,
            history=self.history,
        )
        panel = SessionPanel(controller)
        self._controllers[session_id] = controller
        self._panels[session_id] = panel
        controller.run_finished.connect(lambda _run_id, _status: self._refresh_history())

        self.tabs.addTab(panel, session_id)
        self.tabs.setCurrentWidget(panel)

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        key = next((k for k, panel in self._panels.items() if panel is widget), None)
        if key is None:
            return
        if not self._close_session(key, confirm=True):
            return
        self.tabs.removeTab(index)

        if self.tabs.count() == 0:
            self._add_session()

        del self._panels[key]
        del self._controllers[key]
        self._refresh_history()

    def _close_session(self, key: str, confirm: bool = True) -> bool:
        controller = self._controllers.get(key)
        if not controller:
            return False

        if confirm and controller.active:
            choice = QMessageBox.question(
                self,
                "Close session",
                "Session is active. Stop and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return False

        asyncio.create_task(controller.shutdown())
        return True

    def _refresh_history(self) -> None:
        self.history_table.setRowCount(0)
        runs = self._query_runs()
        for row, run in enumerate(runs):
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, QTableWidgetItem(run.get("run_id", "")))
            self.history_table.setItem(row, 1, QTableWidgetItem(run.get("mode", "")))
            self.history_table.setItem(row, 2, QTableWidgetItem(run.get("strategy") or ""))
            self.history_table.setItem(row, 3, QTableWidgetItem(run.get("status", "")))
            self.history_table.setItem(row, 4, QTableWidgetItem(run.get("started_at", "") or ""))
            self.history_table.setItem(row, 5, QTableWidgetItem(run.get("ended_at", "") or ""))
            self.history_table.setItem(row, 6, QTableWidgetItem(f"{float(run.get('total_cost', 0) or 0):.2f}"))
            self.history_table.setItem(row, 7, QTableWidgetItem(str(run.get("total_tokens", 0))))

        self._load_run_detail()

    def _query_runs(self) -> list[dict[str, Any]]:
        status = self.status_filter.currentText()
        mode = self.mode_filter.currentText()
        date_text = self.date_filter.currentText()

        since = None
        if date_text == "Last 24h":
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        elif date_text == "Last 7d":
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        elif date_text == "Last 30d":
            since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        return self.history.list_runs(
            status=None if status == "All" else status,
            mode=None if mode == "All" else mode,
            since=since,
            limit=int(self.runtime.config.gui.history_max_runs),
        )

    def _load_run_detail(self) -> None:
        selected = self.history_table.selectedItems()
        if not selected:
            self.history_detail.setPlainText("")
            return

        run_id = self.history_table.item(selected[0].row(), 0).text()
        data = self.history.get_run(run_id)
        if not data:
            self.history_detail.setPlainText("Run not found")
            return

        lines = [
            f"Run ID: {run_id}",
            f"Status: {data.get('status', '')}",
            f"Mode: {data.get('mode', '')}",
            f"Strategy: {data.get('strategy', '') or ''}",
            f"Repo: {data.get('repo', '')}",
            f"Started: {data.get('started_at', '')}",
            f"Ended: {data.get('ended_at', '')}",
            f"Cost: {float(data.get('total_cost', 0) or 0):.2f}",
            f"Tokens: {data.get('total_tokens', 0)}",
            "",
            "Workers:",
        ]

        for worker in data.get("workers", []):
            lines.append(
                f"- {worker.get('worker_id')} | {worker.get('status')} | merged={bool(worker.get('merged'))} "
                f"cost={float(worker.get('estimated_cost', 0) or 0):.2f}"
            )
            if worker.get("summary"):
                lines.append(f"  {worker.get('summary')}")

        self.history_detail.setPlainText("\n".join(lines))

    async def _shutdown_all_sessions(self) -> None:
        for controller in list(self._controllers.values()):
            await controller.shutdown()

    async def _close_window(self) -> None:
        await self._shutdown_all_sessions()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._is_closing:
            event.accept()
            return

        active_sessions = [controller for controller in self._controllers.values() if controller.active]
        if active_sessions:
            choice = QMessageBox.question(
                self,
                "Close",
                "Close with active sessions?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._is_closing = True
        event.ignore()
        asyncio.create_task(self._close_window())
