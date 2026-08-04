import os
import unittest
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.entity.types import CanonicalEntityKey, EntityRecord, TimedAlias
from diffasaurus.ui.entity_history import EntityHistoryPage
from diffasaurus.ui.point_in_time import PointInTimePage, PRESENCE_PARTIAL_COPY


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


if __name__ == "__main__":
    unittest.main()
