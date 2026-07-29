import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QProcess, QThreadPool
from PyQt6.QtWidgets import QApplication

from diffasaurus.core.powershell_runtime import (
    PowerShellRuntime,
    probe_powershell_runtime,
)
from diffasaurus.ui.powershell_environment import (
    INSTALL_MODULE_SCRIPT,
    PowerShellEnvironmentDialog,
)
from diffasaurus.ui.powershell_manager import (
    PowerShellManagerDialog,
    discover_runtime_inventory,
)


class PowerShellEnvironmentUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait_for(self, condition, timeout: float = 4) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def _close_dialog(self, dialog):
        QThreadPool.globalInstance().waitForDone(3_000)
        self.app.processEvents()
        dialog.close()
        self.app.processEvents()
        dialog.deleteLater()
        self.app.processEvents()

    def test_runtime_scan_does_not_block_the_ui_thread(self):
        def slow_discovery():
            time.sleep(0.35)
            return []

        with patch(
            "diffasaurus.ui.powershell_manager.discover_powershell_runtimes",
            side_effect=slow_discovery,
        ):
            started = time.monotonic()
            dialog = PowerShellManagerDialog()
            dialog.show()
            self.app.processEvents()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.15)
            self.assertTrue(dialog.progress.isVisible())
            self.assertIn("background", dialog.status.text())
            self._close_dialog(dialog)

    def test_portable_import_does_not_block_the_ui_thread(self):
        runtime = PowerShellRuntime(
            Path("/tmp/portable/pwsh"),
            "7.6.4",
            "Portable",
            "Arm64",
            managed=True,
        )

        def slow_import(_path):
            time.sleep(0.35)
            return runtime

        with (
            patch(
                "diffasaurus.ui.powershell_manager.discover_powershell_runtimes",
                return_value=[],
            ),
            patch(
                "diffasaurus.ui.powershell_manager.QFileDialog.getOpenFileName",
                return_value=("/tmp/download/pwsh", ""),
            ),
            patch(
                "diffasaurus.ui.powershell_manager.import_portable_runtime",
                side_effect=slow_import,
            ),
            patch(
                "diffasaurus.ui.powershell_manager.QMessageBox.information",
            ),
        ):
            dialog = PowerShellManagerDialog()
            dialog.show()
            self.assertTrue(
                self._wait_for(lambda: not dialog.progress.isVisible())
            )
            started = time.monotonic()
            dialog.add_portable()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.15)
            self.assertIn("background", dialog.status.text())
            self._close_dialog(dialog)

    def test_runtime_inventory_contains_both_module_counts(self):
        runtime = PowerShellRuntime(
            Path("/tmp/system/pwsh"),
            "7.5.4",
            "System",
            "Arm64",
        )
        modules = [
            object(),
            object(),
        ]

        with (
            patch(
                "diffasaurus.ui.powershell_manager.discover_powershell_runtimes",
                return_value=[runtime],
            ),
            patch(
                "diffasaurus.ui.powershell_manager.list_installed_modules",
                return_value=modules,
            ),
            patch(
                "diffasaurus.ui.powershell_manager.private_module_count",
                return_value=3,
            ),
        ):
            inventory = discover_runtime_inventory()

        self.assertEqual(inventory, [(runtime, 3, 2)])

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_embedded_console_is_persistent_and_isolated(self):
        runtime = probe_powershell_runtime(Path(shutil.which("pwsh")), "System")
        self.assertIsNotNone(runtime)
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "diffasaurus.core.powershell_environment.powershell_environments_dir",
                return_value=Path(directory) / "environments",
            ):
                dialog = PowerShellEnvironmentDialog(runtime)
                try:
                    self.assertTrue(
                        self._wait_for(
                            lambda: dialog.console_process.state()
                            == QProcess.ProcessState.Running
                            and "PSModulePath:" in dialog.console_output.toPlainText()
                        )
                    )
                    dialog.command.setText(
                        "$DiffasaurusTestValue = 12345678 + 12335679"
                    )
                    dialog.send_command()
                    dialog.command.setText("$DiffasaurusTestValue")
                    dialog.send_command()
                    self.assertTrue(
                        self._wait_for(
                            lambda: "24681357"
                            in dialog.console_output.toPlainText()
                        ),
                        dialog.console_output.toPlainText(),
                    )
                    output = dialog.console_output.toPlainText()
                    self.assertNotIn(".local/share/powershell/Modules", output)
                finally:
                    self._close_dialog(dialog)
                    self.assertEqual(
                        dialog.console_process.state(),
                        QProcess.ProcessState.NotRunning,
                    )

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_module_install_command_is_valid_powershell(self):
        environment = dict(os.environ)
        environment["DIFFASAURUS_TEST_SCRIPT"] = INSTALL_MODULE_SCRIPT
        result = subprocess.run(
            (
                shutil.which("pwsh"),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$errors = $null;"
                "[void][Management.Automation.Language.Parser]::ParseInput("
                "$env:DIFFASAURUS_TEST_SCRIPT,[ref]$null,[ref]$errors);"
                "if ($errors) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }",
            ),
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
