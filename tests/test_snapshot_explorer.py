import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.models.csv_model import CsvTableModel, read_csv_table
from diffasaurus.models.proxies import CsvFilterProxy
from diffasaurus.ui.multi_column_filter import collect_distinct_values
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer, load_snapshot_payload


class SnapshotExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_csv_reader_detects_delimiter_and_normalizes_short_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.csv"
            path.write_text(
                "Name;State;City\nAlice;Enabled;Paris\nBob;Disabled\n",
                encoding="utf-8",
            )
            headers, rows, delimiter = read_csv_table(path)

        self.assertEqual(delimiter, ";")
        self.assertEqual(headers, ["Name", "State", "City"])
        self.assertEqual(rows[1], ["Bob", "Disabled", ""])

    def test_search_and_column_filters_combine(self):
        model = CsvTableModel(
            ["Name", "State", "City"],
            [
                ["Alice Martin", "Enabled", "Paris"],
                ["Alice Smith", "Disabled", "London"],
                ["Bob Jones", "Enabled", "Paris"],
                ["Charlie", "Enabled", ""],
            ],
        )
        proxy = CsvFilterProxy()
        proxy.setSourceModel(model)
        proxy.set_smart_search_columns([0])
        proxy.set_search_text("alice")
        proxy.set_column_allowed_values(2, {"Paris"})
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(proxy.data(proxy.index(0, 0)), "Alice Martin")

        proxy.set_search_text("")
        proxy.set_column_allowed_values(1, {"Enabled"})
        self.assertEqual(proxy.rowCount(), 2)

    def test_distinct_values_include_blank_once(self):
        model = CsvTableModel(
            ["State"],
            [["Enabled"], [""], ["Enabled"], ["Disabled"], [""]],
        )
        self.assertEqual(
            collect_distinct_values(model, 0),
            ["Disabled", "Enabled", ""],
        )

    def test_registry_uses_specific_and_generic_dashboards(self):
        identity = CsvTableModel(
            ["DisplayName", "AccountEnabled", "UserType"],
            [["Adele", "True", "Member"], ["Alex", "False", "Guest"]],
        )
        title, stats = get_dashboard_definition(identity, list(identity.headers))
        self.assertEqual(title, "Identity Dashboard")
        self.assertTrue(any(card["title"] == "Enabled" for card in stats))

        generic = CsvTableModel(
            ["UnknownA", "UnknownB"],
            [["one", ""], ["two", "value"]],
        )
        title, stats = get_dashboard_definition(generic, list(generic.headers))
        self.assertEqual(title, "Snapshot Dashboard")
        self.assertTrue(any(card["title"] == "Completeness" for card in stats))

    def test_membership_schema_is_not_misclassified_as_identity(self):
        memberships = CsvTableModel(
            [
                "UserPrincipalName",
                "UserType",
                "AccountEnabled",
                "GroupName",
                "GroupId",
                "GroupType",
                "MembershipType",
            ],
            [
                [
                    "adele@example.com",
                    "Member",
                    "True",
                    "All Staff",
                    "group-1",
                    "Security",
                    "Dynamic",
                ],
                [
                    "alex@example.com",
                    "Guest",
                    "False",
                    "Guests",
                    "group-2",
                    "Microsoft365",
                    "Assigned",
                ],
            ],
        )
        title, stats = get_dashboard_definition(
            memberships,
            list(memberships.headers),
        )
        self.assertEqual(title, "Group Memberships Dashboard")
        self.assertTrue(any(card["title"] == "Unique groups" for card in stats))

    def test_snapshot_payload_builds_dashboard_off_ui_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Entra_Users_Properties_20260101-010000.csv"
            path.write_text(
                "DisplayName,AccountEnabled,UserType\n"
                "Adele,True,Member\nAlex,False,Guest\n",
                encoding="utf-8",
            )
            headers, rows, _delimiter, title, stats = load_snapshot_payload(path)

        self.assertEqual(len(headers), 3)
        self.assertEqual(len(rows), 2)
        self.assertEqual(title, "Identity Dashboard")
        enabled = next(card for card in stats if card["title"] == "Enabled")
        self.assertEqual(enabled["value"], 1)

    def test_dashboard_card_filter_switches_to_matching_table_rows(self):
        explorer = SnapshotExplorer()
        try:
            explorer.model.set_table(
                ["DisplayName", "AccountEnabled"],
                [["Adele", "True"], ["Alex", "False"], ["Allan", "True"]],
            )
            explorer.apply_dashboard_filter(
                {"filter_spec": {"AccountEnabled": ["False"]}}
            )
            self.assertEqual(explorer.views.currentIndex(), 0)
            self.assertEqual(explorer.proxy.rowCount(), 1)
            self.assertEqual(
                explorer.proxy.data(explorer.proxy.index(0, 0)),
                "Alex",
            )
        finally:
            explorer.close()
            explorer.thread_pool.waitForDone(1_000)


if __name__ == "__main__":
    unittest.main()
