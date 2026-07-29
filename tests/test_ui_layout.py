import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.ui.main_window import DiffasaurusWindow


class ResponsiveLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_movement_heading_never_overlaps_timeline_or_bars(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=Path(directory),
            ):
                window = DiffasaurusWindow()
                window._screen_fitted = True
                window.resize(1_000, 760)
                window.show()
                self.app.processEvents()

                timeline = window.line_chart.geometry()
                heading = window.movement_title.geometry()
                movement = window.change_bars.geometry()
                self.assertFalse(timeline.intersects(heading))
                self.assertFalse(heading.intersects(movement))

                window.close()
                window.thread_pool.waitForDone(2_000)


if __name__ == "__main__":
    unittest.main()
