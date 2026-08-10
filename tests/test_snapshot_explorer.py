import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.models.csv_model import CsvTableModel, read_csv_table
from diffasaurus.models.proxies import CsvFilterProxy
from diffasaurus.ui.main_window import DiffasaurusWindow
from diffasaurus.ui.multi_column_filter import collect_distinct_values
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer, load_snapshot_payload


def _make_snapshot(path: Path, family: str = "TestFamily") -> ReportSnapshot:
    return ReportSnapshot(
        path=path,
        family=family,
        captured_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        row_count=1,
        headers=tuple(),
    )


def _write_csv(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _wait_for_snapshot_load(explorer: SnapshotExplorer, timeout_ms: int = 5_000) -> None:
    elapsed = 0
    while explorer.progress.isVisible() and elapsed < timeout_ms:
        QApplication.processEvents()
        QTest.qWait(25)
        elapsed += 25
    explorer.thread_pool.waitForDone(timeout_ms)
    for _ in range(20):
        QApplication.processEvents()
        QTest.qWait(25)


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

    def test_set_snapshots_alone_does_not_start_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.csv"
            _write_csv(path, "Name,State\nAlice,Enabled\n")
            snapshot = _make_snapshot(path)
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([snapshot])
                self.assertEqual(explorer._generation, 0)
                self.assertFalse(explorer.progress.isVisible())
                self.assertIsNone(explorer.loaded_path)
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1_000)

    def test_activate_loads_when_combo_index_stays_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "family_a.csv"
            path_b = Path(directory) / "family_b.csv"
            _write_csv(path_a, "AccessPackage,State\nPkg1,Active\n")
            _write_csv(path_b, "RoleName,Member\nAdmin,Alice\n")
            snapshot_a = _make_snapshot(path_a, "AccessPackages")
            snapshot_b = _make_snapshot(path_b, "RoleAssignments")
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([snapshot_a])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertEqual(list(explorer.model.headers), ["AccessPackage", "State"])

                explorer.set_snapshots([snapshot_b])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertEqual(list(explorer.model.headers), ["RoleName", "Member"])
                self.assertEqual(explorer.loaded_path, path_b)
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1_000)

    def test_changing_snapshot_within_family_updates_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path_old = Path(directory) / "snap_old.csv"
            path_new = Path(directory) / "snap_new.csv"
            _write_csv(path_old, "Name,State\nAlice,Enabled\n")
            _write_csv(path_new, "Name,State\nBob,Disabled\n")
            snapshots = [
                _make_snapshot(path_old, "Users"),
                _make_snapshot(path_new, "Users"),
            ]
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots(snapshots)
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertEqual(explorer.loaded_path, path_new)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 0)),
                    "Bob",
                )

                explorer.snapshot_combo.setCurrentIndex(1)
                _wait_for_snapshot_load(explorer)
                self.assertEqual(explorer.loaded_path, path_old)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 0)),
                    "Alice",
                )
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1_000)

    def test_stale_async_result_cannot_replace_latest_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.csv"
            path_b = Path(directory) / "b.csv"
            _write_csv(path_a, "ColA\nvalue-a\n")
            _write_csv(path_b, "ColB\nvalue-b\n")
            snapshot_a = _make_snapshot(path_a)
            snapshot_b = _make_snapshot(path_b)
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([snapshot_a, snapshot_b])
                explorer.snapshot_combo.setCurrentIndex(0)
                explorer.load_selected()
                stale_generation = explorer._generation
                explorer.snapshot_combo.setCurrentIndex(1)
                explorer.load_selected()
                latest_generation = explorer._generation

                stale_payload = load_snapshot_payload(path_a)
                explorer._snapshot_loaded(stale_generation, snapshot_a, stale_payload)
                self.assertNotEqual(list(explorer.model.headers), ["ColA"])

                latest_payload = load_snapshot_payload(path_b)
                explorer._snapshot_loaded(
                    latest_generation,
                    snapshot_b,
                    latest_payload,
                )
                self.assertEqual(list(explorer.model.headers), ["ColB"])
                self.assertEqual(explorer.loaded_path, path_b)
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1_000)

    def test_filters_cleared_after_snapshot_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.csv"
            path_b = Path(directory) / "b.csv"
            _write_csv(path_a, "Name,State\nAlice,Enabled\n")
            _write_csv(path_b, "Role,Member\nAdmin,Bob\n")
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([_make_snapshot(path_a)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                explorer.search.setText("alice")
                explorer.proxy.set_column_allowed_values(1, {"Enabled"})
                self.assertTrue(explorer.search.text())
                self.assertEqual(explorer.proxy.active_filter_count(), 1)

                explorer.set_snapshots([_make_snapshot(path_b)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertEqual(explorer.search.text(), "")
                self.assertEqual(explorer.proxy.active_filter_count(), 0)
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1_000)

    def test_dashboard_updates_after_snapshot_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.csv"
            _write_csv(
                path,
                "DisplayName,AccountEnabled,UserType\n"
                "Adele,True,Member\nAlex,False,Guest\n",
            )
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([_make_snapshot(path)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertEqual(explorer.dashboard.title.text(), "Identity Dashboard")
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1_000)

    def test_table_dashboard_table_preserves_selected_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.csv"
            _write_csv(path, "DisplayName,AccountEnabled\nAdele,True\n")
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([_make_snapshot(path)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                headers_after_load = list(explorer.model.headers)

                explorer.show_view(1)
                explorer.show_view(0)
                self.assertEqual(list(explorer.model.headers), headers_after_load)
                self.assertEqual(explorer.loaded_path, path)
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1_000)

    def test_family_changed_off_explorer_page_does_not_start_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.csv"
            path_b = Path(directory) / "b.csv"
            _write_csv(path_a, "Name\nAlice\n")
            _write_csv(path_b, "Role\nAdmin\n")
            families = {
                "FamilyA": [_make_snapshot(path_a, "FamilyA")],
                "FamilyB": [_make_snapshot(path_b, "FamilyB")],
            }
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=Path(directory),
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window.families = families
                    window.family_combo.blockSignals(True)
                    window.family_combo.clear()
                    window.family_combo.addItems(list(families))
                    window.family_combo.setCurrentText("FamilyA")
                    window.family_combo.blockSignals(False)
                    window.stack.setCurrentIndex(0)

                    window.family_combo.setCurrentText("FamilyB")
                    window.family_changed()
                    window.snapshot_explorer.thread_pool.waitForDone(200)

                    self.assertEqual(window.snapshot_explorer._generation, 0)
                    self.assertFalse(window.snapshot_explorer.progress.isVisible())
                    self.assertIsNone(window.snapshot_explorer.loaded_path)
                finally:
                    window.close()
                    window.thread_pool.waitForDone(2_000)
                    window.snapshot_explorer.thread_pool.waitForDone(2_000)

    def test_family_changed_on_explorer_page_triggers_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "access.csv"
            path_b = Path(directory) / "roles.csv"
            _write_csv(path_a, "AccessPackage,State\nPkg1,Active\n")
            _write_csv(path_b, "RoleName,Member\nAdmin,Alice\n")
            families = {
                "AccessPackages": [_make_snapshot(path_a, "AccessPackages")],
                "RoleAssignments": [_make_snapshot(path_b, "RoleAssignments")],
            }
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=Path(directory),
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window.families = families
                    window.family_combo.blockSignals(True)
                    window.family_combo.clear()
                    window.family_combo.addItems(list(families))
                    window.family_combo.setCurrentText("AccessPackages")
                    window.family_combo.blockSignals(False)
                    window.family_changed()
                    window.show_page(7)
                    _wait_for_snapshot_load(window.snapshot_explorer)
                    self.assertEqual(
                        list(window.snapshot_explorer.model.headers),
                        ["AccessPackage", "State"],
                    )

                    window.family_combo.setCurrentText("RoleAssignments")
                    window.family_changed()
                    _wait_for_snapshot_load(window.snapshot_explorer)
                    self.assertEqual(
                        list(window.snapshot_explorer.model.headers),
                        ["RoleName", "Member"],
                    )
                    self.assertEqual(window.snapshot_explorer.loaded_path, path_b)
                finally:
                    window.close()
                    window.thread_pool.waitForDone(2_000)
                    window.snapshot_explorer.thread_pool.waitForDone(2_000)


if __name__ == "__main__":
    unittest.main()
