from __future__ import annotations

import re
import os
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QSize, Qt
from PyQt6.QtWidgets import (
    QDialog,
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
    modules_dir,
    powershell_executable,
    project_root,
    reports_dir,
)


REPORT_CATALOG = {
    "_app_ENTRA_Users_Properties.ps1": ("👤", "ENTRA · Identities", "Identity attributes and licenses"),
    "_app_ENTRA_Users_Activity.ps1": ("📈", "ENTRA · User activity", "Sign-ins and inactivity"),
    "_app_ENTRA_Users_AuthenticationMethods.ps1": ("🔐", "ENTRA · Authentication", "MFA and passwordless posture"),
    "_app_ENTRA_Groups_Information.ps1": ("👥", "ENTRA · Groups", "Groups, owners and dependencies"),
    "_app_ENTRA_Groups_User_Memberships.ps1": ("🔗", "ENTRA · User memberships", "Group membership by identity"),
    "_app_ENTRA_Access_Packages.ps1": ("🎟", "ENTRA · Access packages", "Entitlement management packages"),
    "app_ENTRA_AccessPackage_Assignments.ps1": ("🎫", "ENTRA · Package assignments", "Access package assignments"),
    "_app_ENTRA_Role_Assignments.ps1": ("🛡", "ENTRA · Role assignments", "Privileged role governance"),
    "_app_INTUNE_Users_Devices.ps1": ("💻", "INTUNE · Managed devices", "Compliance and activity"),
    "_app_INTUNE_Autopilot_Devices.ps1": ("🚀", "INTUNE · Autopilot", "Autopilot inventory"),
    "_app_INTUNE_Apps_Report.ps1": ("📦", "INTUNE · Applications", "Application inventory"),
    "_app_INTUNE_iOS_Devices.ps1": ("📱", "INTUNE · iOS devices", "iPhone and iPad posture"),
    "app_EXCHANGE_SharedMailboxes_Report.ps1": ("📬", "EXCHANGE · Shared mailboxes", "Permissions and activity"),
}


def graph_scopes(script: Path) -> list[str]:
    text = script.read_text(encoding="utf-8", errors="ignore")
    scopes = re.findall(r"[\"']([A-Za-z][A-Za-z0-9.]+(?:Read|Write|Manage)[A-Za-z0-9.]*)[\"']", text)
    return sorted(set(scopes))


class RunScriptsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate tenant snapshots")
        self.resize(920, 680)
        self.pwsh = powershell_executable()
        self.queue: list[Path] = []
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
        self.run.setObjectName("primaryButton")
        close.clicked.connect(self.reject)
        self.run.clicked.connect(self.start)
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(close)
        bottom.addWidget(self.run)
        layout.addLayout(bottom)

        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.finished)
        self.load_scripts()

    def load_scripts(self):
        self.list.clear()
        available = {path.name: path for path in list_report_scripts()}
        for filename, (icon, title, description) in REPORT_CATALOG.items():
            path = available.get(filename)
            if not path:
                continue
            item = QListWidgetItem(f"{icon}  {title}\n     {description}\n     {filename}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setSizeHint(QSize(0, 78))
            self.list.addItem(item)

    def append(self, value: str):
        self.output.moveCursor(self.output.textCursor().MoveOperation.End)
        self.output.insertPlainText(value)
        self.output.moveCursor(self.output.textCursor().MoveOperation.End)

    def start(self):
        if not self.pwsh:
            QMessageBox.warning(
                self,
                "PowerShell required",
                "PowerShell 7 was not found. Install pwsh or place a portable runtime in the pwsh folder.",
            )
            return
        selected = [Path(item.data(Qt.ItemDataRole.UserRole)) for item in self.list.selectedItems()]
        if not selected:
            QMessageBox.information(self, "Generate reports", "Select at least one report.")
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
        self.current_index = -1
        self.output.clear()
        self.progress.setRange(0, len(selected))
        self.progress.setValue(0)
        self.run.setEnabled(False)
        self.run_next()

    def run_next(self):
        self.current_index += 1
        if self.current_index >= len(self.queue):
            self.append("\n✓ All selected reports finished.\n")
            self.run.setEnabled(True)
            self.accept()
            return
        script = self.queue[self.current_index]
        self.progress.setValue(self.current_index)
        environment = QProcessEnvironment.systemEnvironment()
        existing_modules = environment.value("PSModulePath")
        embedded = str(modules_dir())
        environment.insert(
            "PSModulePath",
            embedded + (f"{os.pathsep}{existing_modules}" if existing_modules else ""),
        )
        environment.insert("REPORTS_DIR", str(reports_dir()))
        environment.insert("POWERSHELL_TELEMETRY_OPTOUT", "1")
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(project_root()))
        self.append(f"\n▶ {script.name}\n")
        self.process.start(
            str(self.pwsh),
            (
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ),
        )
        if not self.process.waitForStarted(3000):
            self.append("Could not start PowerShell.\n")
            self.run_next()

    def read_stdout(self):
        self.append(bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace"))

    def read_stderr(self):
        self.append(bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace"))

    def finished(self, exit_code: int, _status):
        self.append(f"\n[exit {exit_code}]\n")
        self.progress.setValue(self.current_index + 1)
        self.run_next()
