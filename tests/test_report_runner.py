import os
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.powershell_runtime import PowerShellRuntime
from diffasaurus.ui.report_runner import REPORT_CATALOG, RunScriptsDialog
from tests.fixtures.configuration_policy_comparison import (
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)
from tools.configuration_policy_inventory import EXPORT_STATUS_COMPLETE, EXPORT_STATUS_INCOMPLETE

POLICY_SCRIPT = "app_INTUNE_ConfigurationPolicy.ps1"
POLICY_TITLE = REPORT_CATALOG[POLICY_SCRIPT][1]
EXPECTED_DAY = date(2026, 8, 14)
CAPTURED_AT_UTC = "2026-08-14T01:00:00.0000000Z"
SNAPSHOT_ID = "Intune_ConfigurationPolicies_20260814-010000"
STALE_SNAPSHOT_ID = "Intune_ConfigurationPolicies_20260801-010000"
STALE_CAPTURED_AT_UTC = "2026-08-01T01:00:00.0000000Z"


class ReportRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _write_anchor(self, root: Path, snapshot_id: str, captured_at: str) -> None:
        (root / f"{snapshot_id}.csv").write_text(
            "SnapshotId,CapturedAtUtc,PolicyId,PolicyName\n"
            f"{snapshot_id},{captured_at},policy-1,Synthetic\n",
            encoding="utf-8-sig",
        )

    def _build_policy_snapshot(
        self,
        root: Path,
        *,
        snapshot_id: str,
        captured_at_utc: str,
        export_status: str = EXPORT_STATUS_COMPLETE,
    ) -> None:
        rel = "Windows/Modern/P__policy-1.json"
        doc = build_basic_modern_policy_document()
        row = build_modern_inventory_row(
            policy_id="policy-1",
            policy_name="Synthetic Policy",
            json_relative_path=rel,
        )
        build_comparison_bundle(
            root,
            snapshot_id=snapshot_id,
            captured_at_utc=captured_at_utc,
            policies=[(rel, doc, row)],
            export_status=export_status,
        )
        self._write_anchor(root, snapshot_id, captured_at_utc)

    def _open_dialog(self, report_dir: Path) -> RunScriptsDialog:
        runtime = PowerShellRuntime(
            report_dir / "pwsh",
            "7.5.4",
            "System",
            "Arm64",
        )
        with (
            patch(
                "diffasaurus.ui.report_runner.get_active_reports_dir",
                return_value=report_dir,
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
            ),
            patch(
                "diffasaurus.ui.report_runner.expected_business_days",
                return_value=[EXPECTED_DAY],
            ),
            patch(
                "diffasaurus.core.powershell_environment.powershell_environments_dir",
                return_value=report_dir / "environments",
            ),
        ):
            dialog = RunScriptsDialog()
            deadline = time.monotonic() + 2
            while not dialog.runtime_combo.currentText() and time.monotonic() < deadline:
                self.app.processEvents()
            return dialog

    def _policy_item_text(self, dialog: RunScriptsDialog) -> str:
        for row in range(dialog.list.count()):
            item = dialog.list.item(row)
            if POLICY_TITLE in item.text():
                return item.text()
        self.fail("Configuration policies row not found in Generate Reports dialog")

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
                patch(
                    "diffasaurus.core.powershell_environment.powershell_environments_dir",
                    return_value=Path(directory) / "environments",
                ),
            ):
                dialog = RunScriptsDialog()
                deadline = time.monotonic() + 2
                while not dialog.runtime_combo.currentText() and time.monotonic() < deadline:
                    self.app.processEvents()
                self.assertIn("PowerShell 7.5.4", dialog.runtime_combo.currentText())
                self.assertEqual(dialog.pwsh_runtime, runtime)
                self.assertEqual(
                    dialog.runtime_status.text(),
                    "Ready · 0 private modules",
                )
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

    def test_configuration_policy_load_scripts_shows_latest_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._build_policy_snapshot(
                root,
                snapshot_id=SNAPSHOT_ID,
                captured_at_utc=CAPTURED_AT_UTC,
            )
            dialog = self._open_dialog(root)
            try:
                text = self._policy_item_text(dialog)
                captured = datetime(2026, 8, 14, 1, 0, 0)
                self.assertIn("✓ Latest", text)
                self.assertIn(captured.strftime("%d %b %Y · %H:%M"), text)
            finally:
                dialog.close()

    def test_configuration_policy_load_scripts_shows_bundle_note_with_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._build_policy_snapshot(
                root,
                snapshot_id=SNAPSHOT_ID,
                captured_at_utc=CAPTURED_AT_UTC,
                export_status=EXPORT_STATUS_INCOMPLETE,
            )
            dialog = self._open_dialog(root)
            try:
                text = self._policy_item_text(dialog)
                captured = datetime(2026, 8, 14, 1, 0, 0)
                self.assertIn("⚠ Incomplete bundle", text)
                self.assertIn(captured.strftime("%d %b %Y · %H:%M"), text)
            finally:
                dialog.close()

    def test_configuration_policy_load_scripts_missing_snapshot_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dialog = self._open_dialog(root)
            try:
                text = self._policy_item_text(dialog)
                self.assertIn(f"⚠ Missing {EXPECTED_DAY:%d %b %Y}", text)
            finally:
                dialog.close()

    def test_configuration_policy_load_scripts_stale_snapshot_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._build_policy_snapshot(
                root,
                snapshot_id=STALE_SNAPSHOT_ID,
                captured_at_utc=STALE_CAPTURED_AT_UTC,
            )
            dialog = self._open_dialog(root)
            try:
                text = self._policy_item_text(dialog)
                self.assertIn(f"⚠ Missing {EXPECTED_DAY:%d %b %Y}", text)
                self.assertNotIn("✓ Latest", text)
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
