import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.core.session_state import save_pre_update_session
from diffasaurus.ui.main_window import DiffasaurusWindow
from diffasaurus.ui.navigation_pages import PAGE_SNAPSHOT_EXPLORER
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer
from tools.validate_sparkle_bundle import validate_app_bundle, validate_sparkle_info_plist


class SessionRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_snapshot_explorer_restore_helpers(self):
        explorer = SnapshotExplorer()
        snapshot = ReportSnapshot(
            path=Path("/tmp/users.csv"),
            family="users",
            captured_at=datetime(2026, 1, 1, 12, 0, 0),
            row_count=1,
            headers=("id",),
        )
        explorer.set_family("users")
        explorer.set_snapshots([snapshot])
        explorer.show_view(1)
        self.assertEqual(explorer.current_view_mode(), "dashboard")
        self.assertTrue(explorer.restore_snapshot_path(str(snapshot.path)))
        explorer.restore_view_mode("table")
        self.assertEqual(explorer.current_view_mode(), "table")

    def test_main_window_restores_page_family_snapshot_and_view(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            reports = base / "reports"
            reports.mkdir()
            snapshot_path = reports / "users.csv"
            snapshot_path.write_text("id\n1\n", encoding="utf-8")
            save_pre_update_session(
                page=PAGE_SNAPSHOT_EXPLORER,
                report_family="users",
                snapshot_path=str(snapshot_path),
                explorer_view="dashboard",
                base_dir=base / "userdata",
            )
            with patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=reports):
                with patch("diffasaurus.core.session_state.user_data_dir", return_value=base / "userdata"):
                    with patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                        window = DiffasaurusWindow()
                        window._screen_fitted = True
                        snapshot = ReportSnapshot(
                            path=snapshot_path,
                            family="users",
                            captured_at=datetime(2026, 1, 1, 12, 0, 0),
                            row_count=1,
                            headers=("id",),
                        )
                        window.families = {"users": [snapshot]}
                        window.family_combo.clear()
                        window.family_combo.addItems(window.families)
                        window._restore_pre_update_session_if_needed()
                    self.assertEqual(window.stack.currentIndex(), PAGE_SNAPSHOT_EXPLORER)
                    self.assertEqual(window.family_combo.currentText(), "users")
                    self.assertEqual(window.snapshot_explorer.current_view_mode(), "dashboard")
                    window.close()
                    window.thread_pool.waitForDone(2_000)


class SparkleBundleValidationTests(unittest.TestCase):
    def test_missing_framework_paths_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            app_path = Path(directory) / "Diffasaurus.app"
            app_path.mkdir()
            (app_path / "Contents").mkdir()
            errors = validate_app_bundle(app_path)
            self.assertTrue(errors)

    def test_valid_sparkle_info_plist_passes(self):
        errors = validate_sparkle_info_plist(
            {
                "SUFeedURL": "https://cloudbydefault.github.io/diffasaurus/appcast-preview.xml",
                "SUPublicEDKey": "public-key",
                "SUEnableAutomaticChecks": False,
                "SUAutomaticallyUpdate": False,
                "SUVerifyUpdateBeforeExtraction": True,
            }
        )
        self.assertEqual(errors, [])

    def test_missing_pre_extraction_verification_fails(self):
        errors = validate_sparkle_info_plist(
            {
                "SUFeedURL": "https://example.test/appcast.xml",
                "SUPublicEDKey": "public-key",
                "SUEnableAutomaticChecks": False,
                "SUAutomaticallyUpdate": False,
            }
        )
        self.assertIn("Info.plist SUVerifyUpdateBeforeExtraction must be true", errors)

    def test_signed_feed_requirement_is_rejected(self):
        errors = validate_sparkle_info_plist(
            {
                "SUFeedURL": "https://example.test/appcast.xml",
                "SUPublicEDKey": "public-key",
                "SUEnableAutomaticChecks": False,
                "SUAutomaticallyUpdate": False,
                "SUVerifyUpdateBeforeExtraction": True,
                "SURequireSignedFeed": True,
            }
        )
        self.assertIn(
            "Info.plist must not set SURequireSignedFeed in this updater version",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
