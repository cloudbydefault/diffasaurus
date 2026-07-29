from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThreadPool, QUrl, Qt
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from diffasaurus.core.paths import powershell_runtimes_dir
from diffasaurus.core.powershell_environment import (
    list_installed_modules,
    private_module_count,
)
from diffasaurus.core.powershell_runtime import (
    PowerShellRuntime,
    discover_powershell_runtimes,
    import_portable_runtime,
    remove_portable_runtime,
    select_powershell_runtime,
    selected_powershell_runtime,
)
from diffasaurus.ui.background import BackgroundCall
from diffasaurus.ui.powershell_environment import PowerShellEnvironmentDialog


RUNTIME_ROLE = int(Qt.ItemDataRole.UserRole)


class PowerShellManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PowerShell runtimes")
        self.resize(900, 500)
        self.runtimes: list[PowerShellRuntime] = []
        self.selected_runtime: PowerShellRuntime | None = None
        self._tasks: set[BackgroundCall] = set()
        self._refresh_generation = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title = QLabel("PowerShell runtime manager")
        title.setStyleSheet("font-size:22px; font-weight:850;")
        subtitle = QLabel(
            "Choose the PowerShell 7 runtime used for report generation, "
            "or import an extracted portable distribution."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#8295a8;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            (
                "Active",
                "Version",
                "Source",
                "Architecture",
                "Isolated",
                "Installed",
                "Executable",
            )
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        for column in range(6):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.use_selected())
        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#8295a8;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.status)
        layout.addWidget(self.progress)

        tools = QHBoxLayout()
        self.add = QPushButton("＋ Import portable pwsh")
        self.add.clicked.connect(self.add_portable)
        self.remove = QPushButton("Remove portable")
        self.remove.clicked.connect(self.remove_portable)
        self.environment = QPushButton("Modules && console")
        self.environment.clicked.connect(self.open_environment)
        self.open_folder = QPushButton("Open portable folder")
        self.open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(powershell_runtimes_dir()))
            )
        )
        self.downloads = QPushButton("PowerShell downloads")
        self.downloads.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/PowerShell/PowerShell/releases")
            )
        )
        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(self.refresh)
        for button in (
            self.add,
            self.remove,
            self.environment,
            self.open_folder,
            self.downloads,
            self.rescan,
        ):
            tools.addWidget(button)
        tools.addStretch()
        layout.addLayout(tools)

        actions = QHBoxLayout()
        actions.addStretch()
        close = QPushButton("Cancel")
        close.clicked.connect(self.reject)
        self.use = QPushButton("Use selected runtime")
        self.use.setObjectName("primaryButton")
        self.use.clicked.connect(self.use_selected)
        actions.addWidget(close)
        actions.addWidget(self.use)
        layout.addLayout(actions)
        self.refresh()

    def _start_background(self, function, on_success, *args):
        task = BackgroundCall(function, *args)
        self._tasks.add(task)
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(self._operation_failed)
        task.signals.done.connect(lambda: self._tasks.discard(task))
        QThreadPool.globalInstance().start(task)

    def _set_busy(self, busy: bool, message: str = ""):
        self.progress.setVisible(busy)
        for button in (self.add, self.remove, self.rescan, self.use):
            button.setEnabled(not busy)
        self.environment.setEnabled(not busy and self._current_runtime() is not None)
        if message:
            self.status.setText(message)

    def refresh(self, preferred: Path | bool | None = None):
        preferred_path = preferred if isinstance(preferred, Path) else None
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._set_busy(True, "Scanning PowerShell installations in the background…")
        self._start_background(
            discover_powershell_runtimes,
            lambda runtimes: self._runtimes_loaded(
                list(runtimes),
                preferred_path,
                generation,
            ),
        )

    def _runtimes_loaded(
        self,
        runtimes: list[PowerShellRuntime],
        preferred: Path | None,
        generation: int,
    ):
        if generation != self._refresh_generation:
            return
        self.runtimes = runtimes
        active = selected_powershell_runtime(self.runtimes)
        preferred_key = str(preferred.resolve()) if preferred else ""
        active_key = active.identity if active else ""
        self.table.setRowCount(len(self.runtimes))
        selected_row = -1
        for row, runtime in enumerate(self.runtimes):
            values = (
                "●" if runtime.identity == active_key else "",
                runtime.version,
                runtime.source,
                runtime.architecture or "Unknown",
                str(private_module_count(runtime)),
                "Scanning…",
                str(runtime.path),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(
                        QColor("#4fd1a5")
                        if runtime.identity == active_key
                        else QColor("transparent")
                    )
                if column == 1 and not runtime.supported:
                    item.setToolTip("Diffasaurus requires PowerShell 7 or newer.")
                elif column == 4:
                    item.setToolTip(
                        "Independent modules available to Diffasaurus reports "
                        "for this exact runtime."
                    )
                elif column == 5:
                    item.setToolTip(
                        "Scanning user and machine module locations…"
                    )
                item.setData(RUNTIME_ROLE, runtime)
                self.table.setItem(row, column, item)
            self._start_background(
                list_installed_modules,
                lambda modules, identity=runtime.identity: self._installed_modules_loaded(
                    identity, len(modules)
                ),
                runtime,
            )
            if runtime.identity == preferred_key or (
                selected_row < 0 and runtime.identity == active_key
            ):
                selected_row = row

        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self.runtimes:
            self.table.selectRow(0)
        self._set_busy(False)
        self.status.setText(
            f"{len(self.runtimes)} usable runtime{'s' if len(self.runtimes) != 1 else ''} detected."
            if self.runtimes
            else "No usable PowerShell runtime detected. Add an extracted portable PowerShell folder."
        )
        self._selection_changed()

    def _installed_modules_loaded(self, identity: str, count: int):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            runtime = item.data(RUNTIME_ROLE) if item else None
            if isinstance(runtime, PowerShellRuntime) and runtime.identity == identity:
                count_item = self.table.item(row, 5)
                if count_item is not None:
                    count_item.setText(str(count))
                    count_item.setToolTip(
                        "User and machine modules visible to this PowerShell "
                        "outside Diffasaurus. Built-in modules are excluded."
                    )
                return

    def _current_runtime(self) -> PowerShellRuntime | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        runtime = item.data(RUNTIME_ROLE) if item else None
        return runtime if isinstance(runtime, PowerShellRuntime) else None

    def _selection_changed(self):
        runtime = self._current_runtime()
        self.use.setEnabled(bool(runtime and runtime.supported))
        self.remove.setEnabled(bool(runtime and runtime.managed))
        self.environment.setEnabled(bool(runtime and runtime.supported))
        if runtime and not runtime.supported:
            self.status.setText(
                f"PowerShell {runtime.version} is detected but unsupported; version 7 or newer is required."
            )

    def add_portable(self):
        executable, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose pwsh from the extracted portable PowerShell folder",
            str(Path.home() / "Downloads"),
            "PowerShell executable (pwsh pwsh.exe);;All files (*)",
        )
        if not executable:
            return
        self._set_busy(
            True,
            "Importing and validating PowerShell in the background… "
            "You can continue moving or repainting this window.",
        )
        self._start_background(
            import_portable_runtime,
            self._portable_imported,
            Path(executable),
        )

    def _portable_imported(self, runtime):
        if not isinstance(runtime, PowerShellRuntime):
            self._operation_failed("The imported runtime result was invalid.")
            return
        self.refresh(runtime.path)
        QMessageBox.information(
            self,
            "Portable PowerShell added",
            f"PowerShell {runtime.version} was copied into Diffasaurus and selected.",
        )

    def remove_portable(self):
        runtime = self._current_runtime()
        if runtime is None or not runtime.managed:
            return
        answer = QMessageBox.question(
            self,
            "Remove portable PowerShell",
            f"Remove PowerShell {runtime.version} from Diffasaurus?\n\n{runtime.path}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True, "Removing the portable runtime in the background…")
        self._start_background(
            remove_portable_runtime,
            lambda _result: self.refresh(),
            runtime,
        )

    def _operation_failed(self, message: str):
        self._set_busy(False)
        QMessageBox.warning(self, "PowerShell runtime", message)
        self._selection_changed()

    def open_environment(self):
        runtime = self._current_runtime()
        if runtime is None or not runtime.supported:
            return
        dialog = PowerShellEnvironmentDialog(runtime, self)
        dialog.exec()
        self.refresh(runtime.path)

    def use_selected(self):
        runtime = self._current_runtime()
        if runtime is None or not runtime.supported:
            return
        select_powershell_runtime(runtime)
        self.selected_runtime = runtime
        self.accept()
