from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from diffasaurus.core.paths import powershell_runtimes_dir
from diffasaurus.core.powershell_runtime import (
    PowerShellRuntime,
    discover_powershell_runtimes,
    import_portable_runtime,
    remove_portable_runtime,
    select_powershell_runtime,
    selected_powershell_runtime,
)


RUNTIME_ROLE = int(Qt.ItemDataRole.UserRole)


class PowerShellManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PowerShell runtimes")
        self.resize(900, 500)
        self.runtimes: list[PowerShellRuntime] = []
        self.selected_runtime: PowerShellRuntime | None = None

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

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ("Active", "Version", "Source", "Architecture", "Executable")
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.use_selected())
        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#8295a8;")
        layout.addWidget(self.status)

        tools = QHBoxLayout()
        add = QPushButton("＋ Add portable folder")
        add.clicked.connect(self.add_portable)
        self.remove = QPushButton("Remove portable")
        self.remove.clicked.connect(self.remove_portable)
        open_folder = QPushButton("Open portable folder")
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(powershell_runtimes_dir()))
            )
        )
        downloads = QPushButton("PowerShell downloads")
        downloads.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/PowerShell/PowerShell/releases")
            )
        )
        refresh = QPushButton("Rescan")
        refresh.clicked.connect(self.refresh)
        for button in (add, self.remove, open_folder, downloads, refresh):
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

    def refresh(self, preferred: Path | None = None):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.runtimes = discover_powershell_runtimes()
        finally:
            QApplication.restoreOverrideCursor()
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
                item.setData(RUNTIME_ROLE, runtime)
                self.table.setItem(row, column, item)
            if runtime.identity == preferred_key or (
                selected_row < 0 and runtime.identity == active_key
            ):
                selected_row = row

        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self.runtimes:
            self.table.selectRow(0)
        self.status.setText(
            f"{len(self.runtimes)} usable runtime{'s' if len(self.runtimes) != 1 else ''} detected."
            if self.runtimes
            else "No usable PowerShell runtime detected. Add an extracted portable PowerShell folder."
        )
        self._selection_changed()

    def _current_runtime(self) -> PowerShellRuntime | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        runtime = item.data(RUNTIME_ROLE) if item else None
        return runtime if isinstance(runtime, PowerShellRuntime) else None

    def _selection_changed(self):
        runtime = self._current_runtime()
        self.use.setEnabled(bool(runtime and runtime.supported))
        self.remove.setEnabled(bool(runtime and runtime.managed))
        if runtime and not runtime.supported:
            self.status.setText(
                f"PowerShell {runtime.version} is detected but unsupported; version 7 or newer is required."
            )

    def add_portable(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose extracted portable PowerShell folder",
            str(Path.home() / "Downloads"),
        )
        if not folder:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            runtime = import_portable_runtime(Path(folder))
        except Exception as exc:
            QMessageBox.warning(self, "Portable PowerShell", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
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
        try:
            remove_portable_runtime(runtime)
        except Exception as exc:
            QMessageBox.warning(self, "Remove portable PowerShell", str(exc))
            return
        self.refresh()

    def use_selected(self):
        runtime = self._current_runtime()
        if runtime is None or not runtime.supported:
            return
        select_powershell_runtime(runtime)
        self.selected_runtime = runtime
        self.accept()
