import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.entity.resolution import EntityIndexCancelled, EntityResolver
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.ui.main_window import DiffasaurusWindow, _build_entity_index_task


class EntityIndexWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self) -> DiffasaurusWindow:
        with patch.object(DiffasaurusWindow, "refresh_history"):
            window = DiffasaurusWindow()
        window._screen_fitted = True
        window.families = {"Entra_Users_Properties": []}
        window._index_generation = 5
        return window

    def test_shared_resolver_served_to_both_pages(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_index_generation = 1
        window._entity_index_ready(1, (resolver, None))
        self.assertIs(window.entity_history_page.entity_selector._resolver, resolver)
        self.assertIs(window.point_in_time_page.entity_selector._resolver, resolver)

    def test_ensure_index_skips_when_resolver_is_current(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_resolver = resolver
        window._entity_resolver_index_generation = window._index_generation
        with patch.object(window, "_start_entity_index_build") as start:
            window._ensure_entity_index()
            start.assert_not_called()

    def test_page_switch_uses_ensure_not_force_rebuild(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_resolver = resolver
        window._entity_resolver_index_generation = window._index_generation
        with patch.object(window, "_start_entity_index_build") as start:
            window.show_page(2)
            start.assert_not_called()

    def test_stale_generation_result_is_ignored(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_index_generation = 2
        window._entity_index_ready(1, (resolver, None))
        self.assertIsNone(window._entity_resolver)

    def test_exception_clears_building_state(self):
        window = self._window()
        window._entity_index_building = True
        window._entity_index_generation = 3
        with patch("diffasaurus.ui.main_window.QMessageBox.warning"):
            window._entity_index_failed(3, "boom")
        self.assertFalse(window._entity_index_building)
        self.assertIn("boom", window.point_in_time_page.entity_selector.status_label.text())

    def test_cancelled_build_returns_none(self):
        cancelled = threading.Event()
        cancelled.set()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from tests.test_entity_resolution import write_report

            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            from diffasaurus.core.report_history import scan_report_history

            families = scan_report_history(root)
            result = _build_entity_index_task(families, cancelled)
            self.assertIsNone(result)

    def test_close_while_indexing_does_not_leave_building_stuck(self):
        window = self._window()
        window._entity_index_building = True
        from PyQt6.QtGui import QCloseEvent

        event = QCloseEvent()
        window.closeEvent(event)
        self.assertTrue(window._shutdown_requested)
        self.assertFalse(window._entity_index_building)
        window.thread_pool.waitForDone(1_000)
        window.close()

    def test_build_task_produces_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from tests.test_entity_resolution import write_report

            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "old@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-2",
                        "UPN": "other@example.com",
                        "DisplayName": "Other",
                    }
                ],
            )
            from diffasaurus.core.report_history import scan_report_history

            families = scan_report_history(root)
            cancelled = threading.Event()
            result = _build_entity_index_task(families, cancelled)
            self.assertIsNotNone(result)
            resolver, stats = result
            assert resolver is not None
            deleted = resolver.get(CanonicalEntityKey("user", "user-1"))
            self.assertIsNotNone(deleted)
            assert deleted is not None
            self.assertFalse(deleted.present_in_latest)


if __name__ == "__main__":
    unittest.main()
