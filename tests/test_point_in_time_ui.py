import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.types import CanonicalEntityKey, EntityRecord, TimedAlias
from diffasaurus.ui.entity_history import EntityHistoryPage
from diffasaurus.ui.entity_search import EntitySelectorPanel
from diffasaurus.ui.point_in_time import PointInTimePage, PRESENCE_PARTIAL_COPY
from tests.fixtures.entity_index_generator import write_report


class PointInTimeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_point_in_time_page_has_splitter(self):
        page = PointInTimePage()
        self.assertIsNotNone(page.splitter)
        self.assertGreaterEqual(page.splitter.handleWidth(), 1)

    def test_timezone_disclaimer_visible(self):
        page = PointInTimePage()
        self.assertIn("no timezone offset", page.datetime_selector.disclaimer.text())

    def test_partial_presence_copy_constant(self):
        self.assertIn("primary inventory", PRESENCE_PARTIAL_COPY.lower())

    def test_view_at_date_button_on_entity_history(self):
        page = EntityHistoryPage()
        self.assertEqual(page.view_at_date_button.text(), "View at date")
        self.assertFalse(page.view_at_date_button.isEnabled())

    def test_entity_history_handoff_preserves_key(self):
        history = EntityHistoryPage()
        pit = PointInTimePage()
        record = EntityRecord(
            key=CanonicalEntityKey("user", "user-1"),
            display_name="Ada",
        )
        history._select_entity(record)
        pit.select_entity(record, datetime(2026, 8, 4, 12, 0, 0))
        self.assertEqual(pit.entity_selector.selected.key, record.key)

    def _build_repository(self, root: Path) -> EntityIndexRepository:
        os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
        run_sync(root, cold=True)
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
        repo = EntityIndexRepository.open(root)
        assert repo is not None
        return repo

    def test_single_exact_result_auto_selects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            repo = self._build_repository(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            panel.search_input.setText("ada@example.com")
            panel._run_search()
            self.assertIsNotNone(panel.selected)
            self.assertEqual(panel.selected.key, CanonicalEntityKey("user", "user-1"))
            repo.close()

    def test_multiple_results_require_explicit_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {"Id": "user-1", "UPN": "ada.one@example.com", "DisplayName": "Ada One"},
                    {"Id": "user-2", "UPN": "ada.two@example.com", "DisplayName": "Ada Two"},
                ],
            )
            repo = self._build_repository(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            panel.search_input.setText("ada")
            panel._run_search()
            self.assertIsNone(panel.selected)
            self.assertGreater(panel.disambiguation.count(), 1)
            repo.close()

    def test_selecting_second_result_stores_canonical_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {"Id": "user-1", "UPN": "ada.one@example.com", "DisplayName": "Ada One"},
                    {"Id": "user-2", "UPN": "ada.two@example.com", "DisplayName": "Ada Two"},
                ],
            )
            repo = self._build_repository(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            panel.search_input.setText("ada")
            panel._run_search()
            second = panel.disambiguation.item(1)
            panel._pick_disambiguation(second)
            key = second.data(Qt.ItemDataRole.UserRole)
            self.assertIsInstance(key, CanonicalEntityKey)
            self.assertEqual(panel.selected.key, key)
            repo.close()

    def test_editing_query_clears_selected_entity(self):
        panel = EntitySelectorPanel()
        record = EntityRecord(
            key=CanonicalEntityKey("user", "user-1"),
            display_name="Ada",
        )
        panel._select_entity(record)
        panel.search_input.setText("changed")
        self.assertIsNone(panel.selected)

    def test_reconstruct_disabled_until_selection(self):
        page = PointInTimePage()
        self.assertFalse(page.reconstruct_button.isEnabled())
        record = EntityRecord(
            key=CanonicalEntityKey("user", "user-1"),
            display_name="Ada",
        )
        page._on_entity_selected(record)
        self.assertTrue(page.reconstruct_button.isEnabled())

    def test_reconstruct_uses_selected_key_not_first_result(self):
        pit = PointInTimePage()
        selected = EntityRecord(
            key=CanonicalEntityKey("user", "user-2"),
            display_name="Ada Two",
        )
        pit._on_entity_selected(selected)
        emitted: list[tuple[EntityRecord, datetime]] = []

        def _capture(record, target):
            emitted.append((record, target))

        pit.reconstruct_requested.connect(_capture)
        pit.reconstruct_button.click()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0].key, CanonicalEntityKey("user", "user-2"))

    def test_period_changes_and_reconstruct_without_sqlite_thread_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada Updated"}],
            )
            repo = self._build_repository(root)
            key = CanonicalEntityKey("user", "user-1")
            target = datetime(2026, 8, 1, 1, 0, 0)

            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as executor:
                period_future = executor.submit(repo.period_changes, key, timedelta(days=30))
                reconstruct_future = executor.submit(repo.reconstruct_state, key, target)
                period_future.result()
                reconstruct_future.result()
            repo.close()


if __name__ == "__main__":
    unittest.main()
