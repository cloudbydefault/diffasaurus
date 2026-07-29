import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.powershell_runtime import PowerShellRuntime
from diffasaurus.ui.report_runner import REPORT_CATALOG, RunScriptsDialog


class ReportRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_runtime_selector_shows_discovered_version(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = PowerShellRuntime(
                Path(directory) / "pwsh",
                "7.5.4",
                "System",
                "Arm64",
            )
            with (
                patch(
                    "diffasaurus.ui.report_runner.get_active_reports_dir",
                    return_value=Path(directory),
                ),
                patch(
                    "diffasaurus.ui.report_runner.discover_powershell_runtimes",
                    return_value=[runtime],
                ),
                patch(
                    "diffasaurus.ui.report_runner.selected_powershell_runtime",
                    return_value=runtime,
                ),
                patch(
                    "diffasaurus.ui.report_runner.select_powershell_runtime",
                ) as select_runtime,
            ):
                dialog = RunScriptsDialog()
                self.assertIn("PowerShell 7.5.4", dialog.runtime_combo.currentText())
                self.assertEqual(dialog.pwsh_runtime, runtime)
                self.assertEqual(dialog.runtime_status.text(), "Ready")
                select_runtime.assert_called_with(runtime)
                dialog.close()

    def test_every_catalog_report_honors_selected_report_folder(self):
        root = Path(__file__).resolve().parents[1]
        for filename in REPORT_CATALOG:
            text = (root / "psscripts" / filename).read_text(
                encoding="utf-8",
                errors="ignore",
            )
            self.assertIn(
                "$env:REPORTS_DIR",
                text,
                f"{filename} must write recovery snapshots to the active history folder",
            )


if __name__ == "__main__":
    unittest.main()
