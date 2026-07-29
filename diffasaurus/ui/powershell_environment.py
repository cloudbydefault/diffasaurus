from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QThreadPool, QUrl, Qt
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.powershell_environment import (
    ISOLATION_PREAMBLE,
    PowerShellModule,
    copy_installed_modules,
    list_installed_modules,
    list_private_modules,
    powershell_environment,
    remove_private_module,
    runtime_modules_dir,
)
from diffasaurus.core.powershell_runtime import PowerShellRuntime
from diffasaurus.ui.background import BackgroundCall


MODULE_ROLE = int(Qt.ItemDataRole.UserRole)

INSTALL_MODULE_SCRIPT = (
    ISOLATION_PREAMBLE
    + "$name = $env:DIFFASAURUS_MODULE_NAME;"
    "$version = $env:DIFFASAURUS_MODULE_VERSION;"
    "$saveResource = Get-Command Save-PSResource -ErrorAction SilentlyContinue;"
    "if ($saveResource) {"
    "  $parameters = @{"
    "    Name=$name; Path=$env:DIFFASAURUS_MODULE_ROOT;"
    "    ErrorAction='Stop'"
    "  };"
    "  foreach ($optional in 'TrustRepository','AcceptLicense','Quiet') {"
    "    if ($saveResource.Parameters.ContainsKey($optional)) {"
    "      $parameters[$optional] = $true"
    "    }"
    "  };"
    "  if ($version) { $parameters.Version = $version };"
    "  Save-PSResource @parameters"
    "} elseif (Get-Command Save-Module -ErrorAction SilentlyContinue) {"
    "  $parameters = @{"
    "    Name=$name; Path=$env:DIFFASAURUS_MODULE_ROOT;"
    "    Force=$true; ErrorAction='Stop'"
    "  };"
    "  if ($version) { $parameters.RequiredVersion = $version };"
    "  Save-Module @parameters"
    "} else {"
    "  throw 'This runtime has neither Save-PSResource nor Save-Module.'"
    "}"
)


def qprocess_environment(
    runtime: PowerShellRuntime,
    extra: dict[str, str] | None = None,
) -> QProcessEnvironment:
    values = powershell_environment(runtime)
    if extra:
        values.update(extra)
    environment = QProcessEnvironment()
    for key, value in values.items():
        environment.insert(str(key), str(value))
    return environment


class PowerShellEnvironmentDialog(QDialog):
    def __init__(self, runtime: PowerShellRuntime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.modules: list[PowerShellModule] = []
        self.installed_modules: list[PowerShellModule] = []
        self._tasks: set[BackgroundCall] = set()
        self._install_queue: list[tuple[str, str]] = []
        self.setWindowTitle(f"PowerShell {runtime.version} environment")
        self.resize(980, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        title = QLabel(f"PowerShell {runtime.version} · isolated environment")
        title.setStyleSheet("font-size:22px; font-weight:850;")
        details = QLabel(
            f"{runtime.source} · {runtime.architecture or 'Unknown architecture'}\n"
            f"{runtime.path}"
        )
        details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.setStyleSheet("color:#8295a8;")
        layout.addWidget(title)
        layout.addWidget(details)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_modules_tab(), "Isolated modules")
        self.tabs.addTab(self._build_installed_modules_tab(), "Installed modules")
        self.tabs.addTab(self._build_console_tab(), "PowerShell console")
        layout.addWidget(self.tabs, 1)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(close)
        layout.addLayout(bottom)

        self.install_process = QProcess(self)
        self.install_process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.install_process.readyReadStandardOutput.connect(
            self._read_install_output
        )
        self.install_process.finished.connect(self._install_finished)

        self.console_process = QProcess(self)
        self.console_process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.console_process.started.connect(self._console_started)
        self.console_process.readyReadStandardOutput.connect(
            self._read_console_output
        )
        self.console_process.finished.connect(self._console_finished)

        self.refresh_modules()
        self.start_console()

    def _build_modules_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        explanation = QLabel(
            "Only modules saved here are visible to this runtime during report runs. "
            "System and other PowerShell versions remain separate."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#8295a8;")
        layout.addWidget(explanation)

        self.module_table = QTableWidget(0, 3)
        self.module_table.setHorizontalHeaderLabels(("Module", "Version", "Location"))
        self.module_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.module_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.module_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.module_table.verticalHeader().setVisible(False)
        self.module_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.module_table.itemSelectionChanged.connect(self._module_selection_changed)
        layout.addWidget(self.module_table, 1)

        self.module_status = QLabel("")
        self.module_status.setStyleSheet("color:#8295a8;")
        self.module_progress = QProgressBar()
        self.module_progress.setRange(0, 0)
        self.module_progress.hide()
        layout.addWidget(self.module_status)
        layout.addWidget(self.module_progress)

        actions = QHBoxLayout()
        self.install_button = QPushButton("＋ Install module…")
        self.install_button.clicked.connect(self.prompt_install_module)
        self.report_modules_button = QPushButton("Install report modules")
        self.report_modules_button.clicked.connect(self.install_report_modules)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected_module)
        self.remove_button.setEnabled(False)
        open_folder = QPushButton("Open modules folder")
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(runtime_modules_dir(self.runtime)))
            )
        )
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_modules)
        for button in (
            self.install_button,
            self.report_modules_button,
            self.remove_button,
            open_folder,
            self.refresh_button,
        ):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)

        self.install_output = QPlainTextEdit()
        self.install_output.setReadOnly(True)
        self.install_output.setMaximumBlockCount(1_000)
        self.install_output.setPlaceholderText("Module installation output appears here.")
        self.install_output.setMaximumHeight(150)
        layout.addWidget(self.install_output)
        return tab

    def _build_installed_modules_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        explanation = QLabel(
            "These modules are installed in normal CurrentUser or AllUsers "
            "locations and are visible when this PowerShell runs outside "
            "Diffasaurus. Copying creates an independent version for reports "
            "without changing the original installation."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#8295a8;")
        layout.addWidget(explanation)

        self.installed_table = QTableWidget(0, 3)
        self.installed_table.setHorizontalHeaderLabels(
            ("Module", "Version", "Native location")
        )
        self.installed_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.installed_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.installed_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.installed_table.verticalHeader().setVisible(False)
        self.installed_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.installed_table.itemSelectionChanged.connect(
            self._installed_selection_changed
        )
        layout.addWidget(self.installed_table, 1)

        self.installed_status = QLabel("")
        self.installed_status.setStyleSheet("color:#8295a8;")
        self.installed_progress = QProgressBar()
        self.installed_progress.setRange(0, 0)
        self.installed_progress.hide()
        layout.addWidget(self.installed_status)
        layout.addWidget(self.installed_progress)

        actions = QHBoxLayout()
        self.copy_selected_button = QPushButton("Copy selected to isolated")
        self.copy_selected_button.clicked.connect(self.copy_selected_installed)
        self.copy_selected_button.setEnabled(False)
        self.copy_all_button = QPushButton("Copy all to isolated")
        self.copy_all_button.clicked.connect(self.copy_all_installed)
        self.copy_all_button.setEnabled(False)
        self.refresh_installed_button = QPushButton("Refresh")
        self.refresh_installed_button.clicked.connect(self.refresh_installed_modules)
        actions.addWidget(self.copy_selected_button)
        actions.addWidget(self.copy_all_button)
        actions.addWidget(self.refresh_installed_button)
        actions.addStretch()
        layout.addLayout(actions)
        return tab

    def _build_console_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        explanation = QLabel(
            "This persistent console uses the selected executable and this runtime's "
            "private module path. Variables and imported modules remain available "
            "until the console is restarted."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#8295a8;")
        layout.addWidget(explanation)

        self.console_output = QPlainTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setMaximumBlockCount(5_000)
        layout.addWidget(self.console_output, 1)

        command_row = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText("Enter a PowerShell command")
        self.command.returnPressed.connect(self.send_command)
        self.send_button = QPushButton("Run")
        self.send_button.clicked.connect(self.send_command)
        command_row.addWidget(QLabel("PS>"))
        command_row.addWidget(self.command, 1)
        command_row.addWidget(self.send_button)
        layout.addLayout(command_row)

        tools = QHBoxLayout()
        self.restart_console_button = QPushButton("Restart console")
        self.restart_console_button.clicked.connect(self.restart_console)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.console_output.clear)
        tools.addWidget(self.restart_console_button)
        tools.addWidget(clear)
        tools.addStretch()
        layout.addLayout(tools)
        return tab

    def _start_background(self, function, on_success, *args):
        task = BackgroundCall(function, *args)
        self._tasks.add(task)
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(self._background_failed)
        task.signals.done.connect(lambda: self._tasks.discard(task))
        QThreadPool.globalInstance().start(task)

    def refresh_modules(self):
        self.refresh_button.setEnabled(False)
        self.module_progress.show()
        self.module_status.setText("Scanning this runtime's private modules…")
        self._start_background(
            list_private_modules,
            self._modules_loaded,
            self.runtime,
        )
        self.refresh_installed_modules()

    def refresh_installed_modules(self):
        self.refresh_installed_button.setEnabled(False)
        self.copy_selected_button.setEnabled(False)
        self.copy_all_button.setEnabled(False)
        self.installed_progress.show()
        self.installed_status.setText(
            "Scanning modules installed outside Diffasaurus…"
        )
        self._start_background(
            list_installed_modules,
            self._installed_modules_loaded,
            self.runtime,
        )

    def _modules_loaded(self, value):
        self.modules = list(value)
        self.module_table.setRowCount(len(self.modules))
        for row, module in enumerate(self.modules):
            for column, text in enumerate(
                (module.name, module.version, str(module.path))
            ):
                item = QTableWidgetItem(text)
                item.setData(MODULE_ROLE, module)
                self.module_table.setItem(row, column, item)
        self.module_progress.hide()
        self.refresh_button.setEnabled(True)
        self.module_status.setText(
            f"{len(self.modules)} private module version"
            f"{'s' if len(self.modules) != 1 else ''} available to PowerShell "
            f"{self.runtime.version}."
        )
        self._module_selection_changed()

    def _installed_modules_loaded(self, value):
        self.installed_modules = list(value)
        self.installed_table.setRowCount(len(self.installed_modules))
        for row, module in enumerate(self.installed_modules):
            for column, text in enumerate(
                (module.name, module.version, str(module.path))
            ):
                item = QTableWidgetItem(text)
                item.setData(MODULE_ROLE, module)
                self.installed_table.setItem(row, column, item)
        self.installed_progress.hide()
        self.refresh_installed_button.setEnabled(True)
        self.copy_all_button.setEnabled(bool(self.installed_modules))
        self.installed_status.setText(
            f"{len(self.installed_modules)} installed module version"
            f"{'s' if len(self.installed_modules) != 1 else ''} detected outside "
            "Diffasaurus. Built-in PowerShell modules are not included."
        )
        self._installed_selection_changed()

    def _background_failed(self, message: str):
        self.module_progress.hide()
        self.installed_progress.hide()
        self.refresh_button.setEnabled(True)
        self.refresh_installed_button.setEnabled(True)
        self.copy_all_button.setEnabled(bool(self.installed_modules))
        self.module_status.setText(message)
        self.installed_status.setText(message)
        self._installed_selection_changed()

    def _current_module(self) -> PowerShellModule | None:
        row = self.module_table.currentRow()
        item = self.module_table.item(row, 0) if row >= 0 else None
        module = item.data(MODULE_ROLE) if item else None
        return module if isinstance(module, PowerShellModule) else None

    def _current_installed_module(self) -> PowerShellModule | None:
        row = self.installed_table.currentRow()
        item = self.installed_table.item(row, 0) if row >= 0 else None
        module = item.data(MODULE_ROLE) if item else None
        return module if isinstance(module, PowerShellModule) else None

    def _module_selection_changed(self):
        self.remove_button.setEnabled(
            self._current_module() is not None
            and self.install_process.state() == QProcess.ProcessState.NotRunning
        )

    def _installed_selection_changed(self):
        self.copy_selected_button.setEnabled(
            self._current_installed_module() is not None
            and not self.installed_progress.isVisible()
        )

    def copy_selected_installed(self):
        module = self._current_installed_module()
        if module is not None:
            self._copy_installed([module])

    def copy_all_installed(self):
        if self.installed_modules:
            self._copy_installed(self.installed_modules)

    def _copy_installed(self, modules: list[PowerShellModule]):
        noun = (
            f"{modules[0].name} {modules[0].version}"
            if len(modules) == 1
            else f"all {len(modules)} detected module versions"
        )
        answer = QMessageBox.question(
            self,
            "Copy installed modules",
            f"Copy {noun} into PowerShell {self.runtime.version}'s isolated "
            "environment?\n\nThe original installed modules will not be changed.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.installed_progress.show()
        self.installed_status.setText(f"Copying {noun} in the background…")
        self.copy_selected_button.setEnabled(False)
        self.copy_all_button.setEnabled(False)
        self.refresh_installed_button.setEnabled(False)
        self._start_background(
            copy_installed_modules,
            self._installed_modules_copied,
            self.runtime,
            modules,
        )

    def _installed_modules_copied(self, count):
        self.installed_status.setText(
            f"Copied {count} module version{'s' if count != 1 else ''}. "
            "Existing isolated versions were left unchanged."
        )
        self.refresh_modules()

    def prompt_install_module(self):
        name, accepted = QInputDialog.getText(
            self,
            "Install private module",
            "Module name from a registered PowerShell repository:",
            text="Microsoft.Graph",
        )
        if not accepted or not name.strip():
            return
        version, accepted = QInputDialog.getText(
            self,
            "Optional module version",
            "Exact version (leave empty for latest):",
        )
        if not accepted:
            return
        self._install_queue = [(name.strip(), version.strip())]
        self._run_next_install()

    def install_report_modules(self):
        answer = QMessageBox.question(
            self,
            "Install report modules",
            "Download Microsoft.Graph and ExchangeOnlineManagement into this "
            "runtime's private environment?\n\nThis can take several minutes.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._install_queue = [
            ("Microsoft.Graph", ""),
            ("ExchangeOnlineManagement", ""),
        ]
        self._run_next_install()

    def _run_next_install(self):
        if self.install_process.state() != QProcess.ProcessState.NotRunning:
            return
        if not self._install_queue:
            self._set_install_busy(False)
            self.refresh_modules()
            return
        name, version = self._install_queue.pop(0)
        self._set_install_busy(True)
        self.module_status.setText(
            f"Installing {name}{f' {version}' if version else ''}…"
        )
        self.install_output.appendPlainText(
            f"\n▶ Installing {name}{f' {version}' if version else ''}\n"
        )
        self.install_process.setProcessEnvironment(
            qprocess_environment(
                self.runtime,
                {
                    "DIFFASAURUS_MODULE_NAME": name,
                    "DIFFASAURUS_MODULE_VERSION": version,
                },
            )
        )
        self.install_process.start(
            str(self.runtime.path),
            (
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                INSTALL_MODULE_SCRIPT,
            ),
        )

    def _set_install_busy(self, busy: bool):
        self.module_progress.setVisible(busy)
        self.install_button.setEnabled(not busy)
        self.report_modules_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.remove_button.setEnabled(not busy and self._current_module() is not None)

    def _read_install_output(self):
        value = bytes(self.install_process.readAllStandardOutput()).decode(
            "utf-8",
            errors="replace",
        )
        self.install_output.insertPlainText(value)

    def _install_finished(self, exit_code: int, _status):
        if exit_code != 0:
            self.install_output.appendPlainText(
                f"\n✗ Installation failed with exit code {exit_code}.\n"
            )
            self._install_queue.clear()
            self._set_install_busy(False)
            self.module_status.setText("Module installation failed. Review the output.")
            return
        self.install_output.appendPlainText("\n✓ Installation completed.\n")
        self._run_next_install()

    def remove_selected_module(self):
        module = self._current_module()
        if module is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove private module",
            f"Remove {module.name} {module.version} from PowerShell "
            f"{self.runtime.version}?\n\n{module.path}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            remove_private_module(self.runtime, module)
        except Exception as exc:
            QMessageBox.warning(self, "Remove module", str(exc))
            return
        self.refresh_modules()

    def start_console(self):
        if self.console_process.state() != QProcess.ProcessState.NotRunning:
            return
        self.command.setEnabled(False)
        self.send_button.setEnabled(False)
        self.console_output.appendPlainText(
            f"Starting {self.runtime.path}…\n"
        )
        self.console_process.setProcessEnvironment(
            qprocess_environment(self.runtime)
        )
        self.console_process.start(
            str(self.runtime.path),
            ("-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"),
        )

    def _console_started(self):
        bootstrap = (
            ISOLATION_PREAMBLE
            + "$PSStyle.OutputRendering = 'PlainText';"
            f"Write-Output 'Diffasaurus PowerShell {self.runtime.version} "
            f"({self.runtime.architecture})';"
            "Write-Output ('Private modules: ' + $env:DIFFASAURUS_MODULE_ROOT);"
            "Write-Output ('PSModulePath: ' + $env:PSModulePath)\r\n"
        )
        self.console_process.write(bootstrap.encode("utf-8"))
        self.command.setEnabled(True)
        self.send_button.setEnabled(True)
        self.command.setFocus()

    def send_command(self):
        command = self.command.text().strip()
        if (
            not command
            or self.console_process.state() == QProcess.ProcessState.NotRunning
        ):
            return
        self.console_output.appendPlainText(f"\nPS> {command}")
        self.console_process.write((command + "\r\n").encode("utf-8"))
        self.command.clear()

    def _read_console_output(self):
        value = bytes(self.console_process.readAllStandardOutput()).decode(
            "utf-8",
            errors="replace",
        )
        self.console_output.insertPlainText(value)
        scrollbar = self.console_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _console_finished(self, exit_code: int, _status):
        self.console_output.appendPlainText(
            f"\nConsole stopped (exit {exit_code}).\n"
        )
        self.command.setEnabled(False)
        self.send_button.setEnabled(False)

    def restart_console(self):
        if self.console_process.state() != QProcess.ProcessState.NotRunning:
            self.console_process.kill()
            self.console_process.waitForFinished(1_000)
        self.start_console()

    def closeEvent(self, event: QCloseEvent):
        if self.install_process.state() != QProcess.ProcessState.NotRunning:
            answer = QMessageBox.question(
                self,
                "Installation in progress",
                "Stop the current module installation and close?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.install_process.kill()
            self.install_process.waitForFinished(1_000)
        if self.console_process.state() != QProcess.ProcessState.NotRunning:
            self.console_process.write(b"exit\n")
            if not self.console_process.waitForFinished(500):
                self.console_process.kill()
                self.console_process.waitForFinished(500)
        event.accept()
