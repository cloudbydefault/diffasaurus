import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.entity.resolution import EntityIndexCancelled, EntityResolver
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.ui.main_window import DiffasaurusWindow, _build_entity_index_task


class EntityIndexWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, *, persistent: bool = False) -> DiffasaurusWindow:
        with patch.object(DiffasaurusWindow, "refresh_history"):
            with patch(
                "diffasaurus.ui.main_window.persistent_entity_index_enabled",
                return_value=persistent,
            ):
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

    def test_success_clears_indexing_message(self):
        window = self._window()
        window._entity_index_building = True
        window._entity_index_generation = 1
        window.entity_history_page.show_indexing()
        window.point_in_time_page.show_indexing()
        resolver = EntityResolver()
        window._entity_index_ready(1, (resolver, None))
        self.assertFalse(window._entity_index_building)
        self.assertIn(
            "Index ready",
            window.entity_history_page.entity_selector.status_label.text(),
        )
        self.assertIn(
            "Index ready",
            window.point_in_time_page.entity_selector.status_label.text(),
        )

    def test_ensure_index_skips_when_resolver_is_current(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_resolver = resolver
        window._entity_resolver_index_generation = window._index_generation
        with patch.object(window, "_start_entity_index_build") as start:
            window._ensure_entity_index()
            start.assert_not_called()

    def test_ensure_index_coalesces_duplicate_report_refresh(self):
        window = self._window()
        window._entity_index_building = True
        window._entity_index_target_report_generation = window._index_generation
        with patch.object(window, "_start_entity_index_build") as start:
            window._ensure_entity_index(force=True)
            start.assert_not_called()

    def test_user_refresh_restarts_build(self):
        window = self._window()
        window._entity_index_building = True
        window._entity_index_target_report_generation = window._index_generation
        with patch.object(window, "_start_entity_index_build") as start:
            window._ensure_entity_index(force=True, user_requested=True)
            start.assert_called_once_with(force=True)

    def test_page_switch_uses_ensure_not_force_rebuild(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_resolver = resolver
        window._entity_resolver_index_generation = window._index_generation
        with patch.object(window, "_start_entity_index_build") as start:
            window.show_page(2)
            start.assert_not_called()

    def test_stale_generation_result_is_ignored_without_breaking_current_build(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_index_generation = 2
        window._entity_index_building = True
        window._entity_index_ready(1, (resolver, None))
        self.assertIsNone(window._entity_resolver)
        self.assertTrue(window._entity_index_building)

    def test_valid_current_generation_result_is_accepted(self):
        window = self._window()
        resolver = EntityResolver()
        window._entity_index_generation = 4
        window._entity_index_building = True
        window._entity_index_ready(4, (resolver, None))
        self.assertIs(window._entity_resolver, resolver)
        self.assertFalse(window._entity_index_building)

    def test_exception_clears_building_state(self):
        window = self._window()
        window._entity_index_building = True
        window._entity_index_generation = 3
        window.entity_history_page.show_indexing()
        with patch("diffasaurus.ui.main_window.QMessageBox.warning"):
            window._entity_index_failed(3, "boom")
        self.assertFalse(window._entity_index_building)
        self.assertIn("boom", window.point_in_time_page.entity_selector.status_label.text())

    def test_cancelled_build_returns_none_and_clears_indexing_ui(self):
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

        window = self._window()
        window._entity_index_building = True
        window._entity_index_generation = 2
        window.entity_history_page.show_indexing()
        window._entity_index_ready(2, None)
        self.assertFalse(window._entity_index_building)
        self.assertFalse(window.entity_history_page.entity_selector.status_label.isVisible())

    def test_close_while_indexing_does_not_leave_building_stuck(self):
        window = self._window()
        window._entity_index_building = True
        from PyQt6.QtGui import QCloseEvent

        event = QCloseEvent()
        window.closeEvent(event)
        self.assertTrue(window._shutdown_requested)
        self.assertFalse(window._entity_index_building)
        window._entity_index_pool.waitForDone(1_000)
        window.thread_pool.waitForDone(1_000)
        window.close()

    def test_legacy_mode_does_not_launch_qprocess(self):
        window = self._window(persistent=False)
        self.assertIsNone(window._entity_index_controller)
        with patch(
            "diffasaurus.ui.entity_index_controller.EntityIndexController"
        ) as controller_cls:
            window._start_entity_index_build(force=True)
            controller_cls.assert_not_called()

    def test_legacy_build_uses_dedicated_pool(self):
        window = self._window()
        with patch.object(window, "_run_entity_index_background") as run:
            window._start_entity_index_build(force=True)
            run.assert_called_once()

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
