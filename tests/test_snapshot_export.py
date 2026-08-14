import csv
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtWidgets import QApplication, QTableView

from diffasaurus.models.csv_model import CsvTableModel
from diffasaurus.models.proxies import CsvFilterProxy
from diffasaurus.ui.snapshot_export import (
    default_export_filename,
    selected_source_rows,
    visible_source_rows,
    write_csv_export,
)


class SnapshotExportHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _table_with_proxy(self, headers, rows):
        model = CsvTableModel(headers, rows)
        proxy = CsvFilterProxy()
        proxy.setSourceModel(model)
        table = QTableView()
        table.setModel(proxy)
        return model, proxy, table

    def test_visible_source_rows_exports_all_unfiltered_rows(self):
        model, proxy, _table = self._table_with_proxy(
            ["Name", "State"],
            [["Charlie", "Enabled"], ["Alice", "Enabled"], ["Bob", "Disabled"]],
        )
        rows = visible_source_rows(proxy, model)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ["Charlie", "Enabled"])

    def test_visible_source_rows_respects_column_filter(self):
        model, proxy, _table = self._table_with_proxy(
            ["Name", "State"],
            [["Charlie", "Enabled"], ["Alice", "Enabled"], ["Bob", "Disabled"]],
        )
        proxy.set_column_allowed_values(1, {"Disabled"})
        rows = visible_source_rows(proxy, model)
        self.assertEqual(rows, [["Bob", "Disabled"]])

    def test_visible_source_rows_respects_search_filter(self):
        model, proxy, _table = self._table_with_proxy(
            ["Name", "State"],
            [["Charlie", "Enabled"], ["Alice", "Enabled"], ["Bob", "Disabled"]],
        )
        proxy.set_search_text("alice")
        rows = visible_source_rows(proxy, model)
        self.assertEqual(rows, [["Alice", "Enabled"]])

    def test_visible_source_rows_follows_display_order_after_sort(self):
        model, proxy, table = self._table_with_proxy(
            ["Name"],
            [["Charlie"], ["Alice"], ["Bob"]],
        )
        table.setSortingEnabled(True)
        table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        rows = visible_source_rows(proxy, model)
        self.assertEqual([row[0] for row in rows], ["Alice", "Bob", "Charlie"])

    def test_selected_source_rows_exports_only_selected_rows_in_display_order(self):
        model, proxy, table = self._table_with_proxy(
            ["Name"],
            [["A"], ["B"], ["C"], ["D"]],
        )
        selection = table.selectionModel()
        assert selection is not None
        selection.select(
            proxy.index(1, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection.select(
            proxy.index(3, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        rows = selected_source_rows(table, proxy, model)
        self.assertEqual([row[0] for row in rows], ["B", "D"])

    def test_selected_source_rows_after_sort_maps_to_correct_source_values(self):
        model, proxy, table = self._table_with_proxy(
            ["Name", "Rank"],
            [["Charlie", "3"], ["Alice", "1"], ["Bob", "2"]],
        )
        table.setSortingEnabled(True)
        table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        selection = table.selectionModel()
        assert selection is not None
        selection.select(
            proxy.index(0, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection.select(
            proxy.index(2, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        rows = selected_source_rows(table, proxy, model)
        self.assertEqual(rows, [["Alice", "1"], ["Charlie", "3"]])

    def test_write_csv_export_round_trips_unicode_and_delimiter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.csv"
            write_csv_export(
                path,
                ["Name", "Note"],
                [["Café", 'quote "one"'], ["Normal", "a;b"]],
                delimiter=";",
            )
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("Café", text)
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(rows[0], ["Name", "Note"])
            self.assertEqual(rows[1], ["Café", 'quote "one"'])
            self.assertEqual(rows[2], ["Normal", "a;b"])

    def test_default_export_filename_uses_snapshot_stem(self):
        path = Path("/tmp/Entra_Group_User_Memberships_20260810_030235.csv")
        self.assertEqual(
            default_export_filename(path, suffix="filtered", fallback="snapshot_filtered.csv"),
            "Entra_Group_User_Memberships_20260810_030235_filtered.csv",
        )
        self.assertEqual(
            default_export_filename(None, suffix="selection", fallback="snapshot_selection.csv"),
            "snapshot_selection.csv",
        )


if __name__ == "__main__":
    unittest.main()
