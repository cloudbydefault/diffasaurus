import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.report_history import (
    ComparisonSummary,
    FamilyChangeStatus,
    ReportSnapshot,
    family_change_status,
    scan_report_history,
)
from diffasaurus.ui.recent_changes import DETAIL_TABLE_LIMIT, FamilyChangeSection
from tests.test_report_history import ios_device_row, write_report


def _drain_qt(*, events: int = 20) -> None:
    app = QApplication.instance()
    assert app is not None
    for _ in range(events):
        app.processEvents()
        time.sleep(0.02)


def _close_section(section: FamilyChangeSection) -> None:
    section._detail_search_timer.stop()
    section.close()
    _drain_qt()
    section.deleteLater()
    _drain_qt()


def _family_snapshot(family: str, captured_at: datetime) -> ReportSnapshot:
    return ReportSnapshot(
        path=Path(f"{family}_{captured_at:%Y%m%d-%H%M%S}.csv"),
        family=family,
        captured_at=captured_at,
        row_count=1,
        headers=("UPN",),
    )


def _comparison_status(
    family: str,
    summary: ComparisonSummary,
    *,
    reference: datetime | None = None,
) -> FamilyChangeStatus:
    reference = reference or datetime(2026, 8, 4, 13, 0, 0)
    return FamilyChangeStatus(
        family=family,
        status="changed",
        baseline=_family_snapshot(family, reference - timedelta(days=2)),
        latest=_family_snapshot(family, reference - timedelta(hours=1)),
        key_column="Key",
        summary=summary,
        reason="",
        semantic_details=(),
        policy_summary=None,
        policy_target_descriptor=None,
        partial_coverage=False,
    )


def _expand_with_details(section: FamilyChangeSection) -> None:
    section._toggle_details()
    _drain_qt()


def _type_search(section: FamilyChangeSection, text: str) -> None:
    section.detail_search.clear()
    for character in text:
        section.detail_search.insert(character)
        QApplication.processEvents()


class RecentChangesDetailSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _section_with_summary(self, family: str, summary: ComparisonSummary) -> FamilyChangeSection:
        section = FamilyChangeSection()
        section.apply_status(
            _comparison_status(family, summary),
            datetime(2026, 8, 4, 12, 0, 0),
        )
        _expand_with_details(section)
        return section

    def test_generic_detail_search_does_not_crash_and_filters(self):
        summary = ComparisonSummary(
            added=0,
            removed=0,
            changed=2,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": "pkg-1",
                    "column": "ModifiedDateTime",
                    "before": "old",
                    "after": "new",
                    "identity": "Developer Access",
                },
                {
                    "change": "Changed",
                    "key": "pkg-2",
                    "column": "ModifiedDateTime",
                    "before": "old",
                    "after": "new",
                    "identity": "Finance Access",
                },
            ),
        )
        section = self._section_with_summary("Entra_Access_Packages", summary)
        try:
            self.assertEqual(section.detail_table.rowCount(), 2)
            _type_search(section, "finance")
            _drain_qt()
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertIn("Finance", section.detail_table.item(0, 1).text())
        finally:
            _close_section(section)

    def test_membership_detail_search_does_not_crash_and_filters(self):
        summary = ComparisonSummary(
            added=2,
            removed=0,
            changed=0,
            stable=0,
            details=(
                {
                    "change": "Added",
                    "identity": "jane@example.com → Developers",
                    "column": "Group",
                    "before": "",
                    "after": "Developers",
                    "key": "k1",
                    "group_name": "Developers",
                },
                {
                    "change": "Added",
                    "identity": "bob@example.com → Finance",
                    "column": "Group",
                    "before": "",
                    "after": "Finance",
                    "key": "k2",
                    "group_name": "Finance",
                },
            ),
        )
        section = self._section_with_summary("Entra_Group_User_Memberships", summary)
        try:
            _type_search(section, "finance")
            _drain_qt()
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertIn("Finance", section.detail_table.item(0, 1).text())
        finally:
            _close_section(section)

    def test_semantic_user_activity_search_does_not_crash(self):
        summary = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": "user-1",
                    "identity": "Ada Lovelace · ada@example.com",
                    "column": "LastSuccessfulSignInDateTime",
                    "before": "2026-08-12 12:57:40",
                    "after": "2026-08-13 15:18:14",
                    "user_id": "user-1",
                    "UPN": "ada@example.com",
                },
            ),
        )
        section = self._section_with_summary("Entra_Users_Activity", summary)
        try:
            _type_search(section, "ada")
            _drain_qt()
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertEqual(
                section.detail_table.item(0, 2).text(),
                "Last successful sign-in",
            )
        finally:
            _close_section(section)

    def test_device_family_search_matches_noncompliant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = ios_device_row(device_name="Phone A", compliance_state="Compliant")
            latest = ios_device_row(device_name="Phone A", compliance_state="Noncompliant")
            write_report(root / "Intune_iOS_Devices_20260731-042100.csv", [baseline])
            write_report(root / "Intune_iOS_Devices_20260804-042100.csv", [latest])
            reference = datetime(2026, 8, 4, 13, 5, 0)
            status = family_change_status(
                "Intune_iOS_Devices",
                scan_report_history(root)["Intune_iOS_Devices"],
                timedelta(hours=48),
                reference=reference,
                include_details=True,
            )
            section = FamilyChangeSection()
            section.apply_status(status, reference - timedelta(hours=48))
            _expand_with_details(section)
            try:
                _type_search(section, "noncompliant")
                _drain_qt()
                self.assertEqual(section.detail_table.rowCount(), 1)
            finally:
                _close_section(section)

    def test_search_is_case_insensitive(self):
        summary = ComparisonSummary(
            added=1,
            removed=0,
            changed=0,
            stable=0,
            details=(
                {
                    "change": "Added",
                    "identity": "Alex Example",
                    "column": "Name",
                    "before": "",
                    "after": "Alex",
                    "key": "k1",
                },
            ),
        )
        section = self._section_with_summary("Entra_Access_Packages", summary)
        try:
            _type_search(section, "ALEX")
            _drain_qt()
            self.assertEqual(section.detail_table.rowCount(), 1)
        finally:
            _close_section(section)

    def test_clearing_search_restores_rows_promptly(self):
        summary = ComparisonSummary(
            added=2,
            removed=0,
            changed=0,
            stable=0,
            details=tuple(
                {
                    "change": "Added",
                    "identity": f"User {index}",
                    "column": "Name",
                    "before": "",
                    "after": f"User {index}",
                    "key": f"k{index}",
                }
                for index in range(2)
            ),
        )
        section = self._section_with_summary("Entra_Access_Packages", summary)
        try:
            _type_search(section, "User 1")
            _drain_qt()
            self.assertEqual(section.detail_table.rowCount(), 1)
            section.detail_search.clear()
            _drain_qt()
            self.assertEqual(section.detail_table.rowCount(), 2)
        finally:
            _close_section(section)

    def test_added_removed_changed_buttons_work_with_search(self):
        summary = ComparisonSummary(
            added=1,
            removed=1,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Added",
                    "identity": "Added User",
                    "column": "Name",
                    "before": "",
                    "after": "Added User",
                    "key": "a",
                },
                {
                    "change": "Removed",
                    "identity": "Removed User",
                    "column": "Name",
                    "before": "Removed User",
                    "after": "",
                    "key": "r",
                },
                {
                    "change": "Changed",
                    "identity": "Changed User",
                    "column": "Name",
                    "before": "Old",
                    "after": "New",
                    "key": "c",
                },
            ),
        )
        section = self._section_with_summary("Entra_Access_Packages", summary)
        try:
            section.detail_search.setText("changed user")
            _drain_qt()
            section._set_filter("Changed")
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertEqual(section.detail_table.item(0, 0).text(), "Changed")
        finally:
            _close_section(section)

    def test_detail_table_limit_notice_remains(self):
        summary = ComparisonSummary(
            added=DETAIL_TABLE_LIMIT + 5,
            removed=0,
            changed=0,
            stable=0,
            details=tuple(
                {
                    "change": "Added",
                    "identity": f"User {index}",
                    "column": "Name",
                    "before": "",
                    "after": f"User {index}",
                    "key": f"k{index}",
                }
                for index in range(DETAIL_TABLE_LIMIT + 5)
            ),
        )
        section = self._section_with_summary("Entra_Access_Packages", summary)
        try:
            self.assertEqual(section.detail_table.rowCount(), DETAIL_TABLE_LIMIT)
            self.assertIn("2,000", section.detail_notice.text())
            section.detail_search.setText("User")
            _drain_qt()
            self.assertEqual(section.detail_table.rowCount(), DETAIL_TABLE_LIMIT)
            self.assertIn("2,000", section.detail_notice.text())
        finally:
            _close_section(section)

    def test_debounce_applies_latest_prefix_only(self):
        summary = ComparisonSummary(
            added=2,
            removed=0,
            changed=0,
            stable=0,
            details=(
                {
                    "change": "Added",
                    "identity": "Alex",
                    "column": "Name",
                    "before": "",
                    "after": "Alex",
                    "key": "a",
                },
                {
                    "change": "Added",
                    "identity": "Bob",
                    "column": "Name",
                    "before": "",
                    "after": "Bob",
                    "key": "b",
                },
            ),
        )
        section = self._section_with_summary("Entra_Access_Packages", summary)
        try:
            apply_calls: list[str] = []
            original = section._apply_detail_filters_now

            def tracked_apply() -> None:
                apply_calls.append(section.detail_search.text())
                original()

            section._detail_search_timer.timeout.disconnect()
            section._detail_search_timer.timeout.connect(tracked_apply)
            section.detail_search.setText("a")
            section.detail_search.setText("al")
            section.detail_search.setText("alex")
            time.sleep(0.2)
            _drain_qt()
            self.assertEqual(apply_calls, ["alex"])
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertIn("Alex", section.detail_table.item(0, 1).text())
        finally:
            _close_section(section)

    def test_apply_status_cancels_pending_debounce(self):
        section = FamilyChangeSection()
        summary = ComparisonSummary(
            added=1,
            removed=0,
            changed=0,
            stable=0,
            details=(
                {
                    "change": "Added",
                    "identity": "Ada",
                    "column": "Name",
                    "before": "",
                    "after": "Ada",
                    "key": "a",
                },
            ),
        )
        try:
            section.apply_status(
                _comparison_status("Entra_Access_Packages", summary),
                datetime(2026, 8, 4, 12, 0, 0),
            )
            _expand_with_details(section)
            section.detail_search.setText("ada")
            section.apply_status(
                _comparison_status(
                    "Entra_Access_Packages",
                    ComparisonSummary(added=0, removed=0, changed=0, stable=0, details=()),
                ),
                datetime(2026, 8, 4, 12, 0, 0),
            )
            time.sleep(0.2)
            _drain_qt()
            self.assertFalse(section._detail_search_timer.isActive())
        finally:
            _close_section(section)

    def test_collapsed_section_ignores_pending_debounce(self):
        summary = ComparisonSummary(
            added=1,
            removed=0,
            changed=0,
            stable=0,
            details=(
                {
                    "change": "Added",
                    "identity": "Ada",
                    "column": "Name",
                    "before": "",
                    "after": "Ada",
                    "key": "a",
                },
            ),
        )
        section = self._section_with_summary("Entra_Access_Packages", summary)
        try:
            section.detail_search.setText("ada")
            section._expanded = False
            section.details_panel.hide()
            time.sleep(0.2)
            _drain_qt()
        finally:
            _close_section(section)

    def test_membership_search_uses_comparison_renderer_not_raw_semantic_rows(self):
        summary = ComparisonSummary(
            added=1,
            removed=0,
            changed=0,
            stable=0,
            details=(
                {
                    "change": "Added",
                    "identity": "jane@example.com",
                    "column": "Group",
                    "before": "",
                    "after": "Developers",
                    "key": "k1",
                    "group_name": "Developers",
                },
            ),
        )
        section = self._section_with_summary("Entra_Group_User_Memberships", summary)
        try:
            with patch(
                "diffasaurus.ui.recent_changes.populate_comparison_detail_table"
            ) as populate:
                section.detail_search.setText("dev")
                _drain_qt()
                populate.assert_called()
        finally:
            _close_section(section)


if __name__ == "__main__":
    unittest.main()
