from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QSize, QThreadPool, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from diffasaurus.core.paths import (
    list_report_scripts,
    project_root,
)
from diffasaurus.core.powershell_environment import (
    REPORT_COMMAND,
    powershell_environment,
    private_module_count,
)
from diffasaurus.core.powershell_runtime import (
    PowerShellRuntime,
    discover_powershell_runtimes,
    select_powershell_runtime,
    selected_powershell_runtime,
)
from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.configuration_policies.integration import (
    anchor_bundle_status,
    discovery_index,
    legacy_configuration_policy_diagnostics,
)
from diffasaurus.core.configuration_policies.history import discover_policy_snapshots
from diffasaurus.core.report_history import expected_business_days, scan_report_index
from diffasaurus.core.settings import get_active_reports_dir
from diffasaurus.ui.background import BackgroundCall
from diffasaurus.ui.powershell_manager import PowerShellManagerDialog


REPORT_CATALOG = {
    "_app_ENTRA_Users_Properties.ps1": (
        "👤", "ENTRA · Identities", "Identity attributes and licenses", "Entra_Users_Properties"
    ),
    "_app_ENTRA_Users_Activity.ps1": (
        "📈", "ENTRA · User activity", "Sign-ins and inactivity", "Entra_Users_Activity"
    ),
    "_app_ENTRA_Users_AuthenticationMethods.ps1": (
        "🔐", "ENTRA · Authentication", "MFA and passwordless posture",
        "Entra_Users_AuthenticationMethods",
    ),
    "_app_ENTRA_Groups_Information.ps1": (
        "👥", "ENTRA · Groups", "Groups, owners and dependencies", "Entra_Groups_Dependencies"
    ),
    "_app_ENTRA_Groups_User_Memberships.ps1": (
        "🔗", "ENTRA · User memberships", "Group membership by identity",
        "Entra_Group_User_Memberships",
    ),
    "_app_ENTRA_Access_Packages.ps1": (
        "🎟", "ENTRA · Access packages", "Entitlement management packages", "Entra_Access_Packages"
    ),
    "app_ENTRA_AccessPackage_Assignments.ps1": (
        "🎫", "ENTRA · Package assignments", "Access package assignments",
        "Entra_AccessPackage_User_Assignments",
    ),
    "_app_ENTRA_Role_Assignments.ps1": (
        "🛡", "ENTRA · Role assignments", "Privileged role governance", "Entra_Role_Assignments"
    ),
    "_app_INTUNE_Users_Devices.ps1": (
        "💻", "INTUNE · Managed devices", "Compliance and activity",
        "Intune_ManagedDevices_Compliance",
    ),
    "_app_INTUNE_Autopilot_Devices.ps1": (
        "🚀", "INTUNE · Autopilot", "Autopilot inventory", "Intune_Devices_Autopilot"
    ),
    "_app_INTUNE_Apps_Report.ps1": (
        "📦", "INTUNE · Applications", "Application inventory", "Intune_Apps_Full"
    ),
    "_app_INTUNE_iOS_Devices.ps1": (
        "📱", "INTUNE · iOS devices", "iPhone and iPad posture", "Intune_iOS_Devices"
    ),
    "_app_INTUNE_Android_Devices.ps1": (
        "📱", "INTUNE · Android devices", "Android inventory and security posture",
        "Intune_Android_Devices",
    ),
    "app_INTUNE_ConfigurationPolicy.ps1": (
        "⚙", "INTUNE · Configuration policies", "Settings, assignments and policy history",
        CONFIGURATION_POLICY_FAMILY,
    ),
    "app_EXCHANGE_SharedMailboxes_Report.ps1": (
        "📬", "EXCHANGE · Shared mailboxes", "Permissions and activity",
        "Exchange_SharedMailboxes",
    ),
}

FAMILY_DISPLAY_NAMES: dict[str, str] = {
    family: title for _, (_, title, _, family) in REPORT_CATALOG.items()
}

CATALOG_FAMILY_ORDER: tuple[str, ...] = tuple(
    family for _, (_, _, _, family) in REPORT_CATALOG.items()
)


def family_display_name(family: str) -> str:
    return FAMILY_DISPLAY_NAMES.get(family, family.replace("_", " "))


SCRIPT_PATH_ROLE = int(Qt.ItemDataRole.UserRole)
MISSING_ROLE = SCRIPT_PATH_ROLE + 1


def graph_scopes(script: Path) -> list[str]:
    text = script.read_text(encoding="utf-8", errors="ignore")
    scopes = re.findall(r"[\"']([A-Za-z][A-Za-z0-9.]+(?:Read|Write|Manage)[A-Za-z0-9.]*)[\"']", text)
    return sorted(set(scopes))


class RunScriptsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate tenant snapshots")
        self.resize(920, 680)
        self.runtimes: list[PowerShellRuntime] = []
        self.pwsh_runtime: PowerShellRuntime | None = None
        self._tasks: set[BackgroundCall] = set()
        self._refresh_generation = 0
        self.report_dir = get_active_reports_dir()
        self.queue: list[Path] = []
        self.failures: list[Path] = []
        self.current_index = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        title = QLabel("Generate fresh CSV reports")
        title.setStyleSheet("font-size:22px; font-weight:850;")
        subtitle = QLabel(
            "Choose one or more datasets. Reports run sequentially and become new timeline snapshots."
        )
        subtitle.setStyleSheet("color:#8295a8;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        runtime_card = QFrame()
        runtime_card.setObjectName("runtimeCard")
        runtime_layout = QHBoxLayout(runtime_card)
        runtime_layout.setContentsMargins(14, 11, 14, 11)
        runtime_layout.setSpacing(10)
        runtime_label = QLabel("POWERSHELL")
        runtime_label.setStyleSheet(
            "color:#8295a8; font-size:10px; font-weight:750; letter-spacing:1px;"
        )
        self.runtime_combo = QComboBox()
        self.runtime_combo.setMinimumWidth(370)
        self.runtime_combo.currentIndexChanged.connect(self.runtime_changed)
        self.runtime_status = QLabel("")
        self.runtime_status.setStyleSheet("color:#8295a8;")
        self.manage_runtime_button = QPushButton("Manage runtimes")
        self.manage_runtime_button.clicked.connect(self.manage_runtimes)
        self.rescan_runtime_button = QPushButton("Rescan")
        self.rescan_runtime_button.clicked.connect(self.refresh_runtimes)
        runtime_layout.addWidget(runtime_label)
        runtime_layout.addWidget(self.runtime_combo)
        runtime_layout.addWidget(self.runtime_status, 1)
        runtime_layout.addWidget(self.rescan_runtime_button)
        runtime_layout.addWidget(self.manage_runtime_button)
        layout.addWidget(runtime_card)

        report_tools = QHBoxLayout()
        self.report_status = QLabel("")
        self.report_status.setStyleSheet("color:#8295a8;")
        select_missing = QPushButton("Select missing")
        select_missing.clicked.connect(self.select_missing)
        select_all = QPushButton("Select all")
        select_all.clicked.connect(self.select_all)
        report_tools.addWidget(self.report_status)
        report_tools.addStretch()
        report_tools.addWidget(select_missing)
        report_tools.addWidget(select_all)
        layout.addLayout(report_tools)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setSpacing(6)
        layout.addWidget(self.list, 3)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.output, 2)

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        close = QPushButton("Close")
        self.run = QPushButton("Generate selected")
        self.retry = QPushButton("Retry failed")
        self.retry.hide()
        self.run.setObjectName("primaryButton")
        close.clicked.connect(self.reject)
        self.run.clicked.connect(self.start)
        self.retry.clicked.connect(self.retry_failed)
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(close)
        bottom.addWidget(self.retry)
        bottom.addWidget(self.run)
        layout.addLayout(bottom)

        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.finished)
        self.refresh_runtimes()
        self.load_scripts()

    def load_scripts(self):
        self.list.clear()
        available = {path.name: path for path in list_report_scripts()}
        try:
            history = scan_report_index(self.report_dir)
        except Exception:
            history = {}
        policy_discovery = discover_policy_snapshots(self.report_dir)
        policy_index = discovery_index(self.report_dir)
        legacy_count = legacy_configuration_policy_diagnostics(policy_discovery.diagnostics)
        expected_day = expected_business_days(count=1)[0]
        missing_count = 0
        for filename, (icon, title, description, family) in REPORT_CATALOG.items():
            path = available.get(filename)
            if not path:
                continue
            snapshots = history.get(family, [])
            latest = snapshots[-1] if snapshots else None
            if family == CONFIGURATION_POLICY_FAMILY:
                healthy, note = anchor_bundle_status(
                    self.report_dir,
                    family,
                    latest,
                    index=policy_index,
                    legacy_count=legacy_count,
                )
                missing = not healthy or latest is None or latest.captured_at.date() < expected_day
                missing_count += int(missing)
                if latest is None:
                    evidence = f"⚠ Missing {expected_day:%d %b %Y}"
                elif note:
                    evidence = f"{note} · {latest.captured_at:%d %b %Y · %H:%M}"
                elif latest.captured_at.date() < expected_day:
                    evidence = f"⚠ Missing {expected_day:%d %b %Y}"
                else:
                    evidence = f"✓ Latest {latest.captured_at:%d %b %Y · %H:%M}"
            else:
                latest_time = latest.captured_at if latest else None
                missing = latest_time is None or latest_time.date() < expected_day
                missing_count += int(missing)
                if missing:
                    evidence = f"⚠ Missing {expected_day:%d %b %Y}"
                else:
                    evidence = f"✓ Latest {latest_time:%d %b %Y · %H:%M}"
            item = QListWidgetItem(
                f"{icon}  {title}     {evidence}\n"
                f"     {description}\n"
                f"     {filename}"
            )
            item.setData(SCRIPT_PATH_ROLE, str(path))
            item.setData(MISSING_ROLE, missing)
            item.setSizeHint(QSize(0, 78))
            self.list.addItem(item)
        self.report_status.setText(
            f"{missing_count} missing · output to {self.report_dir}"
            if missing_count
            else f"All scheduled reports present · output to {self.report_dir}"
        )

    def refresh_runtimes(self, preferred: Path | bool | None = None):
        preferred_path = preferred if isinstance(preferred, Path) else None
        selected_path = preferred_path or (
            self.pwsh_runtime.path if self.pwsh_runtime else None
        )
        self._refresh_generation += 1
        generation = self._refresh_generation
        self.runtime_status.setText("Scanning in background…")
        self.runtime_combo.setEnabled(False)
        self.rescan_runtime_button.setEnabled(False)
        self.run.setEnabled(False)
        task = BackgroundCall(discover_powershell_runtimes)
        self._tasks.add(task)
        task.signals.succeeded.connect(
            lambda runtimes: self._runtimes_loaded(
                list(runtimes),
                selected_path,
                generation,
            )
        )
        task.signals.failed.connect(self._runtime_scan_failed)
        task.signals.done.connect(lambda: self._tasks.discard(task))
        QThreadPool.globalInstance().start(task)

    def _runtimes_loaded(
        self,
        runtimes: list[PowerShellRuntime],
        selected_path: Path | None,
        generation: int,
    ):
        if generation != self._refresh_generation:
            return
        self.runtimes = runtimes
        active = selected_powershell_runtime(self.runtimes)
        self.runtime_combo.blockSignals(True)
        self.runtime_combo.clear()
        if not self.runtimes:
            self.runtime_combo.addItem("No PowerShell runtime detected", None)
        for runtime in self.runtimes:
            self.runtime_combo.addItem(runtime.label, runtime)
        target = ""
        if selected_path:
            try:
                target = str(selected_path.resolve())
            except OSError:
                target = str(selected_path)
        elif active:
            target = active.identity
        for index, runtime in enumerate(self.runtimes):
            if runtime.identity == target:
                self.runtime_combo.setCurrentIndex(index)
                break
        self.runtime_combo.blockSignals(False)
        self.runtime_combo.setEnabled(True)
        self.rescan_runtime_button.setEnabled(True)
        self.run.setEnabled(True)
        self.runtime_changed()

    def _runtime_scan_failed(self, message: str):
        self.runtime_status.setText(f"Scan failed: {message}")
        self.runtime_combo.setEnabled(True)
        self.rescan_runtime_button.setEnabled(True)
        self.run.setEnabled(True)

    def runtime_changed(self):
        runtime = self.runtime_combo.currentData()
        self.pwsh_runtime = runtime if isinstance(runtime, PowerShellRuntime) else None
        if self.pwsh_runtime:
            select_powershell_runtime(self.pwsh_runtime)
            self.runtime_status.setText(
                f"Ready · {private_module_count(self.pwsh_runtime)} private modules"
                if self.pwsh_runtime.supported
                else "PowerShell 7+ required"
            )
        else:
            self.runtime_status.setText("No runtime detected")

    def manage_runtimes(self):
        dialog = PowerShellManagerDialog(self)
        if dialog.exec() and dialog.selected_runtime:
            self.refresh_runtimes(dialog.selected_runtime.path)
        else:
            self.refresh_runtimes()

    def select_missing(self):
        self.list.clearSelection()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if bool(item.data(MISSING_ROLE)):
                item.setSelected(True)

    def select_all(self):
        self.list.selectAll()

    def append(self, value: str):
        self.output.moveCursor(self.output.textCursor().MoveOperation.End)
        self.output.insertPlainText(value)
        self.output.moveCursor(self.output.textCursor().MoveOperation.End)

    def start(self):
        if not self.pwsh_runtime or not self.pwsh_runtime.supported:
            QMessageBox.warning(
                self,
                "PowerShell required",
                "Choose a PowerShell 7 runtime or add an extracted portable version "
                "with Manage runtimes.",
            )
            return
        selected = [Path(item.data(SCRIPT_PATH_ROLE)) for item in self.list.selectedItems()]
        if not selected:
            QMessageBox.information(self, "Generate reports", "Select at least one report.")
            return
        if private_module_count(self.pwsh_runtime) == 0:
            answer = QMessageBox.question(
                self,
                "Empty PowerShell environment",
                f"PowerShell {self.pwsh_runtime.version} has no private modules yet.\n\n"
                "System and other runtime modules are intentionally hidden, so reports "
                "requiring Microsoft Graph will fail until modules are installed in "
                "Manage runtimes → Modules & console.\n\nRun anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        scopes = sorted({scope for script in selected for scope in graph_scopes(script)})
        scope_text = "\n".join(f"• {scope}" for scope in scopes) or "Scopes are declared by the scripts."
        answer = QMessageBox.question(
            self,
            "Microsoft Graph permissions",
            "The selected reports will sign in to Microsoft Graph and request these delegated permissions:\n\n"
            f"{scope_text}\n\nContinue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.queue = selected
        self.failures = []
        self.current_index = -1
        self.output.clear()
        self.retry.hide()
        self.progress.setRange(0, len(selected))
        self.progress.setValue(0)
        self.run.setEnabled(False)
        self.runtime_combo.setEnabled(False)
        self.rescan_runtime_button.setEnabled(False)
        self.manage_runtime_button.setEnabled(False)
        self.run_next()

    def run_next(self):
        self.current_index += 1
        if self.current_index >= len(self.queue):
            if self.failures:
                self.append(
                    f"\n⚠ Completed with {len(self.failures)} failed report"
                    f"{'s' if len(self.failures) != 1 else ''}. "
                    "Review the output and retry when ready.\n"
                )
                self.retry.show()
            else:
                self.append("\n✓ All selected reports finished successfully.\n")
            self.run.setEnabled(True)
            self.runtime_combo.setEnabled(True)
            self.rescan_runtime_button.setEnabled(True)
            self.manage_runtime_button.setEnabled(True)
            self.load_scripts()
            return
        script = self.queue[self.current_index]
        self.progress.setValue(self.current_index)
        environment = QProcessEnvironment()
        for key, value in powershell_environment(self.pwsh_runtime).items():
            environment.insert(key, value)
        environment.insert("REPORTS_DIR", str(self.report_dir))
        environment.insert("DIFFASAURUS_SCRIPT_PATH", str(script))
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(project_root()))
        self.append(f"\n▶ {script.name}\n")
        self.process.start(
            str(self.pwsh_runtime.path),
            (
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                REPORT_COMMAND,
            ),
        )
        if not self.process.waitForStarted(3000):
            self.append("Could not start PowerShell.\n")
            if script not in self.failures:
                self.failures.append(script)
            self.run_next()

    def read_stdout(self):
        self.append(bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace"))

    def read_stderr(self):
        self.append(bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace"))

    def finished(self, exit_code: int, _status):
        self.append(f"\n[exit {exit_code}]\n")
        if exit_code != 0 and self.queue[self.current_index] not in self.failures:
            self.failures.append(self.queue[self.current_index])
        self.progress.setValue(self.current_index + 1)
        self.run_next()

    def retry_failed(self):
        if not self.failures:
            return
        paths = {str(path) for path in self.failures}
        self.list.clearSelection()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(SCRIPT_PATH_ROLE) in paths:
                item.setSelected(True)
        self.start()
