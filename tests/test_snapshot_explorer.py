import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.core.user_dashboards import (
    ColumnFilterSpec,
    SortSpec,
    UserDashboard,
    UserDashboardStore,
)
from diffasaurus.ui.user_dashboard_filters import (
    capture_current_view,
    configure_proxy_for_dashboard,
)
from diffasaurus.models.csv_model import CsvTableModel, read_csv_table
from diffasaurus.models.proxies import CsvFilterProxy
from diffasaurus.ui.main_window import DiffasaurusWindow
from diffasaurus.ui.multi_column_filter import collect_distinct_values
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer, load_snapshot_payload, LARGE_SNAPSHOT_ROW_THRESHOLD
from diffasaurus.ui.user_dashboard_workspace import (
    UserDashboardCard,
    user_dashboard_card_menu_stylesheet,
)
from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY


def _drain_qt(*, thread_pools: list | None = None, events: int = 20) -> None:
    for pool in thread_pools or []:
        pool.waitForDone(5_000)
    for _ in range(events):
        QApplication.processEvents()
        time.sleep(0.02)


def _close_explorer(explorer: SnapshotExplorer) -> None:
    explorer.close()
    _drain_qt(thread_pools=[explorer.thread_pool])
    explorer.deleteLater()
    _drain_qt()


def _close_main_window(window: DiffasaurusWindow) -> None:
    pools = [window.thread_pool, window._entity_index_pool]
    if hasattr(window, "snapshot_explorer"):
        pools.append(window.snapshot_explorer.thread_pool)
    window.close()
    _drain_qt(thread_pools=pools)
    window.deleteLater()
    _drain_qt()


@contextmanager
def _isolated_main_window(*, report_dir: Path):
    with (
        patch(
            "diffasaurus.ui.main_window.get_active_reports_dir",
            return_value=report_dir,
        ),
        patch.object(DiffasaurusWindow, "refresh_history", lambda self: None),
        patch(
            "diffasaurus.ui.main_window.persistent_entity_index_enabled",
            return_value=False,
        ),
        patch.object(
            DiffasaurusWindow,
            "_request_persistent_entity_sync",
            lambda *args, **kwargs: None,
        ),
    ):
        window = DiffasaurusWindow()
        window.report_dir = report_dir
        try:
            yield window
        finally:
            _close_main_window(window)


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


MEMBERSHIP_HEADERS = [
    "UserPrincipalName",
    "UserDisplayName",
    "UserMail",
    "UserId",
    "UserType",
    "AccountEnabled",
    "GroupId",
    "GroupDisplayName",
    "GroupMail",
    "GroupType",
    "MembershipType",
    "AssignedDate",
    "Source",
    "Department",
    "JobTitle",
    "Office",
    "Notes",
]


def _membership_row(index: int) -> list[str]:
    group = index % 25
    return [
        f"user{index:05d}@example.com",
        f"User {index:05d}",
        f"user{index:05d}@example.com",
        f"user-id-{index:05d}",
        "Member",
        "True",
        f"group-id-{group:03d}",
        f"Group {group:03d}",
        f"group{group:03d}@example.com",
        "Security",
        "Assigned",
        "2026-01-01",
        "Entra",
        "Engineering",
        "Analyst",
        "Paris",
        f"note-{index:05d}",
    ]


def _write_membership_csv(path: Path, row_count: int) -> None:
    lines = [",".join(MEMBERSHIP_HEADERS)]
    for index in range(row_count):
        lines.append(",".join(_membership_row(index)))
    path.write_text("\n".join(lines), encoding="utf-8")


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
            _close_explorer(explorer)

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
                _close_explorer(explorer)

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
                _close_explorer(explorer)

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
                _close_explorer(explorer)

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
                _close_explorer(explorer)

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
                _close_explorer(explorer)

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
                self.assertEqual(
                    explorer.dashboard.builtin_view.title.text(),
                    "Identity Dashboard",
                )
            finally:
                _close_explorer(explorer)

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
                _close_explorer(explorer)

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
            with _isolated_main_window(report_dir=Path(directory)) as window:
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
            with _isolated_main_window(report_dir=Path(directory)) as window:
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

    def test_proxy_sorts_numeric_values_before_text(self):
        model = CsvTableModel(
            ["Value"],
            [["10"], ["2"], ["abc"], ["1"]],
        )
        proxy = CsvFilterProxy()
        proxy.setSourceModel(model)
        proxy.sort(0, Qt.SortOrder.AscendingOrder)
        ordered = [proxy.data(proxy.index(row, 0)) for row in range(proxy.rowCount())]
        self.assertEqual(ordered, ["1", "2", "10", "abc"])

    def test_proxy_sorts_text_case_insensitively(self):
        model = CsvTableModel(
            ["Name"],
            [["bravo"], ["Alpha"], ["charlie"]],
        )
        proxy = CsvFilterProxy()
        proxy.setSourceModel(model)
        proxy.sort(0, Qt.SortOrder.AscendingOrder)
        ordered = [proxy.data(proxy.index(row, 0)) for row in range(proxy.rowCount())]
        self.assertEqual(ordered, ["Alpha", "bravo", "charlie"])

    def test_proxy_sort_cache_resets_on_model_reload(self):
        model = CsvTableModel(["Name"], [["b"], ["a"]])
        proxy = CsvFilterProxy()
        proxy.setSourceModel(model)
        proxy.sort(0, Qt.SortOrder.AscendingOrder)
        self.assertEqual(proxy.data(proxy.index(0, 0)), "a")
        model.set_table(["Name"], [["z"], ["y"]])
        proxy.sort(0, Qt.SortOrder.DescendingOrder)
        self.assertEqual(proxy.data(proxy.index(0, 0)), "z")

    def test_large_snapshot_preserves_source_order_on_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memberships.csv"
            _write_membership_csv(path, 150)
            explorer = SnapshotExplorer()
            try:
                with patch(
                    "diffasaurus.ui.snapshot_explorer.LARGE_SNAPSHOT_ROW_THRESHOLD",
                    100,
                ):
                    explorer.set_snapshots([_make_snapshot(path, "Entra_Group_User_Memberships")])
                    explorer.activate()
                    _wait_for_snapshot_load(explorer)
                self.assertEqual(explorer.model.rowCount(), 150)
                self.assertEqual(explorer.proxy.rowCount(), 150)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 0)),
                    "user00000@example.com",
                )
            finally:
                _close_explorer(explorer)

    def test_large_snapshot_explicit_sort_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memberships.csv"
            _write_membership_csv(path, 150)
            explorer = SnapshotExplorer()
            try:
                with patch(
                    "diffasaurus.ui.snapshot_explorer.LARGE_SNAPSHOT_ROW_THRESHOLD",
                    100,
                ):
                    explorer.set_snapshots([_make_snapshot(path, "Entra_Group_User_Memberships")])
                    explorer.activate()
                    _wait_for_snapshot_load(explorer)
                explorer.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 0)),
                    "user00000@example.com",
                )
                explorer.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 0)),
                    "user00149@example.com",
                )
            finally:
                _close_explorer(explorer)

    def test_large_snapshot_search_and_filter_still_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memberships.csv"
            _write_membership_csv(path, 150)
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([_make_snapshot(path, "Entra_Group_User_Memberships")])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                explorer.search.setText("user00100")
                explorer._apply_search()
                self.assertEqual(explorer.proxy.rowCount(), 1)
                explorer.clear_filters()
                explorer.proxy.set_column_allowed_values(7, {"Group 001"})
                self.assertGreater(explorer.proxy.rowCount(), 0)
                self.assertLess(explorer.proxy.rowCount(), 150)
            finally:
                _close_explorer(explorer)

    def test_small_snapshot_auto_sorts_default_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.csv"
            _write_csv(
                path,
                "Name,State\nCharlie,Enabled\nAlice,Enabled\nBob,Enabled\n",
            )
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([_make_snapshot(path)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 0)),
                    "Alice",
                )
            finally:
                _close_explorer(explorer)

    def test_small_snapshot_preserves_active_sort_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.csv"
            path_b = Path(directory) / "b.csv"
            _write_csv(path_a, "Name,Rank\nCharlie,3\nAlice,1\nBob,2\n")
            _write_csv(path_b, "Name,Rank\nZoe,9\nAmy,4\nMia,7\n")
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots(
                    [_make_snapshot(path_a, "Users"), _make_snapshot(path_b, "Users")]
                )
                explorer.snapshot_combo.setCurrentIndex(1)
                explorer.load_selected()
                _wait_for_snapshot_load(explorer)
                explorer.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 1)),
                    "1",
                )

                explorer.snapshot_combo.setCurrentIndex(0)
                explorer.load_selected()
                _wait_for_snapshot_load(explorer)
                self.assertEqual(
                    explorer.proxy.data(explorer.proxy.index(0, 1)),
                    "4",
                )
            finally:
                _close_explorer(explorer)

    def test_small_to_large_snapshot_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            small_path = Path(directory) / "small.csv"
            large_path = Path(directory) / "large.csv"
            _write_csv(small_path, "Name,State\nCharlie,Enabled\nAlice,Enabled\n")
            _write_membership_csv(large_path, 150)
            explorer = SnapshotExplorer()
            try:
                with patch(
                    "diffasaurus.ui.snapshot_explorer.LARGE_SNAPSHOT_ROW_THRESHOLD",
                    100,
                ):
                    explorer.set_snapshots(
                        [
                            _make_snapshot(large_path, "Entra_Group_User_Memberships"),
                            _make_snapshot(small_path, "Users"),
                        ]
                    )
                    explorer.snapshot_combo.setCurrentIndex(0)
                    explorer.load_selected()
                    _wait_for_snapshot_load(explorer)
                    self.assertEqual(
                        explorer.proxy.data(explorer.proxy.index(0, 0)),
                        "Alice",
                    )

                    explorer.snapshot_combo.setCurrentIndex(1)
                    explorer.load_selected()
                    _wait_for_snapshot_load(explorer)
                    self.assertEqual(
                        explorer.proxy.data(explorer.proxy.index(0, 0)),
                        "user00000@example.com",
                    )
                    self.assertEqual(
                        explorer.table.horizontalHeader().sortIndicatorSection(),
                        -1,
                    )
            finally:
                _close_explorer(explorer)

    def test_large_to_small_snapshot_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            small_path = Path(directory) / "small.csv"
            large_path = Path(directory) / "large.csv"
            _write_csv(small_path, "Name,State\nCharlie,Enabled\nAlice,Enabled\n")
            _write_membership_csv(large_path, 150)
            explorer = SnapshotExplorer()
            try:
                with patch(
                    "diffasaurus.ui.snapshot_explorer.LARGE_SNAPSHOT_ROW_THRESHOLD",
                    100,
                ):
                    explorer.set_snapshots(
                        [
                            _make_snapshot(large_path, "Entra_Group_User_Memberships"),
                            _make_snapshot(small_path, "Users"),
                        ]
                    )
                    explorer.snapshot_combo.setCurrentIndex(1)
                    explorer.load_selected()
                    _wait_for_snapshot_load(explorer)
                    explorer.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
                    self.assertEqual(
                        explorer.proxy.data(explorer.proxy.index(0, 0)),
                        "user00149@example.com",
                    )

                    explorer.snapshot_combo.setCurrentIndex(0)
                    explorer.load_selected()
                    _wait_for_snapshot_load(explorer)
                    self.assertEqual(
                        explorer.proxy.data(explorer.proxy.index(0, 0)),
                        "Alice",
                    )
            finally:
                _close_explorer(explorer)

    def test_large_snapshot_reload_clears_explicit_sort(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.csv"
            path_b = Path(directory) / "b.csv"
            _write_membership_csv(path_a, 150)
            _write_membership_csv(path_b, 150)
            explorer = SnapshotExplorer()
            try:
                with patch(
                    "diffasaurus.ui.snapshot_explorer.LARGE_SNAPSHOT_ROW_THRESHOLD",
                    100,
                ):
                    explorer.set_snapshots(
                        [
                            _make_snapshot(path_b, "Entra_Group_User_Memberships"),
                            _make_snapshot(path_a, "Entra_Group_User_Memberships"),
                        ]
                    )
                    explorer.activate()
                    _wait_for_snapshot_load(explorer)
                    explorer.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
                    self.assertEqual(
                        explorer.proxy.data(explorer.proxy.index(0, 0)),
                        "user00149@example.com",
                    )

                    explorer.snapshot_combo.setCurrentIndex(1)
                    explorer.load_selected()
                    _wait_for_snapshot_load(explorer)
                    self.assertEqual(
                        explorer.proxy.data(explorer.proxy.index(0, 0)),
                        "user00000@example.com",
                    )
                    self.assertEqual(
                        explorer.table.horizontalHeader().sortIndicatorSection(),
                        -1,
                    )
            finally:
                _close_explorer(explorer)

    def test_open_filter_constructs_dialog_with_model_and_filters(self):
        explorer = SnapshotExplorer()
        try:
            explorer.model.set_table(
                ["Name", "State"],
                [["Alice", "Enabled"], ["Bob", "Disabled"]],
            )
            explorer._filters = {1: {"allowed": {"Enabled"}, "allow_empty": False}}
            fake_dialog = MagicMock()
            fake_dialog.exec.return_value = QDialog.DialogCode.Rejected

            with patch(
                "diffasaurus.ui.snapshot_explorer.MultiColumnFilterDialog",
                return_value=fake_dialog,
            ) as constructor:
                explorer.open_filter()

            constructor.assert_called_once_with(
                explorer,
                explorer.model,
                current_filters={1: {"allowed": {"Enabled"}, "allow_empty": False}},
            )
            fake_dialog.exec.assert_called_once()
            self.assertEqual(explorer.proxy.active_filter_count(), 0)
        finally:
            _close_explorer(explorer)

    def test_open_filter_accepted_applies_result_filters(self):
        explorer = SnapshotExplorer()
        try:
            explorer.model.set_table(
                ["Name", "State"],
                [["Alice", "Enabled"], ["Bob", "Disabled"]],
            )
            fake_dialog = MagicMock()
            fake_dialog.exec.return_value = QDialog.DialogCode.Accepted
            fake_dialog.result_filters = {
                1: {"allowed": {"Disabled"}, "allow_empty": False},
            }

            with patch(
                "diffasaurus.ui.snapshot_explorer.MultiColumnFilterDialog",
                return_value=fake_dialog,
            ):
                explorer.open_filter()

            self.assertEqual(explorer.proxy.active_filter_count(), 1)
            self.assertEqual(explorer.proxy.rowCount(), 1)
            self.assertEqual(
                explorer.proxy.data(explorer.proxy.index(0, 0)),
                "Bob",
            )
        finally:
            _close_explorer(explorer)

    def test_export_selection_button_starts_disabled(self):
        explorer = SnapshotExplorer()
        try:
            self.assertFalse(explorer.export_selection_button.isEnabled())
        finally:
            _close_explorer(explorer)

    def test_export_selection_button_disabled_without_selection(self):
        explorer = SnapshotExplorer()
        try:
            explorer.model.set_table(
                ["Name", "State"],
                [["Alice", "Enabled"], ["Bob", "Disabled"]],
            )
            self.assertTrue(explorer.export_view_button.isEnabled())
            self.assertFalse(explorer.export_selection_button.isEnabled())
        finally:
            _close_explorer(explorer)

    def test_export_selection_button_state_follows_table_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "members.csv"
            _write_csv(path, "Name,State\nAlice,Enabled\nBob,Disabled\n")
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([_make_snapshot(path)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertTrue(explorer.export_view_button.isEnabled())
                self.assertFalse(explorer.export_selection_button.isEnabled())

                selection = explorer.table.selectionModel()
                assert selection is not None
                selection.select(
                    explorer.proxy.index(0, 0),
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                self.assertTrue(explorer.export_selection_button.isEnabled())

                selection.select(
                    explorer.proxy.index(1, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                self.assertTrue(explorer.export_selection_button.isEnabled())

                selection.clearSelection()
                self.assertFalse(explorer.export_selection_button.isEnabled())

                selection.select(
                    explorer.proxy.index(0, 0),
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                self.assertTrue(explorer.export_selection_button.isEnabled())

                explorer.proxy.set_column_allowed_values(0, {"Bob"})
                self.assertFalse(explorer.export_selection_button.isEnabled())

                explorer.clear_filters()
                self.assertFalse(explorer.export_selection_button.isEnabled())
            finally:
                _close_explorer(explorer)

    def test_export_selection_button_disabled_after_model_reset_with_stale_selection(self):
        explorer = SnapshotExplorer()
        try:
            explorer._family = "Test"
            explorer.model.set_table(["Name"], [["Alice"]])
            selection = explorer.table.selectionModel()
            assert selection is not None
            selection.select(
                explorer.proxy.index(0, 0),
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.assertTrue(explorer.export_selection_button.isEnabled())

            explorer.proxy.begin_bulk_update()
            explorer.model.set_table(["Name"], [["Bob"], ["Carol"]])
            explorer.proxy.end_bulk_update()
            self.assertFalse(explorer.export_selection_button.isEnabled())
        finally:
            _close_explorer(explorer)

    def test_export_view_writes_visible_rows_only(self):
        with tempfile.TemporaryDirectory() as directory:
            export_path = Path(directory) / "filtered.csv"
            explorer = SnapshotExplorer()
            try:
                explorer.loaded_path = Path(directory) / "memberships.csv"
                explorer.model.set_table(
                    ["Name", "State"],
                    [["Alice", "Enabled"], ["Bob", "Disabled"]],
                    ";",
                )
                explorer.proxy.set_column_allowed_values(1, {"Disabled"})
                with patch(
                    "diffasaurus.ui.snapshot_explorer.QFileDialog.getSaveFileName",
                    return_value=(str(export_path), "CSV files (*.csv)"),
                ):
                    explorer.export_view()
                with export_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(__import__("csv").reader(handle, delimiter=";"))
                self.assertEqual(rows, [["Name", "State"], ["Bob", "Disabled"]])
                self.assertIn("Exported 1 visible rows", explorer.status.text())
            finally:
                _close_explorer(explorer)

    def test_export_selection_writes_selected_rows_in_display_order(self):
        with tempfile.TemporaryDirectory() as directory:
            export_path = Path(directory) / "selection.csv"
            explorer = SnapshotExplorer()
            try:
                explorer.loaded_path = Path(directory) / "memberships.csv"
                explorer.model.set_table(
                    ["Name"],
                    [["Charlie"], ["Alice"], ["Bob"]],
                )
                explorer.table.setSortingEnabled(True)
                explorer.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
                selection = explorer.table.selectionModel()
                assert selection is not None
                selection.select(
                    explorer.proxy.index(2, 0),
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                selection.select(
                    explorer.proxy.index(0, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                self.assertTrue(explorer.export_selection_button.isEnabled())
                with patch(
                    "diffasaurus.ui.snapshot_explorer.QFileDialog.getSaveFileName",
                    return_value=(str(export_path), "CSV files (*.csv)"),
                ):
                    explorer.export_selection()
                with export_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(__import__("csv").reader(handle))
                self.assertEqual(rows, [["Name"], ["Alice"], ["Charlie"]])
            finally:
                _close_explorer(explorer)

    def test_export_view_cancel_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            export_path = Path(directory) / "filtered.csv"
            explorer = SnapshotExplorer()
            try:
                explorer.model.set_table(["Name"], [["Alice"]])
                with patch(
                    "diffasaurus.ui.snapshot_explorer.QFileDialog.getSaveFileName",
                    return_value=("", "CSV files (*.csv)"),
                ):
                    explorer.export_view()
                self.assertFalse(export_path.exists())
            finally:
                _close_explorer(explorer)

    def test_export_controls_hidden_for_configuration_policy_family(self):
        explorer = SnapshotExplorer()
        try:
            explorer.model.set_table(["Name"], [["Policy"]])
            explorer.set_family(CONFIGURATION_POLICY_FAMILY)
            self.assertFalse(explorer.export_view_button.isVisible())
            self.assertFalse(explorer.export_selection_button.isVisible())
            self.assertFalse(explorer.save_as_dashboard_button.isVisible())
            self.assertFalse(explorer.export_view_button.isEnabled())
            self.assertFalse(explorer.export_selection_button.isEnabled())
            self.assertFalse(explorer.save_as_dashboard_button.isEnabled())
            self.assertFalse(explorer.dashboard_toolbar.isVisible())
            self.assertFalse(explorer.new_dashboard_button.isEnabled())
        finally:
            _close_explorer(explorer)

    def test_toolbars_switch_with_table_and_dashboard_views(self):
        explorer = SnapshotExplorer()
        try:
            self.assertEqual(explorer.toolbar_stack.currentIndex(), 0)
            explorer.show_view(1)
            self.assertEqual(explorer.toolbar_stack.currentIndex(), 1)
            explorer.show_view(0)
            self.assertEqual(explorer.toolbar_stack.currentIndex(), 0)
        finally:
            _close_explorer(explorer)

    def _toolbar_button_texts(self, toolbar) -> list[str]:
        return [button.text() for button in toolbar.findChildren(QPushButton)]

    def test_table_toolbar_includes_save_as_dashboard(self):
        explorer = SnapshotExplorer()
        try:
            explorer.show_view(0)
            self.assertIn(
                "Save as dashboard",
                self._toolbar_button_texts(explorer.table_toolbar),
            )
            self.assertEqual(
                explorer.save_as_dashboard_button.parentWidget(),
                explorer.table_toolbar,
            )
        finally:
            _close_explorer(explorer)

    def test_dashboard_toolbar_excludes_save_as_dashboard(self):
        explorer = SnapshotExplorer()
        try:
            explorer.show_view(1)
            texts = self._toolbar_button_texts(explorer.dashboard_toolbar)
            self.assertNotIn("Save as dashboard", texts)
            self.assertNotIn("Save current view", texts)
            self.assertEqual(
                texts,
                [
                    "+ New dashboard",
                    "Import",
                    "Export definitions",
                    "Reset my dashboards",
                ],
            )
        finally:
            _close_explorer(explorer)

    def test_my_dashboards_empty_state_has_no_duplicate_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "config" / "user_dashboards.json"
            explorer = SnapshotExplorer()
            try:
                explorer._dashboard_store = UserDashboardStore(store_path)
                explorer.set_family("TestFamily")
                explorer.model.set_table(["Name"], [["Alice"]])
                explorer._refresh_user_dashboards()
                explorer.show_view(1)
                explorer.dashboard.tabs.setCurrentIndex(1)
                explorer.show()
                QApplication.processEvents()
                self.assertEqual(explorer.dashboard.grid.count(), 0)
                self.assertTrue(explorer.dashboard.empty_label.isVisible())
                self.assertFalse(hasattr(explorer.dashboard, "empty_save_button"))
                self.assertFalse(hasattr(explorer.dashboard, "empty_new_button"))
            finally:
                _close_explorer(explorer)

    def test_save_as_dashboard_captures_view_and_lists_in_my_dashboards(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "config" / "user_dashboards.json"
            explorer = SnapshotExplorer()
            try:
                explorer._dashboard_store = UserDashboardStore(store_path)
                explorer.set_family("TestFamily")
                explorer.model.set_table(
                    ["DisplayName", "AccountEnabled"],
                    [["Adele", "True"], ["Alex", "False"]],
                )
                explorer.proxy.set_column_allowed_values(1, {"False"})
                explorer.search.setText("alex")
                explorer._apply_search()
                explorer.show_view(0)
                with (
                    patch(
                        "diffasaurus.ui.snapshot_explorer.QInputDialog.getText",
                        return_value=("Disabled users", True),
                    ),
                    patch(
                        "diffasaurus.ui.snapshot_explorer.QInputDialog.getMultiLineText",
                        return_value=("", True),
                    ),
                ):
                    explorer._save_current_view_as_dashboard()
                saved = explorer._dashboard_store.for_family("TestFamily")
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0].name, "Disabled users")
                self.assertEqual(saved[0].filters[0].column, "AccountEnabled")
                self.assertEqual(saved[0].search_text, "alex")
                explorer._refresh_user_dashboards()
                self.assertEqual(explorer.dashboard.grid.count(), 1)
                self.assertEqual(explorer.views.currentIndex(), 0)
                self.assertIn('Dashboard "Disabled users" saved', explorer.status.text())
            finally:
                _close_explorer(explorer)

    def test_user_dashboard_card_menu_actions_and_style(self):
        dashboard = UserDashboard(id="dash-1", name="Sample", family="TestFamily")
        card = UserDashboardCard(dashboard, missing_columns=[])
        try:
            self.assertEqual(
                card.menu_action_texts(),
                ["Open", "Edit", "Duplicate", "Export CSV", "Delete"],
            )
            stylesheet = card.action_menu.styleSheet()
            self.assertIn("#121f2b", stylesheet)
            self.assertIn("border: 1px solid", stylesheet)
            self.assertEqual(
                card.action_menu.styleSheet(),
                user_dashboard_card_menu_stylesheet(),
            )
            self.assertIn("color: #f87171", card.action_menu.styleSheet())
        finally:
            card.deleteLater()
            _drain_qt()

    def test_user_dashboard_card_menu_signals_dispatch(self):
        dashboard = UserDashboard(id="dash-1", name="Sample", family="TestFamily")
        card = UserDashboardCard(dashboard, missing_columns=[])
        try:
            received: list[tuple[str, str]] = []

            def capture(action: str, dashboard_id: str) -> None:
                received.append((action, dashboard_id))

            card.open_requested.connect(lambda dashboard_id: capture("open", dashboard_id))
            card.edit_requested.connect(lambda dashboard_id: capture("edit", dashboard_id))
            card.duplicate_requested.connect(
                lambda dashboard_id: capture("duplicate", dashboard_id)
            )
            card.export_requested.connect(
                lambda dashboard_id: capture("export", dashboard_id)
            )
            card.delete_requested.connect(
                lambda dashboard_id: capture("delete", dashboard_id)
            )
            card._open_action.trigger()
            card._edit_action.trigger()
            card._duplicate_action.trigger()
            card._export_action.trigger()
            card._delete_action.trigger()
            self.assertEqual(
                received,
                [
                    ("open", "dash-1"),
                    ("edit", "dash-1"),
                    ("duplicate", "dash-1"),
                    ("export", "dash-1"),
                    ("delete", "dash-1"),
                ],
            )
        finally:
            card.deleteLater()
            _drain_qt()

    def test_table_filters_persist_when_switching_to_dashboard(self):
        explorer = SnapshotExplorer()
        try:
            explorer.model.set_table(
                ["Name", "State"],
                [["Alice", "Enabled"], ["Bob", "Disabled"]],
            )
            explorer.proxy.set_column_allowed_values(1, {"Disabled"})
            explorer.search.setText("bob")
            explorer._apply_search()
            explorer.show_view(1)
            explorer.show_view(0)
            self.assertEqual(explorer.proxy.active_filter_count(), 1)
            self.assertEqual(explorer.search.text(), "bob")
            self.assertEqual(explorer.proxy.rowCount(), 1)
        finally:
            _close_explorer(explorer)

    def test_user_dashboard_store_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "config" / "user_dashboards.json"
            explorer = SnapshotExplorer()
            try:
                explorer._dashboard_store = UserDashboardStore(store_path)
                explorer.set_family("Entra_Users_Properties")
                explorer.model.set_table(
                    ["DisplayName", "AccountEnabled"],
                    [["Adele", "True"], ["Alex", "False"]],
                )
                explorer._dashboard_store.create(
                    UserDashboard(
                        id="dash-1",
                        name="Disabled only",
                        family="Entra_Users_Properties",
                        filters=[
                            ColumnFilterSpec(
                                column="AccountEnabled",
                                allowed=["False"],
                            )
                        ],
                    )
                )
                reloaded = UserDashboardStore(store_path)
                self.assertEqual(len(reloaded.for_family("Entra_Users_Properties")), 1)
            finally:
                _close_explorer(explorer)

    def test_user_dashboard_family_scoping_in_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "config" / "user_dashboards.json"
            explorer = SnapshotExplorer()
            try:
                explorer._dashboard_store = UserDashboardStore(store_path)
                explorer._dashboard_store.create(
                    UserDashboard(id="1", name="Users view", family="Users")
                )
                explorer._dashboard_store.create(
                    UserDashboard(id="2", name="Groups view", family="Groups")
                )
                explorer.set_family("Users")
                explorer.model.set_table(["Name"], [["Alice"]])
                explorer._refresh_user_dashboards()
                self.assertEqual(explorer.dashboard.grid.count(), 1)
                explorer.set_family("Groups")
                explorer._refresh_user_dashboards()
                self.assertEqual(explorer.dashboard.grid.count(), 1)
            finally:
                _close_explorer(explorer)

    def test_apply_user_dashboard_reproduces_table_view(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "config" / "user_dashboards.json"
            explorer = SnapshotExplorer()
            try:
                explorer._dashboard_store = UserDashboardStore(store_path)
                explorer.set_family("TestFamily")
                explorer.model.set_table(
                    ["DisplayName", "AccountEnabled", "UserType"],
                    [
                        ["Adele", "True", "Member"],
                        ["Alex", "False", "Guest"],
                        ["Allan", "True", "Member"],
                    ],
                )
                explorer.proxy.set_column_allowed_values(1, {"False"})
                explorer.search.setText("alex")
                explorer._apply_search()
                header = explorer.table.horizontalHeader()
                explorer.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
                draft, _notice = capture_current_view(
                    family="TestFamily",
                    headers=explorer.model.headers,
                    filters=explorer.proxy.column_filter_map(),
                    search_mode="smart",
                    search_text=explorer.search.text(),
                    sort_column=header.sortIndicatorSection(),
                    sort_order=header.sortIndicatorOrder(),
                    has_fixed_row_filter=False,
                )
                draft.name = "Manual snapshot"
                saved = explorer._dashboard_store.create(draft)
                explorer.clear_filters()
                self.assertEqual(explorer.proxy.rowCount(), 3)

                manual = SnapshotExplorer()
                try:
                    manual.set_family("TestFamily")
                    manual.model.set_table(
                        list(explorer.model.headers),
                        [list(row) for row in explorer.model._rows],
                    )
                    manual._apply_user_dashboard(saved)
                    self.assertEqual(manual.proxy.rowCount(), 1)
                    self.assertEqual(
                        manual.proxy.data(manual.proxy.index(0, 0)),
                        "Alex",
                    )
                    self.assertEqual(manual.search.text(), "alex")
                finally:
                    _close_explorer(manual)
            finally:
                _close_explorer(explorer)

    def test_incompatible_user_dashboard_preserves_live_table_state(self):
        explorer = SnapshotExplorer()
        try:
            explorer.set_family("TestFamily")
            explorer.model.set_table(
                ["DisplayName", "AccountEnabled", "UserType"],
                [
                    ["Adele", "True", "Member"],
                    ["Alex", "False", "Guest"],
                    ["Allan", "True", "Member"],
                ],
            )
            explorer.proxy.set_column_allowed_values(1, {"False"})
            explorer.search_mode.setCurrentIndex(1)
            explorer.search.setText("alex")
            explorer._apply_search()
            explorer.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
            explorer.show_view(0)
            header = explorer.table.horizontalHeader()
            before = {
                "rows": explorer.proxy.rowCount(),
                "filters": explorer.proxy.column_filter_map(),
                "search": explorer.search.text(),
                "mode": explorer.search_mode.currentData(),
                "sort_column": header.sortIndicatorSection(),
                "sort_order": header.sortIndicatorOrder(),
                "view": explorer.views.currentIndex(),
            }
            dashboard = UserDashboard(
                id="dash-1",
                name="Missing column",
                family="TestFamily",
                filters=[ColumnFilterSpec(column="Department", allowed=["Sales"])],
                search_mode="all",
                search_text="ignored",
                sort=SortSpec(column="DeviceName", order="desc"),
            )
            with patch(
                "diffasaurus.ui.snapshot_explorer.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Ok,
            ):
                applied = explorer._apply_user_dashboard(dashboard)
            self.assertFalse(applied)
            self.assertEqual(explorer.proxy.rowCount(), before["rows"])
            self.assertEqual(explorer.proxy.column_filter_map(), before["filters"])
            self.assertEqual(explorer.search.text(), before["search"])
            self.assertEqual(explorer.search_mode.currentData(), before["mode"])
            self.assertEqual(header.sortIndicatorSection(), before["sort_column"])
            self.assertEqual(header.sortIndicatorOrder(), before["sort_order"])
            self.assertEqual(explorer.views.currentIndex(), before["view"])
        finally:
            _close_explorer(explorer)

    def test_incompatible_dashboard_missing_sort_column_preserves_state(self):
        explorer = SnapshotExplorer()
        try:
            explorer.set_family("TestFamily")
            explorer.model.set_table(
                ["DisplayName", "AccountEnabled"],
                [["Adele", "True"], ["Alex", "False"]],
            )
            explorer.search.setText("adele")
            explorer._apply_search()
            before_rows = explorer.proxy.rowCount()
            before_search = explorer.search.text()
            dashboard = UserDashboard(
                id="dash-1",
                name="Missing sort",
                family="TestFamily",
                filters=[ColumnFilterSpec(column="AccountEnabled", allowed=["True"])],
                sort=SortSpec(column="MissingSortColumn", order="asc"),
            )
            with patch(
                "diffasaurus.ui.snapshot_explorer.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Ok,
            ):
                self.assertFalse(explorer._apply_user_dashboard(dashboard))
            self.assertEqual(explorer.proxy.rowCount(), before_rows)
            self.assertEqual(explorer.search.text(), before_search)
            self.assertEqual(explorer.proxy.column_filter_map(), {})
        finally:
            _close_explorer(explorer)

    def test_incompatible_dashboard_missing_one_filter_column_preserves_state(self):
        explorer = SnapshotExplorer()
        try:
            explorer.set_family("TestFamily")
            explorer.model.set_table(
                ["DisplayName", "AccountEnabled", "UserType"],
                [
                    ["Adele", "True", "Member"],
                    ["Alex", "False", "Guest"],
                ],
            )
            explorer.proxy.set_column_allowed_values(2, {"Guest"})
            explorer.search.setText("alex")
            explorer._apply_search()
            before = {
                "rows": explorer.proxy.rowCount(),
                "filters": explorer.proxy.column_filter_map(),
                "search": explorer.search.text(),
            }
            dashboard = UserDashboard(
                id="dash-1",
                name="One missing filter column",
                family="TestFamily",
                filters=[
                    ColumnFilterSpec(column="AccountEnabled", allowed=["False"]),
                    ColumnFilterSpec(column="Department", allowed=["Sales"]),
                ],
            )
            with patch(
                "diffasaurus.ui.snapshot_explorer.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Ok,
            ):
                self.assertFalse(explorer._apply_user_dashboard(dashboard))
            self.assertEqual(explorer.proxy.rowCount(), before["rows"])
            self.assertEqual(explorer.proxy.column_filter_map(), before["filters"])
            self.assertEqual(explorer.search.text(), before["search"])
        finally:
            _close_explorer(explorer)

    def test_user_dashboard_export_csv_matches_filtered_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            export_path = Path(directory) / "export.csv"
            explorer = SnapshotExplorer()
            try:
                store_path = Path(directory) / "config" / "user_dashboards.json"
                explorer._dashboard_store = UserDashboardStore(store_path)
                explorer.set_family("TestFamily")
                explorer.loaded_path = Path(directory) / "users.csv"
                explorer.model.set_table(
                    ["DisplayName", "AccountEnabled"],
                    [["Adele", "True"], ["Alex", "False"]],
                )
                dashboard = explorer._dashboard_store.create(
                    UserDashboard(
                        id="dash-1",
                        name="Disabled users",
                        family="TestFamily",
                        filters=[
                            ColumnFilterSpec(
                                column="AccountEnabled",
                                allowed=["False"],
                            )
                        ],
                    )
                )
                with patch(
                    "diffasaurus.ui.snapshot_explorer.QFileDialog.getSaveFileName",
                    return_value=(str(export_path), "CSV files (*.csv)"),
                ):
                    explorer._export_user_dashboard_csv(dashboard.id)
                with export_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    exported = list(__import__("csv").reader(handle))
                self.assertEqual(
                    exported,
                    [["DisplayName", "AccountEnabled"], ["Alex", "False"]],
                )
            finally:
                _close_explorer(explorer)

    def test_configure_proxy_for_dashboard_sorts_by_column_name(self):
        model = CsvTableModel(
            ["Name", "Rank"],
            [["Charlie", "3"], ["Alice", "1"], ["Bob", "2"]],
        )
        proxy = CsvFilterProxy()
        proxy.setSourceModel(model)
        dashboard = UserDashboard(
            id="dash-1",
            name="Sorted",
            family="TestFamily",
            sort=SortSpec(column="Name", order="asc"),
        )
        compatible, missing = configure_proxy_for_dashboard(proxy, model, dashboard)
        self.assertTrue(compatible)
        self.assertEqual(missing, [])
        self.assertEqual(proxy.data(proxy.index(0, 0)), "Alice")

    def test_new_snapshot_load_clears_selection_export_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.csv"
            path_b = Path(directory) / "b.csv"
            _write_csv(path_a, "Name\nAlice\n")
            _write_csv(path_b, "Name\nBob\n")
            explorer = SnapshotExplorer()
            try:
                explorer.set_snapshots([_make_snapshot(path_a)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                selection = explorer.table.selectionModel()
                assert selection is not None
                selection.select(
                    explorer.proxy.index(0, 0),
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                self.assertTrue(explorer.export_selection_button.isEnabled())

                explorer.set_snapshots([_make_snapshot(path_b)])
                explorer.activate()
                _wait_for_snapshot_load(explorer)
                self.assertFalse(explorer.export_selection_button.isEnabled())
            finally:
                _close_explorer(explorer)


if __name__ == "__main__":
    unittest.main()
