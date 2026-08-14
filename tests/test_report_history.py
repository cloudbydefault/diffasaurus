import csv
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.report_history import (
    REASON_NOT_ENOUGH_SNAPSHOTS,
    REASON_NO_BASELINE,
    REASON_STALE_LATEST,
    ReportSnapshot,
    aggregate_recent_changes,
    analyze_snapshot,
    compare_snapshots,
    compare_snapshot_counts,
    composite_key_label,
    detail_identity,
    expected_business_days,
    family_change_status,
    filter_history_by_days,
    identity_display_column,
    metric_series,
    report_family,
    report_run_health,
    role_assignment_suggested_key_label,
    scan_report_index,
    scan_report_history,
    save_analysis_cache,
    schema_changes,
    select_period_snapshots,
    suggested_key,
)


def write_report(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def history_snapshot(
    captured_at: datetime,
    index: int,
    headers: tuple[str, ...] = ("Id",),
) -> ReportSnapshot:
    return ReportSnapshot(
        path=Path(f"Devices_{captured_at:%Y%m%d-%H%M%S}_{index}.csv"),
        family="Devices",
        captured_at=captured_at,
        row_count=index,
        headers=headers,
    )


def history_snapshot(
    captured_at: datetime,
    index: int,
    headers: tuple[str, ...] = ("Id",),
) -> ReportSnapshot:
    return ReportSnapshot(
        path=Path(f"Devices_{captured_at:%Y%m%d-%H%M%S}_{index}.csv"),
        family="Devices",
        captured_at=captured_at,
        row_count=index,
        headers=headers,
    )


def family_snapshot(
    family: str,
    captured_at: datetime,
    headers: tuple[str, ...] = ("UPN",),
) -> ReportSnapshot:
    return ReportSnapshot(
        path=Path(f"{family}_{captured_at:%Y%m%d-%H%M%S}.csv"),
        family=family,
        captured_at=captured_at,
        row_count=1,
        headers=headers,
    )


class RecentChangesTests(unittest.TestCase):
    def test_select_period_snapshots_picks_newest_before_cutoff(self):
        reference = datetime(2026, 8, 4, 12, 0, 0)
        snapshots = [
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=72)),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=36)),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=6)),
        ]
        pair = select_period_snapshots(
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        self.assertIsNotNone(pair)
        baseline, latest = pair
        self.assertEqual(baseline.captured_at, reference - timedelta(hours=36))
        self.assertEqual(latest.captured_at, reference - timedelta(hours=6))

    def test_select_period_snapshots_no_data_single_snapshot(self):
        reference = datetime(2026, 8, 4, 12, 0, 0)
        snapshots = [family_snapshot("Entra_Users_Properties", reference - timedelta(hours=6))]
        status = family_change_status(
            "Entra_Users_Properties",
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        self.assertEqual(status.status, "no_data")
        self.assertEqual(status.reason, REASON_NOT_ENOUGH_SNAPSHOTS)
        self.assertIsNone(select_period_snapshots(snapshots, timedelta(hours=24), reference=reference))

    def test_select_period_snapshots_stale_latest_older_than_cutoff(self):
        reference = datetime(2026, 8, 4, 12, 0, 0)
        snapshots = [
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=48)),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=40)),
        ]
        status = family_change_status(
            "Entra_Users_Properties",
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        self.assertEqual(status.status, "no_data")
        self.assertEqual(status.reason, REASON_STALE_LATEST)
        self.assertIsNone(select_period_snapshots(snapshots, timedelta(hours=24), reference=reference))
        self.assertIsNone(status.summary)

    def test_select_period_snapshots_latest_inside_period(self):
        reference = datetime(2026, 8, 4, 12, 0, 0)
        snapshots = [
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=36)),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=6)),
        ]
        pair = select_period_snapshots(
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        self.assertIsNotNone(pair)
        baseline, latest = pair
        self.assertEqual(baseline.captured_at, reference - timedelta(hours=36))
        self.assertEqual(latest.captured_at, reference - timedelta(hours=6))

    def test_select_period_snapshots_baseline_exactly_at_cutoff(self):
        reference = datetime(2026, 8, 4, 12, 0, 0)
        cutoff = reference - timedelta(hours=24)
        snapshots = [
            family_snapshot("Entra_Users_Properties", cutoff),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=6)),
        ]
        pair = select_period_snapshots(
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        self.assertIsNotNone(pair)
        baseline, latest = pair
        self.assertEqual(baseline.captured_at, cutoff)
        self.assertEqual(latest.captured_at, reference - timedelta(hours=6))

    def test_select_period_snapshots_irregular_schedule(self):
        reference = datetime(2026, 8, 8, 15, 0, 0)  # Friday afternoon
        snapshots = [
            family_snapshot("Entra_Users_Properties", datetime(2026, 8, 4, 1, 0, 0)),  # Mon
            family_snapshot("Entra_Users_Properties", datetime(2026, 8, 6, 9, 30, 0)),  # Wed
            family_snapshot("Entra_Users_Properties", datetime(2026, 8, 8, 1, 0, 0)),  # Fri
        ]
        pair = select_period_snapshots(
            snapshots,
            timedelta(hours=48),
            reference=reference,
        )
        self.assertIsNotNone(pair)
        baseline, latest = pair
        self.assertEqual(baseline.captured_at, datetime(2026, 8, 6, 9, 30, 0))
        self.assertEqual(latest.captured_at, datetime(2026, 8, 8, 1, 0, 0))

    def test_family_change_status_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "Entra_Users_Properties_20260728-010000.csv"
            after = root / "Entra_Users_Properties_20260804-010000.csv"
            rows = [{"UPN": "ada@example.com", "Department": "Engineering"}]
            write_report(before, rows)
            write_report(after, rows)
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            status = family_change_status(
                "Entra_Users_Properties",
                snapshots,
                timedelta(days=7),
                reference=datetime(2026, 8, 4, 12, 0, 0),
            )
            self.assertEqual(status.status, "unchanged")
            self.assertIsNotNone(status.summary)
            self.assertEqual(status.summary.total_changes, 0)

    def test_aggregate_recent_changes_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 12, 0, 0)

            changed_before = root / "Entra_Users_Properties_20260728-010000.csv"
            changed_after = root / "Entra_Users_Properties_20260804-010000.csv"
            write_report(
                changed_before,
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                changed_after,
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )

            unchanged_before = root / "Entra_Access_Packages_20260728-010000.csv"
            unchanged_after = root / "Entra_Access_Packages_20260804-010000.csv"
            rows = [{"Id": "pkg-1", "DisplayName": "Finance"}]
            write_report(unchanged_before, rows)
            write_report(unchanged_after, rows)

            stale = root / "Intune_Apps_Full_20260720-010000.csv"
            write_report(stale, [{"Id": "app-1", "DisplayName": "Teams"}])

            families = scan_report_history(root)
            report = aggregate_recent_changes(
                families,
                timedelta(days=7),
                reference=reference,
                family_order=(
                    "Entra_Users_Properties",
                    "Entra_Access_Packages",
                    "Intune_Apps_Full",
                ),
            )
            by_family = {item.family: item for item in report.families}
            self.assertEqual(by_family["Entra_Users_Properties"].status, "changed")
            self.assertEqual(by_family["Entra_Access_Packages"].status, "unchanged")
            self.assertEqual(by_family["Intune_Apps_Full"].status, "no_data")
            self.assertEqual(by_family["Intune_Apps_Full"].reason, REASON_NOT_ENOUGH_SNAPSHOTS)
            self.assertEqual(report.changed_count, 1)
            self.assertEqual(report.unchanged_count, 1)
            self.assertEqual(report.no_data_count, 1)

    def test_aggregate_never_fabricates_baseline(self):
        reference = datetime(2026, 8, 4, 12, 0, 0)
        snapshots = [
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=20)),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=10)),
        ]
        status = family_change_status(
            "Entra_Users_Properties",
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        self.assertEqual(status.status, "no_data")
        self.assertEqual(status.reason, REASON_NO_BASELINE)
        self.assertIsNone(status.baseline)
        self.assertIsNone(select_period_snapshots(snapshots, timedelta(hours=24), reference=reference))

    def test_family_change_status_works_with_index_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_index(root)["Entra_Users_Properties"]
            self.assertTrue(all(not snapshot.headers for snapshot in snapshots))
            status = family_change_status(
                "Entra_Users_Properties",
                snapshots,
                timedelta(hours=48),
                reference=reference,
            )
            self.assertEqual(status.status, "changed")
            self.assertEqual(status.reason, "")
            self.assertIsNotNone(status.baseline)
            self.assertIsNotNone(status.latest)
            self.assertEqual(status.baseline.captured_at, datetime(2026, 7, 31, 4, 21))
            self.assertEqual(status.latest.captured_at, datetime(2026, 8, 4, 4, 21))
            self.assertNotEqual(status.reason, REASON_NO_BASELINE)

    def test_aggregate_processes_families_independently_from_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            write_report(
                root / "Entra_Access_Packages_20260730-010000.csv",
                [{"Id": "pkg-1", "DisplayName": "Finance"}],
            )
            write_report(
                root / "Entra_Access_Packages_20260804-010000.csv",
                [{"Id": "pkg-1", "DisplayName": "Finance"}],
            )
            families = scan_report_index(root)
            report = aggregate_recent_changes(
                families,
                timedelta(hours=48),
                reference=reference,
                family_order=("Entra_Users_Properties", "Entra_Access_Packages"),
            )
            by_family = {item.family: item for item in report.families}
            self.assertEqual(by_family["Entra_Users_Properties"].status, "changed")
            self.assertEqual(by_family["Entra_Access_Packages"].status, "unchanged")
            self.assertEqual(
                by_family["Entra_Users_Properties"].baseline.captured_at,
                datetime(2026, 7, 31, 4, 21),
            )
            self.assertEqual(
                by_family["Entra_Users_Properties"].latest.captured_at,
                datetime(2026, 8, 4, 4, 21),
            )
            self.assertEqual(
                by_family["Entra_Access_Packages"].baseline.captured_at,
                datetime(2026, 7, 30, 1, 0),
            )
            self.assertEqual(
                by_family["Entra_Access_Packages"].latest.captured_at,
                datetime(2026, 8, 4, 1, 0),
            )

    def test_aggregate_result_is_independent_of_selected_family_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            write_report(
                root / "Entra_Access_Packages_20260730-010000.csv",
                [{"Id": "pkg-1", "DisplayName": "Finance"}],
            )
            write_report(
                root / "Entra_Access_Packages_20260804-010000.csv",
                [{"Id": "pkg-1", "DisplayName": "Finance"}],
            )
            families = scan_report_index(root)
            full_report = aggregate_recent_changes(
                families,
                timedelta(hours=48),
                reference=reference,
                family_order=("Entra_Users_Properties", "Entra_Access_Packages"),
            )
            selected_only = {
                "Entra_Access_Packages": families["Entra_Access_Packages"],
            }
            partial_report = aggregate_recent_changes(
                selected_only,
                timedelta(hours=48),
                reference=reference,
                family_order=("Entra_Users_Properties", "Entra_Access_Packages"),
            )
            full_by_family = {item.family: item for item in full_report.families}
            partial_by_family = {item.family: item for item in partial_report.families}
            self.assertEqual(
                full_by_family["Entra_Access_Packages"].status,
                partial_by_family["Entra_Access_Packages"].status,
            )
            self.assertEqual(
                full_by_family["Entra_Access_Packages"].latest.captured_at,
                partial_by_family["Entra_Access_Packages"].latest.captured_at,
            )
            self.assertEqual(full_by_family["Entra_Users_Properties"].status, "changed")
            self.assertNotIn("Entra_Users_Properties", partial_by_family)
            self.assertEqual(len(partial_report.families), 1)

    def test_aggregate_omits_catalog_families_without_indexed_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 12, 0, 0)
            write_report(
                root / "Entra_Users_Properties_20260804-010000.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            families = scan_report_index(root)
            report = aggregate_recent_changes(
                families,
                timedelta(days=7),
                reference=reference,
                family_order=(
                    "Entra_Users_Properties",
                    "Entra_Users_AuthenticationMethods",
                    "Intune_Apps_Full",
                    "Intune_iOS_Devices",
                ),
            )
            self.assertEqual(
                {item.family for item in report.families},
                {"Entra_Users_Properties"},
            )

    def test_aggregate_includes_single_snapshot_family_as_no_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 12, 0, 0)
            write_report(
                root / "Intune_Apps_Full_20260804-010000.csv",
                [{"Id": "app-1", "DisplayName": "Teams"}],
            )
            families = scan_report_index(root)
            report = aggregate_recent_changes(
                families,
                timedelta(days=7),
                reference=reference,
                family_order=("Intune_Apps_Full", "Intune_iOS_Devices"),
            )
            self.assertEqual(len(report.families), 1)
            self.assertEqual(report.families[0].family, "Intune_Apps_Full")
            self.assertEqual(report.families[0].status, "no_data")
            self.assertEqual(report.families[0].reason, REASON_NOT_ENOUGH_SNAPSHOTS)

    def test_aggregate_includes_unknown_indexed_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 12, 0, 0)
            write_report(
                root / "Custom_Tenant_Export_20260804-010000.csv",
                [{"Id": "row-1", "Value": "alpha"}],
            )
            families = scan_report_index(root)
            report = aggregate_recent_changes(
                families,
                timedelta(days=7),
                reference=reference,
                family_order=("Entra_Users_Properties",),
            )
            self.assertEqual(len(report.families), 1)
            self.assertEqual(report.families[0].family, "Custom_Tenant_Export")
            self.assertEqual(report.families[0].status, "no_data")

    def test_aggregate_totals_count_only_indexed_families(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 12, 0, 0)
            write_report(
                root / "Entra_Users_Properties_20260728-010000.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-010000.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            write_report(
                root / "Entra_Access_Packages_20260728-010000.csv",
                [{"Id": "pkg-1", "DisplayName": "Finance"}],
            )
            write_report(
                root / "Entra_Access_Packages_20260804-010000.csv",
                [{"Id": "pkg-1", "DisplayName": "Finance"}],
            )
            write_report(
                root / "Intune_Apps_Full_20260804-010000.csv",
                [{"Id": "app-1", "DisplayName": "Teams"}],
            )
            families = scan_report_index(root)
            report = aggregate_recent_changes(
                families,
                timedelta(days=7),
                reference=reference,
                family_order=(
                    "Entra_Users_Properties",
                    "Entra_Access_Packages",
                    "Intune_Apps_Full",
                    "Entra_Users_AuthenticationMethods",
                    "Intune_iOS_Devices",
                ),
            )
            self.assertEqual(len(report.families), 3)
            self.assertEqual(report.changed_count, 1)
            self.assertEqual(report.unchanged_count, 1)
            self.assertEqual(report.no_data_count, 1)

    def test_no_data_status_matches_snapshot_fields(self):
        reference = datetime(2026, 8, 4, 12, 0, 0)
        snapshots = [
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=20)),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=10)),
        ]
        status = family_change_status(
            "Entra_Users_Properties",
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        self.assertEqual(status.status, "no_data")
        self.assertEqual(status.reason, REASON_NO_BASELINE)
        self.assertIsNone(status.baseline)
        self.assertIsNotNone(status.latest)


class RecentChangesUiConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_apply_status_does_not_show_baseline_for_true_no_baseline_reason(self):
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        reference = datetime(2026, 8, 4, 12, 0, 0)
        snapshots = [
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=20)),
            family_snapshot("Entra_Users_Properties", reference - timedelta(hours=10)),
        ]
        status = family_change_status(
            "Entra_Users_Properties",
            snapshots,
            timedelta(hours=24),
            reference=reference,
        )
        section = FamilyChangeSection()
        section.apply_status(status, reference - timedelta(hours=24))
        self.assertIn("Latest on disk:", section.coverage_label.text())
        self.assertNotIn("Baseline:", section.coverage_label.text())
        self.assertEqual(status.reason, REASON_NO_BASELINE)

    def test_apply_status_shows_paired_snapshots_for_valid_comparison(self):
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            status = family_change_status(
                "Entra_Users_Properties",
                scan_report_index(root)["Entra_Users_Properties"],
                timedelta(hours=48),
                reference=reference,
            )
            section = FamilyChangeSection()
            section.apply_status(status, reference - timedelta(hours=48))
            self.assertIn("Baseline:", section.coverage_label.text())
            self.assertIn("Latest:", section.coverage_label.text())
            self.assertFalse(section.reason_label.isVisible())
            self.assertEqual(status.status, "changed")

    def test_recent_changes_page_constructs(self):
        from diffasaurus.ui.recent_changes import RecentChangesPage

        page = RecentChangesPage()
        self.assertIsNotNone(page.period_selector)
        page.close()

    def test_generic_detail_property_uses_column_name(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = "Entra_Access_Packages"
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": "pkg-1",
                    "column": "ModifiedDateTime",
                    "before": "09/23/2025 09:43:10",
                    "after": "08/13/2026 13:42:53",
                    "identity": "Developer Access",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(section.detail_table.item(0, 2).text(), "ModifiedDateTime")
        self.assertEqual(section.detail_table.item(0, 1).text(), "Developer Access")

    def test_recent_changes_aggregation_and_apply_report_use_catalog_order(self):
        from diffasaurus.ui.recent_changes import RecentChangesPage
        from diffasaurus.ui.report_runner import CATALOG_FAMILY_ORDER

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Group_User_Memberships_20260731-042100.csv",
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "UserDisplayName": "Ada",
                        "GroupId": "group-1",
                        "GroupName": "Developers",
                        "MembershipType": "Assigned",
                    },
                ],
            )
            write_report(
                root / "Entra_Group_User_Memberships_20260804-042100.csv",
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "UserDisplayName": "Ada",
                        "GroupId": "group-1",
                        "GroupName": "Developers",
                        "MembershipType": "Assigned",
                    },
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            families = scan_report_history(root)
            period, label = timedelta(days=15), "15 days"
            report = aggregate_recent_changes(
                families,
                period,
                period_label=label,
                family_order=CATALOG_FAMILY_ORDER,
            )
            page = RecentChangesPage()
            page.apply_report(report)
            rendered = [section.subtitle_label.text() for section in page._sections.values()]
            self.assertIn("Entra_Users_Properties", rendered)
            self.assertIn("Entra_Group_User_Memberships", rendered)
            self.assertLess(
                CATALOG_FAMILY_ORDER.index("Entra_Users_Properties"),
                CATALOG_FAMILY_ORDER.index("Entra_Group_User_Memberships"),
            )
            self.assertNotEqual(page.card_changed.value.text(), "…")
            page.close()

    def test_main_window_recent_changes_refresh_path_resolves_catalog_order(self):
        from unittest.mock import patch

        from diffasaurus.ui.main_window import DiffasaurusWindow

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=Path(directory),
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    results: list = []
                    errors: list[str] = []

                    def run_sync(function, args, on_success, on_failure):
                        try:
                            results.append(function(*args))
                        except Exception as exc:
                            errors.append(str(exc))
                            on_failure(str(exc))

                    with patch.object(window, "_run_background", run_sync):
                        window._refresh_recent_changes()

                    self.assertEqual(errors, [])
                    self.assertEqual(len(results), 1)
                    window.recent_changes_page.apply_report(results[0])
                    self.assertEqual(window.recent_changes_page.card_changed.value.text(), "0")
                finally:
                    window.close()
                    window.thread_pool.waitForDone(2_000)


class EntraGroupsIdentityDisplayTests(unittest.TestCase):
    FAMILY = "Entra_Groups_Dependencies"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        write_report(
            root / "Entra_Groups_Dependencies_20260731-042100.csv",
            baseline_rows,
        )
        write_report(
            root / "Entra_Groups_Dependencies_20260804-042100.csv",
            latest_rows,
        )
        snapshots = scan_report_history(root)[self.FAMILY]
        self.assertEqual(suggested_key(snapshots[0].headers), "GroupId")
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(baseline, latest, "GroupId", self.FAMILY)

    def test_group_comparison_keys_rows_by_group_id(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    {"GroupId": "id-1", "DisplayName": "Finance Users"},
                    {"GroupId": "id-2", "DisplayName": "Developers"},
                ],
                [
                    {"GroupId": "id-1", "DisplayName": "Finance Employees"},
                    {"GroupId": "id-3", "DisplayName": "Application Access"},
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (1, 1, 1))
            keys = {detail["key"] for detail in result.details}
            self.assertEqual(keys, {"id-1", "id-2", "id-3"})

    def test_added_group_displays_latest_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [{"GroupId": "id-1", "DisplayName": "Finance Users"}],
                [
                    {"GroupId": "id-1", "DisplayName": "Finance Users"},
                    {"GroupId": "id-2", "DisplayName": "Developers"},
                ],
            )
            added = next(
                detail for detail in self._compare(baseline, latest).details
                if detail["change"] == "Added"
            )
            self.assertEqual(added["key"], "id-2")
            self.assertEqual(detail_identity(added), "Developers")

    def test_removed_group_displays_baseline_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    {"GroupId": "id-1", "DisplayName": "Finance Users"},
                    {"GroupId": "id-2", "DisplayName": "Developers"},
                ],
                [{"GroupId": "id-1", "DisplayName": "Finance Users"}],
            )
            removed = next(
                detail for detail in self._compare(baseline, latest).details
                if detail["change"] == "Removed"
            )
            self.assertEqual(removed["key"], "id-2")
            self.assertEqual(detail_identity(removed), "Developers")

    def test_changed_group_displays_latest_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [{"GroupId": "id-1", "DisplayName": "Finance Users"}],
                [{"GroupId": "id-1", "DisplayName": "Finance Employees"}],
            )
            changed = next(
                detail for detail in self._compare(baseline, latest).details
                if detail["change"] == "Changed"
            )
            self.assertEqual(changed["key"], "id-1")
            self.assertEqual(changed["column"], "DisplayName")
            self.assertEqual(detail_identity(changed), "Finance Employees")

    def test_missing_display_name_falls_back_to_group_id(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [{"GroupId": "id-1", "DisplayName": ""}],
                [{"GroupId": "id-2", "DisplayName": ""}],
            )
            result = self._compare(baseline, latest)
            for detail in result.details:
                self.assertEqual(detail_identity(detail), detail["key"])

    def test_group_rename_remains_single_changed_entity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [{"GroupId": "abc-123", "DisplayName": "Finance Users"}],
                [{"GroupId": "abc-123", "DisplayName": "Finance Employees"}],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["key"], "abc-123")
            self.assertEqual(detail_identity(changed), "Finance Employees")
            self.assertEqual(changed["column"], "DisplayName")
            self.assertEqual(changed["before"], "Finance Users")
            self.assertEqual(changed["after"], "Finance Employees")

    def test_duplicate_display_names_remain_independent_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    {"GroupId": "id-1", "DisplayName": "Test Group"},
                    {"GroupId": "id-2", "DisplayName": "Test Group"},
                ],
                [
                    {"GroupId": "id-1", "DisplayName": "Test Group"},
                    {"GroupId": "id-2", "DisplayName": "Test Group Renamed"},
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["key"], "id-2")
            self.assertEqual(detail_identity(changed), "Test Group Renamed")

    def test_compare_without_family_omits_identity_label(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [{"GroupId": "id-1", "DisplayName": "Finance Users"}],
                [{"GroupId": "id-2", "DisplayName": "Developers"}],
            )
            result = compare_snapshots(baseline, latest, "GroupId")
            added = next(detail for detail in result.details if detail["change"] == "Added")
            self.assertNotIn("identity", added)
            self.assertEqual(detail_identity(added), "id-2")

    def test_non_group_family_identity_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [
                    {"UPN": "ada@example.com", "DisplayName": "Ada"},
                    {"UPN": "linus@example.com", "DisplayName": "Linus"},
                ],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                "UPN",
                "Entra_Users_Properties",
            )
            added = next(detail for detail in result.details if detail["change"] == "Added")
            self.assertEqual(detail_identity(added), "Linus · linus@example.com")
            self.assertEqual(added["UPN"], "linus@example.com")

    def test_recent_changes_search_matches_display_name_and_group_id(self):
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [{"GroupId": "id-1", "DisplayName": "Finance Users"}],
                [
                    {"GroupId": "id-1", "DisplayName": "Finance Employees"},
                    {"GroupId": "id-2", "DisplayName": "Developers"},
                ],
            )
            summary = self._compare(baseline, latest)
            section = FamilyChangeSection()
            section._details = summary
            section._filter = "All"

            section.detail_search.setText("Developers")
            section._apply_detail_filters()
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertEqual(
                section.detail_table.item(0, 1).text(),
                "Developers",
            )

            section.detail_search.setText("Finance Employees")
            section._apply_detail_filters()
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertEqual(
                section.detail_table.item(0, 1).text(),
                "Finance Employees",
            )

            section.detail_search.setText("id-1")
            section._apply_detail_filters()
            self.assertEqual(section.detail_table.rowCount(), 1)

            section.detail_search.setText("id-2")
            section._apply_detail_filters()
            self.assertEqual(section.detail_table.rowCount(), 1)
            self.assertEqual(
                section.detail_table.item(0, 1).text(),
                "Developers",
            )
            section.close()

    def test_identity_display_column_mapping(self):
        headers = ("GroupId", "DisplayName", "MailEnabled")
        self.assertEqual(
            identity_display_column(self.FAMILY, headers),
            "DisplayName",
        )
        self.assertEqual(
            identity_display_column("Entra_Users_Properties", headers),
            "",
        )


def membership_row(
    user_id: str,
    group_id: str,
    *,
    user_display: str = "",
    upn: str = "",
    group_name: str = "",
    group_mail: str = "",
    membership_type: str = "Assigned",
    **extra,
) -> dict[str, str]:
    row = {
        "UserId": user_id,
        "UserPrincipalName": upn or f"{user_id}@example.com",
        "UserDisplayName": user_display,
        "UserMail": "",
        "UserType": "Member",
        "AccountEnabled": "True",
        "GroupName": group_name,
        "GroupId": group_id,
        "GroupMail": group_mail,
        "GroupType": "Security",
        "MembershipType": membership_type,
        "SecurityEnabled": "True",
        "MailEnabled": "False",
        "IsMicrosoft365Group": "False",
        "IsDynamicGroup": "False",
        "OnPremisesSyncEnabled": "False",
        "MembershipRule": "",
    }
    row.update(extra)
    return row


def access_package_row(
    package_id: str,
    package_name: str,
    *,
    policy_id: str = "",
    policy_name: str = "",
    package_description: str = "",
    catalog_id: str = "catalog-1",
    created: str = "2025-01-01T00:00:00Z",
    modified: str = "2025-01-01T00:00:00Z",
    policy_description: str = "",
    policy_status: str = "",
    **extra,
) -> dict[str, str]:
    row = {
        "AccessPackageName": package_name,
        "AccessPackageId": package_id,
        "AccessPackageDescription": package_description,
        "CatalogId": catalog_id,
        "CreatedDateTime": created,
        "ModifiedDateTime": modified,
        "PolicyName": policy_name,
        "PolicyId": policy_id,
        "PolicyDescription": policy_description,
        "PolicyStatus": policy_status,
    }
    row.update(extra)
    return row


def user_activity_row(
    user_id: str,
    *,
    display_name: str = "",
    upn: str = "",
    mail: str = "",
    user_type: str = "Member",
    account_enabled: str = "True",
    job_title: str = "",
    company_name: str = "",
    department: str = "",
    country: str = "",
    city: str = "",
    created: str = "2025-01-01T00:00:00Z",
    last_password_change: str = "",
    on_premises_sync: str = "False",
    last_interactive: str = "",
    last_non_interactive: str = "",
    last_successful: str = "",
    **extra,
) -> dict[str, str]:
    row = {
        "DisplayName": display_name,
        "UPN": upn,
        "Mail": mail,
        "UserId": user_id,
        "UserType": user_type,
        "AccountEnabled": account_enabled,
        "JobTitle": job_title,
        "CompanyName": company_name,
        "Department": department,
        "Country": country,
        "City": city,
        "CreatedDateTime": created,
        "LastPasswordChangeDateTime": last_password_change,
        "OnPremisesSyncEnabled": on_premises_sync,
        "LastInteractiveSignInDateTime": last_interactive,
        "LastNonInteractiveSignInDateTime": last_non_interactive,
        "LastSuccessfulSignInDateTime": last_successful,
    }
    row.update(extra)
    return row


def auth_methods_hybrid_row(
    microsoft_report_id: str,
    *,
    display_name: str = "",
    upn: str = "",
    user_type: str = "Member",
    account_enabled: str = "True",
    job_title: str = "",
    company_name: str = "",
    department: str = "",
    country: str = "",
    city: str = "",
    is_system_preferred_enabled: str = "True",
    user_preferred_secondary: str = "push",
    system_preferred_method: str = "push",
    authentication_methods: str = "microsoftAuthenticatorPush",
    is_admin: str = "False",
    is_mfa_registered: str = "True",
    is_mfa_capable: str = "True",
    is_passwordless_capable: str = "False",
    is_sspr_registered: str = "False",
    is_sspr_enabled: str = "False",
    is_sspr_capable: str = "False",
    default_mfa_method: str = "microsoftAuthenticatorPush",
    methods_registered: str = "microsoftAuthenticatorPush",
    system_preferred_methods: str = "push",
    last_updated: str = "2026-08-12T12:00:00Z",
    report_source: str = "Microsoft authenticationMethods/userRegistrationDetails",
    **extra,
) -> dict[str, str]:
    row = {
        "DisplayName": display_name,
        "UPN": upn,
        "UserType": user_type,
        "AccountEnabled": account_enabled,
        "JobTitle": job_title,
        "CompanyName": company_name,
        "Department": department,
        "Country": country,
        "City": city,
        "IsSystemPreferredAuthenticationMethodEnabled": is_system_preferred_enabled,
        "UserPreferredMethodForSecondaryAuthentication": user_preferred_secondary,
        "SystemPreferredAuthenticationMethod": system_preferred_method,
        "AuthenticationMethods": authentication_methods,
        "MicrosoftReportId": microsoft_report_id,
        "IsAdmin": is_admin,
        "IsMfaRegistered": is_mfa_registered,
        "IsMfaCapable": is_mfa_capable,
        "IsPasswordlessCapable": is_passwordless_capable,
        "IsSsprRegistered": is_sspr_registered,
        "IsSsprEnabled": is_sspr_enabled,
        "IsSsprCapable": is_sspr_capable,
        "DefaultMfaMethod": default_mfa_method,
        "MethodsRegistered": methods_registered,
        "SystemPreferredAuthenticationMethods": system_preferred_methods,
        "LastUpdatedDateTime": last_updated,
        "ReportSource": report_source,
    }
    row.update(extra)
    return row


def user_properties_row(
    entra_id: str,
    *,
    display_name: str = "",
    upn: str = "",
    given_name: str = "",
    surname: str = "",
    mail: str = "",
    mail_nickname: str = "",
    user_type: str = "Member",
    account_enabled: str = "True",
    created: str = "2025-01-01T00:00:00Z",
    identities: str = "",
    street_address: str = "",
    city: str = "",
    state: str = "",
    postal_code: str = "",
    country: str = "",
    business_phones: str = "",
    mobile_phone: str = "",
    other_mails: str = "",
    proxy_addresses: str = "",
    im_addresses: str = "",
    job_title: str = "",
    company_name: str = "",
    department: str = "",
    office_location: str = "",
    employee_id: str = "",
    employee_type: str = "",
    employee_hire_date: str = "",
    usage_location: str = "",
    preferred_language: str = "",
    preferred_data_location: str = "",
    on_premises_sync: str = "False",
    on_premises_last_sync: str = "",
    on_premises_dn: str = "",
    on_premises_immutable_id: str = "",
    extension_attributes: str = "",
    manager_display_name: str = "",
    manager_upn: str = "",
    sponsors: str = "",
    manager_status: str = "200",
    manager_error: str = "",
    sponsors_status: str = "200",
    sponsors_error: str = "",
    **extra,
) -> dict[str, str]:
    row = {
        "Id": entra_id,
        "DisplayName": display_name,
        "GivenName": given_name,
        "Surname": surname,
        "UPN": upn,
        "Mail": mail,
        "MailNickname": mail_nickname,
        "UserType": user_type,
        "AccountEnabled": account_enabled,
        "CreatedDateTime": created,
        "Identities": identities,
        "StreetAddress": street_address,
        "City": city,
        "State": state,
        "PostalCode": postal_code,
        "Country": country,
        "BusinessPhones": business_phones,
        "MobilePhone": mobile_phone,
        "OtherMails": other_mails,
        "ProxyAddresses": proxy_addresses,
        "IMAddresses": im_addresses,
        "JobTitle": job_title,
        "CompanyName": company_name,
        "Department": department,
        "OfficeLocation": office_location,
        "EmployeeId": employee_id,
        "EmployeeType": employee_type,
        "EmployeeHireDate": employee_hire_date,
        "UsageLocation": usage_location,
        "PreferredLanguage": preferred_language,
        "PreferredDataLocation": preferred_data_location,
        "OnPremisesSyncEnabled": on_premises_sync,
        "OnPremisesLastSyncDateTime": on_premises_last_sync,
        "OnPremisesDistinguishedName": on_premises_dn,
        "OnPremisesImmutableId": on_premises_immutable_id,
        "ExtensionAttributes": extension_attributes,
        "ManagerDisplayName": manager_display_name,
        "ManagerUPN": manager_upn,
        "Sponsors": sponsors,
        "ManagerStatus": manager_status,
        "ManagerError": manager_error,
        "SponsorsStatus": sponsors_status,
        "SponsorsError": sponsors_error,
    }
    row.update(extra)
    return row


class EntraAccessPackageComparisonTests(unittest.TestCase):
    FAMILY = "Entra_Access_Packages"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = access_package_row("template-package", "Template Package")
        for path, rows in (
            (root / "Entra_Access_Packages_20260731-042100.csv", baseline_rows),
            (root / "Entra_Access_Packages_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        self.assertEqual(
            suggested_key(snapshots[0].headers, self.FAMILY),
            "Access Package + Policy",
        )
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            "Access Package + Policy",
            self.FAMILY,
        )

    def test_two_policies_under_same_package_remain_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                access_package_row(
                    "pkg-1",
                    "Developer Access",
                    policy_id="policy-1",
                    policy_name="Standard Approval",
                ),
                access_package_row(
                    "pkg-1",
                    "Developer Access",
                    policy_id="policy-2",
                    policy_name="Manager Approval",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, rows)
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_same_package_id_does_not_collapse_policy_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                access_package_row(
                    "pkg-1",
                    "Developer Access",
                    policy_id="policy-1",
                    policy_name="Policy One",
                ),
                access_package_row(
                    "pkg-1",
                    "Developer Access",
                    policy_id="policy-2",
                    policy_name="Policy Two",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, rows)
            composite = self._compare(baseline, latest)
            package_only = compare_snapshots(baseline, latest, "AccessPackageId")
            self.assertEqual(composite.stable, 2)
            self.assertEqual(package_only.stable, 1)

    def test_changed_policy_row_reports_friendly_identity_and_column(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    access_package_row(
                        "pkg-1",
                        "Developer Access",
                        policy_id="policy-1",
                        policy_name="Standard Approval",
                        modified="2025-09-23T09:43:10Z",
                        policy_status="Enabled",
                    ),
                ],
                [
                    access_package_row(
                        "pkg-1",
                        "Developer Access",
                        policy_id="policy-1",
                        policy_name="Standard Approval",
                        modified="2026-08-13T13:42:53Z",
                        policy_status="Disabled",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(detail_identity(changed), "Developer Access → Standard Approval")
            self.assertEqual(changed["column"], "ModifiedDateTime")
            status_change = next(
                detail for detail in result.details if detail["column"] == "PolicyStatus"
            )
            self.assertEqual(status_change["before"], "Enabled")
            self.assertEqual(status_change["after"], "Disabled")

    def test_package_without_policy_uses_package_name_only(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    access_package_row(
                        "pkg-1",
                        "Developer Access",
                        package_description="Baseline description",
                    ),
                ],
                [
                    access_package_row(
                        "pkg-1",
                        "Developer Access",
                        package_description="Updated description",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(detail_identity(changed), "Developer Access")
            self.assertEqual(changed["column"], "AccessPackageDescription")

    def test_package_without_policy_is_not_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [access_package_row("pkg-1", "Developer Access")]
            baseline, latest = self._write_pair(Path(directory), rows, rows)
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 1)

    def test_policy_added_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    access_package_row(
                        "pkg-1",
                        "Developer Access",
                        policy_id="policy-1",
                        policy_name="Standard Approval",
                    ),
                ],
                [
                    access_package_row(
                        "pkg-1",
                        "Developer Access",
                        policy_id="policy-2",
                        policy_name="Manager Approval",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.added, 1)
            self.assertEqual(result.removed, 1)
            added = next(detail for detail in result.details if detail["change"] == "Added")
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(added), "Developer Access → Manager Approval")
            self.assertEqual(detail_identity(removed), "Developer Access → Standard Approval")

    def test_row_reordering_does_not_create_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline_rows = [
                access_package_row(
                    "pkg-1",
                    "Developer Access",
                    policy_id="policy-1",
                    policy_name="Policy One",
                ),
                access_package_row(
                    "pkg-1",
                    "Developer Access",
                    policy_id="policy-2",
                    policy_name="Policy Two",
                ),
            ]
            latest_rows = list(reversed(baseline_rows))
            baseline, latest = self._write_pair(Path(directory), baseline_rows, latest_rows)
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_generic_family_key_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")
            result = compare_snapshots(snapshots[0], snapshots[1], "UPN")
            self.assertEqual(result.changed, 1)


class EntraUserActivityComparisonTests(unittest.TestCase):
    FAMILY = "Entra_Users_Activity"

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = user_activity_row("template-user", upn="template@example.com")
        for path, rows in (
            (root / "Entra_Users_Activity_20260731-042100.csv", baseline_rows),
            (root / "Entra_Users_Activity_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, self.FAMILY),
            self.FAMILY,
        )

    def test_user_id_is_preferred_over_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [user_activity_row("user-1", display_name="Ada", upn="ada@example.com")],
                [user_activity_row("user-1", display_name="Ada", upn="ada@example.com")],
            )
            self.assertEqual(suggested_key(baseline.headers, self.FAMILY), "UserId")

    def test_upn_rename_stays_same_user(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_activity_row(
                        "user-1",
                        display_name="Ada Lovelace",
                        upn="ada.old@example.com",
                    ),
                ],
                [
                    user_activity_row(
                        "user-1",
                        display_name="Ada Lovelace",
                        upn="ada.new@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "UPN")
            self.assertEqual(changed["before"], "ada.old@example.com")
            self.assertEqual(changed["after"], "ada.new@example.com")

    def test_distinct_user_ids_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_activity_row("user-1", display_name="Ada Lovelace", upn="ada@example.com"),
                    user_activity_row("user-2", display_name="Ada Lovelace", upn="ada.clone@example.com"),
                ],
                [
                    user_activity_row("user-1", display_name="Ada Lovelace", upn="ada@example.com"),
                    user_activity_row("user-2", display_name="Ada Lovelace", upn="ada.clone@example.com"),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_row_reordering_does_not_create_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                user_activity_row("user-1", display_name="Ada", upn="ada@example.com"),
                user_activity_row("user-2", display_name="Grace", upn="grace@example.com"),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, list(reversed(rows)))
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_display_name_change_remains_same_user(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [user_activity_row("user-1", display_name="Ada", upn="ada@example.com")],
                [user_activity_row("user-1", display_name="Ada Lovelace", upn="ada@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "DisplayName")
            self.assertEqual(detail_identity(changed), "Ada Lovelace · ada@example.com")

    def test_account_enabled_change_remains_same_user(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [user_activity_row("user-1", display_name="Ada", upn="ada@example.com", account_enabled="True")],
                [user_activity_row("user-1", display_name="Ada", upn="ada@example.com", account_enabled="False")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "AccountEnabled")
            self.assertEqual(changed["before"], "True")
            self.assertEqual(changed["after"], "False")

    def test_added_user_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [user_activity_row("user-1", display_name="Ada Lovelace", upn="ada@example.com")],
            )
            result = self._compare(baseline, latest)
            added = next(detail for detail in result.details if detail["change"] == "Added")
            self.assertEqual(detail_identity(added), "Ada Lovelace · ada@example.com")
            self.assertEqual(added["user_id"], "user-1")

    def test_removed_user_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [user_activity_row("user-1", display_name="Grace Hopper", upn="grace@example.com")],
                [],
            )
            result = self._compare(baseline, latest)
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(removed), "Grace Hopper · grace@example.com")

    def test_legacy_snapshot_without_user_id_falls_back_to_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_headers = {
                "DisplayName": "Ada",
                "UPN": "ada@example.com",
                "Mail": "",
                "UserType": "Member",
                "AccountEnabled": "True",
                "LastSuccessfulSignInDateTime": "2026-08-12 12:57:40",
            }
            for stamp, upn in (
                ("20260731-042100", "ada.old@example.com"),
                ("20260804-042100", "ada.new@example.com"),
            ):
                row = dict(legacy_headers)
                row["UPN"] = upn
                write_report(root / f"Entra_Users_Activity_{stamp}.csv", [row])
            snapshots = scan_report_history(root)[self.FAMILY]
            self.assertEqual(suggested_key(snapshots[0].headers, self.FAMILY), "UPN")
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                "UPN",
                self.FAMILY,
            )
            self.assertEqual((result.added, result.removed), (1, 1))

    def test_generic_family_key_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")


class EntraUserActivityPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_identity_formats(self):
        from diffasaurus.ui.comparison_presentation import identity_display_text

        detail = {
            "change": "Changed",
            "key": "user-1",
            "identity": "Ada Lovelace · ada@example.com",
            "column": "LastSuccessfulSignInDateTime",
            "before": "2026-08-12 12:57:40",
            "after": "2026-08-13 15:18:14",
            "user_id": "user-1",
            "UPN": "ada@example.com",
        }
        self.assertEqual(
            identity_display_text(detail, "Entra_Users_Activity"),
            "Ada Lovelace · ada@example.com",
        )
        self.assertEqual(
            identity_display_text(
                {"identity": "ada@example.com", "key": "ada@example.com"},
                "Entra_Users_Activity",
            ),
            "ada@example.com",
        )
        self.assertEqual(
            identity_display_text(
                {"identity": "Ada Lovelace", "key": "user-1"},
                "Entra_Users_Activity",
            ),
            "Ada Lovelace",
        )
        self.assertEqual(
            identity_display_text(
                {"identity": "user-1", "key": "user-1"},
                "Entra_Users_Activity",
            ),
            "user-1",
        )

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        self.assertEqual(
            property_display_text("LastNonInteractiveSignInDateTime", "Entra_Users_Activity"),
            "Last non-interactive sign-in",
        )
        self.assertEqual(
            property_display_text("LastSuccessfulSignInDateTime", "Entra_Users_Activity"),
            "Last successful sign-in",
        )
        self.assertEqual(
            property_display_text("AccountEnabled", "Entra_Users_Activity"),
            "Account enabled",
        )
        self.assertEqual(
            property_display_text("", "Entra_Users_Activity", change="Added"),
            "User",
        )
        self.assertEqual(
            property_display_text("FutureField", "Entra_Users_Activity"),
            "FutureField",
        )
        tooltip = property_tooltip("LastSuccessfulSignInDateTime", "Entra_Users_Activity")
        self.assertIn("Last successful sign-in", tooltip)
        self.assertIn("CSV field: LastSuccessfulSignInDateTime", tooltip)

    def test_identity_tooltip_includes_user_id(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "Ada Lovelace · ada@example.com",
            "user_id": "00000000-0000-0000-0000-000000000001",
            "UPN": "ada@example.com",
            "key": "00000000-0000-0000-0000-000000000001",
        }
        tooltip = identity_tooltip(detail, "Entra_Users_Activity")
        self.assertIn("Ada Lovelace · ada@example.com", tooltip)
        self.assertIn("UserId: 00000000-0000-0000-0000-000000000001", tooltip)
        self.assertIn("UPN: ada@example.com", tooltip)

    def test_recent_changes_detail_table_uses_friendly_property(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = "Entra_Users_Activity"
        section._details = ComparisonSummary(
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
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(
            section.detail_table.item(0, 1).text(),
            "Ada Lovelace · ada@example.com",
        )
        self.assertEqual(
            section.detail_table.item(0, 2).text(),
            "Last successful sign-in",
        )

    def test_recent_changes_summary_uses_user_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family="Entra_Users_Activity",
            status="changed",
            baseline=None,
            latest=None,
            key_column="UserId",
            summary=ComparisonSummary(added=1, removed=1, changed=1063, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "1 user added · 1 user removed · 1,063 users changed",
        )

    def test_comparison_summary_unit_uses_users(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit("Entra_Users_Activity"), "users")


class EntraAuthMethodsHybridComparisonTests(unittest.TestCase):
    FAMILY = "Entra_Users_AuthenticationMethods_Hybrid"

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = auth_methods_hybrid_row("template-id", upn="template@example.com")
        for path, rows in (
            (
                root / "Entra_Users_AuthenticationMethods_Hybrid_20260731-042100.csv",
                baseline_rows,
            ),
            (
                root / "Entra_Users_AuthenticationMethods_Hybrid_20260804-042100.csv",
                latest_rows,
            ),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, self.FAMILY),
            self.FAMILY,
        )

    def test_microsoft_report_id_is_preferred_over_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada",
                        upn="ada@example.com",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada",
                        upn="ada@example.com",
                    ),
                ],
            )
            self.assertEqual(suggested_key(baseline.headers, self.FAMILY), "MicrosoftReportId")

    def test_upn_rename_stays_same_user(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada.old@example.com",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada.new@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "UPN")
            self.assertEqual(changed["before"], "ada.old@example.com")
            self.assertEqual(changed["after"], "ada.new@example.com")

    def test_changed_identity_uses_latest_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada",
                        upn="ada@example.com",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            changed = next(detail for detail in result.details if detail["column"] == "DisplayName")
            self.assertEqual(detail_identity(changed), "Ada Lovelace · ada@example.com")

    def test_added_user_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            added = next(detail for detail in result.details if detail["change"] == "Added")
            self.assertEqual(detail_identity(added), "Ada Lovelace · ada@example.com")
            self.assertEqual(
                added["microsoft_report_id"],
                "00000000-0000-0000-0000-000000000001",
            )

    def test_removed_user_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Grace Hopper",
                        upn="grace@example.com",
                    ),
                ],
                [],
            )
            result = self._compare(baseline, latest)
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(removed), "Grace Hopper · grace@example.com")

    def test_last_updated_only_change_is_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        last_updated="2026-08-12T12:00:00Z",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        last_updated="2026-08-13T15:18:14Z",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 0))
            self.assertFalse(any(detail["column"] == "LastUpdatedDateTime" for detail in result.details))

    def test_real_mfa_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        is_mfa_registered="False",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        is_mfa_registered="True",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "IsMfaRegistered")
            self.assertEqual(changed["before"], "False")
            self.assertEqual(changed["after"], "True")

    def test_last_updated_plus_real_change_emits_only_real_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        default_mfa_method="push",
                        last_updated="2026-08-12T12:00:00Z",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        default_mfa_method="oath",
                        last_updated="2026-08-13T15:18:14Z",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"DefaultMfaMethod"})

    def test_collection_reorder_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        methods_registered="email ; microsoftAuthenticatorPush",
                        authentication_methods="email ; microsoftAuthenticatorPush",
                        system_preferred_methods="push ; oath",
                        system_preferred_method="push ; oath",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        methods_registered="microsoftAuthenticatorPush ; email",
                        authentication_methods="microsoftAuthenticatorPush ; email",
                        system_preferred_methods="oath ; push",
                        system_preferred_method="oath ; push",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_collection_content_change_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        methods_registered="email ; microsoftAuthenticatorPush",
                    ),
                ],
                [
                    auth_methods_hybrid_row(
                        "00000000-0000-0000-0000-000000000001",
                        display_name="Ada Lovelace",
                        upn="ada@example.com",
                        methods_registered="email ; oath",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "MethodsRegistered")
            self.assertEqual(changed["before"], "email ; microsoftAuthenticatorPush")
            self.assertEqual(changed["after"], "email ; oath")

    def test_legacy_snapshot_without_microsoft_report_id_falls_back_to_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_headers = {
                "DisplayName": "Ada",
                "UPN": "ada@example.com",
                "UserType": "Member",
                "AccountEnabled": "True",
                "IsMfaRegistered": "True",
            }
            for stamp, upn in (
                ("20260731-042100", "ada.old@example.com"),
                ("20260804-042100", "ada.new@example.com"),
            ):
                row = dict(legacy_headers)
                row["UPN"] = upn
                write_report(
                    root / f"Entra_Users_AuthenticationMethods_Hybrid_{stamp}.csv",
                    [row],
                )
            snapshots = scan_report_history(root)[self.FAMILY]
            self.assertEqual(suggested_key(snapshots[0].headers, self.FAMILY), "UPN")
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                "UPN",
                self.FAMILY,
            )
            self.assertEqual((result.added, result.removed), (1, 1))

    def test_generic_family_still_compares_last_updated_datetime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "Department": "R&D",
                        "LastUpdatedDateTime": "2026-08-12T12:00:00Z",
                    },
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "Department": "R&D",
                        "LastUpdatedDateTime": "2026-08-13T15:18:14Z",
                    },
                ],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            result = compare_snapshots(snapshots[0], snapshots[1], "UPN")
            changed_columns = {
                detail["column"] for detail in result.details if detail["change"] == "Changed"
            }
            self.assertIn("LastUpdatedDateTime", changed_columns)


class EntraAuthMethodsHybridPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        family = "Entra_Users_AuthenticationMethods_Hybrid"
        self.assertEqual(property_display_text("IsMfaRegistered", family), "MFA registered")
        self.assertEqual(property_display_text("DefaultMfaMethod", family), "Default MFA method")
        self.assertEqual(
            property_display_text("MethodsRegistered", family),
            "Methods registered",
        )
        self.assertEqual(
            property_display_text("AuthenticationMethods", family),
            "Authentication methods",
        )
        self.assertEqual(
            property_display_text("", family, change="Added"),
            "User",
        )
        tooltip = property_tooltip("IsMfaCapable", family)
        self.assertIn("MFA capable", tooltip)
        self.assertIn("CSV field: IsMfaCapable", tooltip)

    def test_identity_tooltip_includes_microsoft_report_id(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "Ada Lovelace · ada@example.com",
            "microsoft_report_id": "00000000-0000-0000-0000-000000000001",
            "UPN": "ada@example.com",
            "key": "00000000-0000-0000-0000-000000000001",
        }
        tooltip = identity_tooltip(detail, "Entra_Users_AuthenticationMethods_Hybrid")
        self.assertIn("Ada Lovelace · ada@example.com", tooltip)
        self.assertIn("MicrosoftReportId: 00000000-0000-0000-0000-000000000001", tooltip)
        self.assertIn("UPN: ada@example.com", tooltip)

    def test_recent_changes_detail_table_uses_friendly_property(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = "Entra_Users_AuthenticationMethods_Hybrid"
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": "00000000-0000-0000-0000-000000000001",
                    "identity": "Ada Lovelace · ada@example.com",
                    "column": "IsMfaRegistered",
                    "before": "False",
                    "after": "True",
                    "microsoft_report_id": "00000000-0000-0000-0000-000000000001",
                    "UPN": "ada@example.com",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(
            section.detail_table.item(0, 1).text(),
            "Ada Lovelace · ada@example.com",
        )
        self.assertEqual(section.detail_table.item(0, 2).text(), "MFA registered")

    def test_recent_changes_summary_uses_user_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family="Entra_Users_AuthenticationMethods_Hybrid",
            status="changed",
            baseline=None,
            latest=None,
            key_column="MicrosoftReportId",
            summary=ComparisonSummary(added=1, removed=1, changed=3, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "1 user added · 1 user removed · 3 users changed",
        )

    def test_comparison_summary_unit_uses_users(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(
            comparison_summary_unit("Entra_Users_AuthenticationMethods_Hybrid"),
            "users",
        )


class EntraUserPropertiesComparisonTests(unittest.TestCase):
    FAMILY = "Entra_Users_Properties"

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = user_properties_row("template-id", upn="template@example.com")
        for path, rows in (
            (root / "Entra_Users_Properties_20260731-042100.csv", baseline_rows),
            (root / "Entra_Users_Properties_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, self.FAMILY),
            self.FAMILY,
        )

    def test_id_is_preferred_over_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [user_properties_row("user-1", display_name="Ada", upn="ada@example.com")],
                [user_properties_row("user-1", display_name="Ada", upn="ada@example.com")],
            )
            self.assertEqual(suggested_key(baseline.headers, self.FAMILY), "Id")

    def test_upn_rename_stays_same_user(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada Lovelace",
                        upn="ada.old@example.com",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada Lovelace",
                        upn="ada.new@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "UPN")

    def test_multiple_property_changes_count_as_one_changed_user(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Yan LECOZ",
                        upn="yan@example.com",
                        job_title="Développeur Aubay",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Yan LE COZ",
                        upn="yan@example.com",
                        job_title="Développeur",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed_columns = {
                detail["column"] for detail in result.details if detail["change"] == "Changed"
            }
            self.assertEqual(changed_columns, {"DisplayName", "JobTitle"})

    def test_added_user_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Paul Example",
                        upn="paul@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            added = next(detail for detail in result.details if detail["change"] == "Added")
            self.assertEqual(detail_identity(added), "Paul Example · paul@example.com")
            self.assertEqual(added["user_id"], "user-1")

    def test_removed_user_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Jane Example",
                        upn="jane@example.com",
                    ),
                ],
                [],
            )
            result = self._compare(baseline, latest)
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(removed), "Jane Example · jane@example.com")

    def test_diagnostic_status_fields_do_not_create_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        manager_status="500",
                        manager_error="Server error",
                        sponsors_status="503",
                        sponsors_error="Unavailable",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        manager_status="429",
                        manager_error="Too many requests",
                        sponsors_status="504",
                        sponsors_error="Gateway timeout",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)
            changed_columns = {detail["column"] for detail in result.details}
            self.assertFalse(
                changed_columns
                & {"ManagerStatus", "ManagerError", "SponsorsStatus", "SponsorsError"}
            )

    def test_manager_known_zero_vs_successful_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        manager_status="404",
                        manager_error="Resource not found",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        manager_display_name="Grace Hopper",
                        manager_upn="grace@example.com",
                        manager_status="200",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed_columns = {
                detail["column"] for detail in result.details if detail["change"] == "Changed"
            }
            self.assertEqual(changed_columns, {"ManagerDisplayName", "ManagerUPN"})

    def test_manager_retrieval_failure_does_not_create_fake_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        manager_display_name="Grace Hopper",
                        manager_upn="grace@example.com",
                        manager_status="200",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        manager_status="503",
                        manager_error="Service unavailable",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 0)
            self.assertFalse(
                any(detail["column"] in {"ManagerDisplayName", "ManagerUPN"} for detail in result.details)
            )

    def test_sponsor_retrieval_failure_does_not_create_fake_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        sponsors="Bob Example",
                        sponsors_status="200",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        sponsors_status="500",
                        sponsors_error="Server error",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 0)
            self.assertFalse(any(detail["column"] == "Sponsors" for detail in result.details))

    def test_sponsor_known_empty_result_remains_trustworthy(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        sponsors="",
                        sponsors_status="200",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        sponsors="Bob Example",
                        sponsors_status="200",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "Sponsors")
            self.assertEqual(changed["before"], "")
            self.assertEqual(changed["after"], "Bob Example")

    def test_collection_reorder_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        business_phones="+33 1 23 45 67 ; +33 6 12 34 56",
                        proxy_addresses="SMTP:ada@example.com ; smtp:alias@example.com",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        business_phones="+33 6 12 34 56 ; +33 1 23 45 67",
                        proxy_addresses="smtp:alias@example.com ; SMTP:ada@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_collection_content_change_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        other_mails="one@example.com ; two@example.com",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        other_mails="one@example.com ; three@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "OtherMails")
            self.assertEqual(changed["before"], "one@example.com ; two@example.com")
            self.assertEqual(changed["after"], "one@example.com ; three@example.com")

    def test_extension_attributes_order_is_semantic(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        extension_attributes="extensionAttribute1=A ; extensionAttribute2=B",
                    ),
                ],
                [
                    user_properties_row(
                        "user-1",
                        display_name="Ada",
                        upn="ada@example.com",
                        extension_attributes="extensionAttribute2=B ; extensionAttribute1=A",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(
                detail for detail in result.details if detail["column"] == "ExtensionAttributes"
            )
            self.assertNotEqual(changed["before"], changed["after"])

    def test_legacy_snapshot_without_id_falls_back_to_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_headers = {
                "DisplayName": "Ada",
                "UPN": "ada@example.com",
                "UserType": "Member",
                "AccountEnabled": "True",
            }
            for stamp, upn in (
                ("20260731-042100", "ada.old@example.com"),
                ("20260804-042100", "ada.new@example.com"),
            ):
                row = dict(legacy_headers)
                row["UPN"] = upn
                write_report(root / f"Entra_Users_Properties_{stamp}.csv", [row])
            snapshots = scan_report_history(root)[self.FAMILY]
            self.assertEqual(suggested_key(snapshots[0].headers, self.FAMILY), "UPN")
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                "UPN",
                self.FAMILY,
            )
            self.assertEqual((result.added, result.removed), (1, 1))

    def test_generic_family_still_compares_last_updated_datetime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "Department": "R&D",
                        "LastUpdatedDateTime": "2026-08-12T12:00:00Z",
                    },
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "Department": "R&D",
                        "LastUpdatedDateTime": "2026-08-13T15:18:14Z",
                    },
                ],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            result = compare_snapshots(snapshots[0], snapshots[1], "UPN")
            changed_columns = {
                detail["column"] for detail in result.details if detail["change"] == "Changed"
            }
            self.assertIn("LastUpdatedDateTime", changed_columns)


class EntraUserPropertiesPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        family = "Entra_Users_Properties"
        self.assertEqual(property_display_text("DisplayName", family), "Display name")
        self.assertEqual(property_display_text("JobTitle", family), "Job title")
        self.assertEqual(property_display_text("UPN", family), "User principal name")
        self.assertEqual(property_display_text("", family, change="Added"), "User")
        tooltip = property_tooltip("ManagerDisplayName", family)
        self.assertIn("Manager display name", tooltip)
        self.assertIn("CSV field: ManagerDisplayName", tooltip)

    def test_identity_tooltip_includes_id(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "Yan LE COZ · yan@example.com",
            "user_id": "00000000-0000-0000-0000-000000000001",
            "UPN": "yan@example.com",
            "key": "00000000-0000-0000-0000-000000000001",
        }
        tooltip = identity_tooltip(detail, "Entra_Users_Properties")
        self.assertIn("Yan LE COZ · yan@example.com", tooltip)
        self.assertIn("Id: 00000000-0000-0000-0000-000000000001", tooltip)
        self.assertIn("UPN: yan@example.com", tooltip)

    def test_recent_changes_detail_table_uses_friendly_property(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = "Entra_Users_Properties"
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": "user-1",
                    "identity": "Yan LE COZ · yan@example.com",
                    "column": "JobTitle",
                    "before": "Développeur Aubay",
                    "after": "Développeur",
                    "user_id": "user-1",
                    "UPN": "yan@example.com",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(section.detail_table.item(0, 1).text(), "Yan LE COZ · yan@example.com")
        self.assertEqual(section.detail_table.item(0, 2).text(), "Job title")

    def test_recent_changes_summary_uses_user_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family="Entra_Users_Properties",
            status="changed",
            baseline=None,
            latest=None,
            key_column="Id",
            summary=ComparisonSummary(added=1, removed=1, changed=1, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "1 user added · 1 user removed · 1 user changed",
        )

    def test_comparison_summary_unit_uses_users(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit("Entra_Users_Properties"), "users")


ANDROID_AAD_ONE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ANDROID_AAD_TWO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ANDROID_INTUNE_ONE = "11111111-1111-1111-1111-111111111111"
ANDROID_INTUNE_TWO = "22222222-2222-2222-2222-222222222222"

ANDROID_DEVICE_HEADERS = (
    "DeviceName",
    "ManagementName",
    "IntuneDeviceId",
    "EntraDeviceId",
    "SerialNumber",
    "Manufacturer",
    "Model",
    "OperatingSystem",
    "OSVersion",
    "AndroidSecurityPatchLevel",
    "UserDisplayName",
    "UserPrincipalName",
    "EmailAddress",
    "PhoneNumber",
    "IMEI",
    "MEID",
    "ICCID",
    "SubscriberCarrier",
    "WiFiMacAddress",
    "OwnerType",
    "ManagementAgent",
    "DeviceEnrollmentType",
    "EnrollmentProfileName",
    "DeviceRegistrationState",
    "EnrolledDateTime",
    "ManagementCertificateExpiration",
    "LastSyncDateTime",
    "DaysSinceLastSync",
    "DeviceActivityStatus",
    "ComplianceState",
    "ComplianceGracePeriodExpiration",
    "AzureADRegistered",
    "IsEncrypted",
    "Rooted",
    "PartnerReportedThreatState",
    "EASActivated",
    "EASDeviceId",
    "EASActivationDateTime",
    "TotalStorageGB",
    "FreeStorageGB",
)


def android_device_row(
    *,
    entra_id: str = ANDROID_AAD_ONE,
    intune_id: str = ANDROID_INTUNE_ONE,
    device_name: str = "Pixel-7",
    serial: str = "SN-ANDROID-1",
    os_version: str = "14",
    patch: str = "2026-07-01",
    compliance: str = "compliant",
    encrypted: str = "True",
    rooted: str = "False",
    owner: str = "company",
    activity: str = "Active <=30d",
    user_upn: str = "ada@example.com",
    manufacturer: str = "Google",
    model: str = "Pixel 7",
    imei: str = "",
    phone: str = "",
    iccid: str = "",
    last_sync: str = "2026-08-01T00:00:00Z",
    days_since_sync: str = "5",
    free_storage: str = "64",
    **extra,
) -> dict[str, str]:
    row = {
        "DeviceName": device_name,
        "ManagementName": device_name,
        "IntuneDeviceId": intune_id,
        "EntraDeviceId": entra_id,
        "SerialNumber": serial,
        "Manufacturer": manufacturer,
        "Model": model,
        "OperatingSystem": "Android",
        "OSVersion": os_version,
        "AndroidSecurityPatchLevel": patch,
        "UserDisplayName": "Ada",
        "UserPrincipalName": user_upn,
        "EmailAddress": user_upn,
        "PhoneNumber": phone,
        "IMEI": imei,
        "MEID": "",
        "ICCID": iccid,
        "SubscriberCarrier": "",
        "WiFiMacAddress": "",
        "OwnerType": owner,
        "ManagementAgent": "mdm",
        "DeviceEnrollmentType": "androidEnterpriseFullyManaged",
        "EnrollmentProfileName": "Corporate Android",
        "DeviceRegistrationState": "registered",
        "EnrolledDateTime": "2026-01-01T00:00:00Z",
        "ManagementCertificateExpiration": "",
        "LastSyncDateTime": last_sync,
        "DaysSinceLastSync": days_since_sync,
        "DeviceActivityStatus": activity,
        "ComplianceState": compliance,
        "ComplianceGracePeriodExpiration": "",
        "AzureADRegistered": "True",
        "IsEncrypted": encrypted,
        "Rooted": rooted,
        "PartnerReportedThreatState": "",
        "EASActivated": "False",
        "EASDeviceId": "",
        "EASActivationDateTime": "",
        "TotalStorageGB": "128",
        "FreeStorageGB": free_storage,
    }
    row.update(extra)
    return row


class IntuneAndroidDevicesComparisonTests(unittest.TestCase):
    FAMILY = "Intune_Android_Devices"
    REPORT_FAMILY = "Intune_Android_Devices_Report"

    def _write_pair(
        self,
        root: Path,
        baseline_rows,
        latest_rows,
        *,
        family: str | None = None,
    ):
        family = family or self.FAMILY
        template = android_device_row()
        for path, rows in (
            (root / f"{family}_20260731-042100.csv", baseline_rows),
            (root / f"{family}_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[family]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest, family: str | None = None):
        family = family or baseline.family
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, family),
            family,
        )

    def test_report_family_parses_report_suffix(self):
        path = Path("Intune_Android_Devices_Report_20260812-170000.csv")
        self.assertEqual(report_family(path), self.REPORT_FAMILY)

    def test_entra_device_id_is_preferred_key(self):
        headers = list(ANDROID_DEVICE_HEADERS)
        self.assertEqual(suggested_key(headers, self.FAMILY), "EntraDeviceId")
        self.assertEqual(suggested_key(headers, self.REPORT_FAMILY), "EntraDeviceId")

    def test_legacy_report_family_uses_same_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, latest = self._write_pair(
                root,
                [android_device_row(compliance="compliant")],
                [android_device_row(compliance="noncompliant")],
                family=self.REPORT_FAMILY,
            )
            result = self._compare(baseline, latest, self.REPORT_FAMILY)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "ComplianceState")
            self.assertEqual(detail_identity(changed), "Pixel-7 · SN-ANDROID-1")

    def test_fallback_to_intune_device_id_when_entra_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(entra_id="", intune_id=ANDROID_INTUNE_ONE, compliance="compliant")],
                [android_device_row(entra_id="", intune_id=ANDROID_INTUNE_ONE, compliance="noncompliant")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_fallback_to_serial_number_when_entra_and_intune_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    android_device_row(
                        entra_id="",
                        intune_id="",
                        serial="SN-ONLY-1",
                        compliance="compliant",
                    ),
                ],
                [
                    android_device_row(
                        entra_id="",
                        intune_id="",
                        serial="SN-ONLY-1",
                        compliance="noncompliant",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_device_name_rename_remains_one_changed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(device_name="Old Pixel Name")],
                [android_device_row(device_name="New Pixel Name")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "DeviceName")
            self.assertEqual(detail_identity(changed), "New Pixel Name · SN-ANDROID-1")

    def test_friendly_added_and_removed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(device_name="Removed Pixel", serial="SN-REMOVE")],
                [android_device_row(entra_id=ANDROID_AAD_TWO, intune_id=ANDROID_INTUNE_TWO, device_name="Added Pixel", serial="SN-ADD")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed), (1, 1))
            added = next(detail for detail in result.details if detail["change"] == "Added")
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(added), "Added Pixel · SN-ADD")
            self.assertEqual(detail_identity(removed), "Removed Pixel · SN-REMOVE")

    def test_sensitive_ids_are_not_used_as_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    android_device_row(
                        device_name="",
                        serial="",
                        entra_id="",
                        intune_id=ANDROID_INTUNE_ONE,
                        imei="IMEI-ONLY",
                        phone="555-0100",
                        iccid="ICCID-ONLY",
                    ),
                ],
                [
                    android_device_row(
                        device_name="",
                        serial="",
                        entra_id="",
                        intune_id=ANDROID_INTUNE_ONE,
                        imei="IMEI-ONLY",
                        phone="555-0100",
                        iccid="ICCID-ONLY",
                        compliance="noncompliant",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            identity = detail_identity(changed)
            self.assertEqual(identity, ANDROID_INTUNE_ONE)
            self.assertNotIn("IMEI", identity)
            self.assertNotIn("555-0100", identity)
            self.assertNotIn("ICCID", identity)

    def test_days_since_last_sync_only_drift_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(days_since_sync="5", last_sync="2026-08-01T00:00:00Z")],
                [android_device_row(days_since_sync="6", last_sync="2026-08-01T00:00:00Z")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_last_sync_date_time_excluded_from_semantic_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(last_sync="2026-08-01T00:00:00Z")],
                [android_device_row(last_sync="2026-08-04T12:00:00Z")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_collection_time_drift_does_not_flood_changed_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline_rows = []
            latest_rows = []
            for index in range(45):
                entra_id = f"{index:08x}-0000-0000-0000-000000000001"
                intune_id = f"{index:08x}-1111-1111-1111-111111111111"
                baseline_rows.append(
                    android_device_row(
                        entra_id=entra_id,
                        intune_id=intune_id,
                        device_name=f"Device-{index}",
                        serial=f"SN-{index}",
                        last_sync="2026-08-01T00:00:00Z",
                        days_since_sync="5",
                    )
                )
                latest_rows.append(
                    android_device_row(
                        entra_id=entra_id,
                        intune_id=intune_id,
                        device_name=f"Device-{index}",
                        serial=f"SN-{index}",
                        last_sync=f"2026-08-0{index % 4 + 1}T12:00:00Z",
                        days_since_sync=str(5 + index),
                    )
                )
            baseline, latest = self._write_pair(Path(directory), baseline_rows, latest_rows)
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 0))

    def test_device_activity_status_threshold_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(activity="Active <=30d", days_since_sync="10")],
                [android_device_row(activity="Stale 31-90d", days_since_sync="45")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "DeviceActivityStatus")
            self.assertEqual(changed["before"], "Active <=30d")
            self.assertEqual(changed["after"], "Stale 31-90d")

    def test_posture_property_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(compliance="compliant", os_version="14")],
                [android_device_row(compliance="noncompliant", os_version="15")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertIn("ComplianceState", columns)
            self.assertIn("OSVersion", columns)

    def test_free_storage_gb_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [android_device_row(free_storage="64")],
                [android_device_row(free_storage="32")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "FreeStorageGB")
            self.assertEqual(changed["before"], "64")
            self.assertEqual(changed["after"], "32")

    def test_property_frequency_after_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline_rows = [
                android_device_row(
                    entra_id=ANDROID_AAD_ONE,
                    compliance="compliant",
                    os_version="14",
                    free_storage="64",
                    last_sync="2026-08-01T00:00:00Z",
                    days_since_sync="5",
                ),
                android_device_row(
                    entra_id=ANDROID_AAD_TWO,
                    intune_id=ANDROID_INTUNE_TWO,
                    device_name="Pixel-8",
                    serial="SN-ANDROID-2",
                    compliance="compliant",
                    os_version="14",
                    free_storage="48",
                    last_sync="2026-08-01T00:00:00Z",
                    days_since_sync="5",
                ),
            ]
            latest_rows = [
                android_device_row(
                    entra_id=ANDROID_AAD_ONE,
                    compliance="noncompliant",
                    os_version="14",
                    free_storage="64",
                    last_sync="2026-08-04T12:00:00Z",
                    days_since_sync="8",
                ),
                android_device_row(
                    entra_id=ANDROID_AAD_TWO,
                    intune_id=ANDROID_INTUNE_TWO,
                    device_name="Pixel-8",
                    serial="SN-ANDROID-2",
                    compliance="compliant",
                    os_version="15",
                    free_storage="40",
                    last_sync="2026-08-04T12:00:00Z",
                    days_since_sync="8",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), baseline_rows, latest_rows)
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 2)
            histogram: dict[str, int] = {}
            for detail in result.details:
                if detail["change"] != "Changed":
                    continue
                histogram[detail["column"]] = histogram.get(detail["column"], 0) + 1
            self.assertNotIn("LastSyncDateTime", histogram)
            self.assertNotIn("DaysSinceLastSync", histogram)
            self.assertEqual(histogram.get("ComplianceState"), 1)
            self.assertEqual(histogram.get("OSVersion"), 1)
            self.assertEqual(histogram.get("FreeStorageGB"), 1)

    def test_ios_last_sync_excluded_from_semantic_changes(self):
        ios_headers = list(ANDROID_DEVICE_HEADERS)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = android_device_row(last_sync="2026-08-01T00:00:00Z")
            for stamp, last_sync in (
                ("20260731-042100", "2026-08-01T00:00:00Z"),
                ("20260804-042100", "2026-08-04T12:00:00Z"),
            ):
                updated = dict(row)
                updated["LastSyncDateTime"] = last_sync
                write_report(root / f"Intune_iOS_Devices_{stamp}.csv", [updated])
            snapshots = scan_report_history(root)["Intune_iOS_Devices"]
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                suggested_key(ios_headers, "Intune_iOS_Devices"),
                "Intune_iOS_Devices",
            )
            self.assertEqual(result.total_changes, 0)

    def test_generic_family_key_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")


class IntuneAndroidDevicesPresentationTests(unittest.TestCase):
    FAMILY = "Intune_Android_Devices"
    REPORT_FAMILY = "Intune_Android_Devices_Report"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        self.assertEqual(property_display_text("DeviceName", self.FAMILY), "Device name")
        self.assertEqual(property_display_text("ComplianceState", self.FAMILY), "Compliance state")
        self.assertEqual(property_display_text("OSVersion", self.FAMILY), "OS version")
        self.assertEqual(property_display_text("", self.FAMILY, change="Added"), "Device")
        self.assertEqual(property_display_text("", self.REPORT_FAMILY, change="Removed"), "Device")
        self.assertEqual(property_display_text("FutureField", self.FAMILY), "FutureField")
        tooltip = property_tooltip("WiFiMacAddress", self.FAMILY)
        self.assertIn("Wi-Fi MAC address", tooltip)
        self.assertIn("CSV field: WiFiMacAddress", tooltip)

    def test_identity_tooltip_excludes_sensitive_ids(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "Pixel-7 · SN-ANDROID-1",
            "device_name": "Pixel-7",
            "serial_number": "SN-ANDROID-1",
            "entra_device_id": ANDROID_AAD_ONE,
            "intune_device_id": ANDROID_INTUNE_ONE,
            "UserPrincipalName": "ada@example.com",
            "IMEI": "secret-imei",
            "PhoneNumber": "555-0100",
            "key": ANDROID_AAD_ONE,
        }
        tooltip = identity_tooltip(detail, self.FAMILY)
        self.assertIn("Pixel-7 · SN-ANDROID-1", tooltip)
        self.assertIn("EntraDeviceId:", tooltip)
        self.assertIn("UserPrincipalName:", tooltip)
        self.assertNotIn("IMEI", tooltip)
        self.assertNotIn("555-0100", tooltip)

    def test_recent_changes_detail_table_uses_friendly_labels(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": ANDROID_AAD_ONE,
                    "identity": "Pixel-7 · SN-ANDROID-1",
                    "column": "ComplianceState",
                    "before": "compliant",
                    "after": "noncompliant",
                    "device_name": "Pixel-7",
                    "serial_number": "SN-ANDROID-1",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(section.detail_table.item(0, 1).text(), "Pixel-7 · SN-ANDROID-1")
        self.assertEqual(section.detail_table.item(0, 2).text(), "Compliance state")

    def test_recent_changes_summary_uses_device_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family=self.REPORT_FAMILY,
            status="changed",
            baseline=None,
            latest=None,
            key_column="EntraDeviceId",
            summary=ComparisonSummary(added=1, removed=2, changed=5, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "1 device added · 2 devices removed · 5 devices changed",
        )

    def test_comparison_summary_unit_uses_devices(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit(self.FAMILY), "devices")
        self.assertEqual(comparison_summary_unit(self.REPORT_FAMILY), "devices")


IOS_AAD_ONE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
IOS_AAD_TWO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
IOS_INTUNE_ONE = "11111111-1111-1111-1111-111111111111"
IOS_INTUNE_TWO = "22222222-2222-2222-2222-222222222222"
IOS_UDID_ONE = "00000000-0000-0000-0000-000000000001"

IOS_DEVICE_HEADERS = (
    "DeviceName",
    "ManagementName",
    "IntuneDeviceId",
    "EntraDeviceId",
    "UDID",
    "SerialNumber",
    "IMEI",
    "MEID",
    "Manufacturer",
    "Model",
    "OperatingSystem",
    "OSVersion",
    "UserDisplayName",
    "UserPrincipalName",
    "EmailAddress",
    "PhoneNumber",
    "OwnerType",
    "ManagementAgent",
    "ManagementState",
    "DeviceEnrollmentType",
    "EnrollmentProfileName",
    "EnrolledDateTime",
    "LastSyncDateTime",
    "DaysSinceLastSync",
    "DeviceActivityStatus",
    "ComplianceState",
    "AzureADRegistered",
    "IsSupervised",
    "IsEncrypted",
    "JailBroken",
    "EASActivated",
    "EASActivationId",
    "EASActivationDateTime",
    "SubscriberCarrier",
    "CellularTechnology",
    "WiFiMacAddress",
    "EthernetMacAddress",
    "ICCID",
    "TotalStorageGB",
    "FreeStorageGB",
    "ActivationLockBypassCode",
    "HasActivationBypassCode",
)


def ios_device_row(
    *,
    entra_id: str = IOS_AAD_ONE,
    intune_id: str = IOS_INTUNE_ONE,
    udid: str = IOS_UDID_ONE,
    device_name: str = "iPhone-15",
    serial: str = "SN-IOS-1",
    os_version: str = "17.5",
    compliance: str = "compliant",
    encrypted: str = "True",
    supervised: str = "True",
    jailbroken: str = "False",
    owner: str = "company",
    activity: str = "Active <=30d",
    user_upn: str = "ada@example.com",
    manufacturer: str = "Apple",
    model: str = "iPhone 15",
    imei: str = "",
    phone: str = "",
    iccid: str = "",
    last_sync: str = "2026-08-01T00:00:00Z",
    days_since_sync: str = "5",
    free_storage: str = "64",
    total_storage: str = "128",
    bypass_code: str = "",
    has_bypass_code: str = "No",
    **extra,
) -> dict[str, str]:
    row = {
        "DeviceName": device_name,
        "ManagementName": device_name,
        "IntuneDeviceId": intune_id,
        "EntraDeviceId": entra_id,
        "UDID": udid,
        "SerialNumber": serial,
        "IMEI": imei,
        "MEID": "",
        "Manufacturer": manufacturer,
        "Model": model,
        "OperatingSystem": "iOS",
        "OSVersion": os_version,
        "UserDisplayName": "Ada",
        "UserPrincipalName": user_upn,
        "EmailAddress": user_upn,
        "PhoneNumber": phone,
        "OwnerType": owner,
        "ManagementAgent": "mdm",
        "ManagementState": "managed",
        "DeviceEnrollmentType": "deviceEnrollmentProgram",
        "EnrollmentProfileName": "Corporate iOS",
        "EnrolledDateTime": "2026-01-01T00:00:00Z",
        "LastSyncDateTime": last_sync,
        "DaysSinceLastSync": days_since_sync,
        "DeviceActivityStatus": activity,
        "ComplianceState": compliance,
        "AzureADRegistered": "True",
        "IsSupervised": supervised,
        "IsEncrypted": encrypted,
        "JailBroken": jailbroken,
        "EASActivated": "False",
        "EASActivationId": "",
        "EASActivationDateTime": "",
        "SubscriberCarrier": "",
        "CellularTechnology": "",
        "WiFiMacAddress": "",
        "EthernetMacAddress": "",
        "ICCID": iccid,
        "TotalStorageGB": total_storage,
        "FreeStorageGB": free_storage,
        "ActivationLockBypassCode": bypass_code,
        "HasActivationBypassCode": has_bypass_code,
    }
    row.update(extra)
    return row


class IntuneIOSDevicesComparisonTests(unittest.TestCase):
    FAMILY = "Intune_iOS_Devices"
    REPORT_FAMILY = "Intune_iOS_Devices_Report"

    def _write_pair(
        self,
        root: Path,
        baseline_rows,
        latest_rows,
        *,
        family: str | None = None,
    ):
        family = family or self.FAMILY
        template = ios_device_row()
        for path, rows in (
            (root / f"{family}_20260731-042100.csv", baseline_rows),
            (root / f"{family}_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[family]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest, family: str | None = None):
        family = family or baseline.family
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, family),
            family,
        )

    def test_report_family_parses_report_suffix(self):
        path = Path("Intune_iOS_Devices_Report_20260812-170000.csv")
        self.assertEqual(report_family(path), self.REPORT_FAMILY)

    def test_alias_does_not_merge_histories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_iOS_Devices_Report_20260731-042100.csv",
                [ios_device_row()],
            )
            write_report(
                root / "Intune_iOS_Devices_20260804-042100.csv",
                [ios_device_row(device_name="Current exporter")],
            )
            families = scan_report_history(root)
            self.assertIn(self.REPORT_FAMILY, families)
            self.assertIn(self.FAMILY, families)
            self.assertEqual(len(families[self.REPORT_FAMILY]), 1)
            self.assertEqual(len(families[self.FAMILY]), 1)

    def test_entra_device_id_is_preferred_key(self):
        headers = list(IOS_DEVICE_HEADERS)
        self.assertEqual(suggested_key(headers, self.FAMILY), "EntraDeviceId")
        self.assertEqual(suggested_key(headers, self.REPORT_FAMILY), "EntraDeviceId")

    def test_legacy_report_family_uses_same_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(compliance="compliant")],
                [ios_device_row(compliance="noncompliant")],
                family=self.REPORT_FAMILY,
            )
            result = self._compare(baseline, latest, self.REPORT_FAMILY)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "ComplianceState")
            self.assertEqual(detail_identity(changed), "iPhone-15 · SN-IOS-1")

    def test_fallback_to_intune_device_id_when_entra_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(entra_id="", intune_id=IOS_INTUNE_ONE, compliance="compliant")],
                [ios_device_row(entra_id="", intune_id=IOS_INTUNE_ONE, compliance="noncompliant")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_fallback_to_serial_number_when_entra_and_intune_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    ios_device_row(
                        entra_id="",
                        intune_id="",
                        udid="",
                        serial="SN-ONLY-1",
                        compliance="compliant",
                    ),
                ],
                [
                    ios_device_row(
                        entra_id="",
                        intune_id="",
                        udid="",
                        serial="SN-ONLY-1",
                        compliance="noncompliant",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_fallback_to_udid_when_other_ids_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    ios_device_row(
                        device_name="",
                        entra_id="",
                        intune_id="",
                        serial="",
                        udid="UDID-ONLY-1",
                        compliance="compliant",
                    ),
                ],
                [
                    ios_device_row(
                        device_name="",
                        entra_id="",
                        intune_id="",
                        serial="",
                        udid="UDID-ONLY-1",
                        compliance="noncompliant",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(detail_identity(changed), "UDID-ONLY-1")

    def test_device_name_rename_remains_one_changed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(device_name="Old iPhone Name")],
                [ios_device_row(device_name="New iPhone Name")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "DeviceName")
            self.assertEqual(detail_identity(changed), "New iPhone Name · SN-IOS-1")

    def test_friendly_added_and_removed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(device_name="Removed iPhone", serial="SN-REMOVE")],
                [
                    ios_device_row(
                        entra_id=IOS_AAD_TWO,
                        intune_id=IOS_INTUNE_TWO,
                        udid="00000000-0000-0000-0000-000000000002",
                        device_name="Added iPhone",
                        serial="SN-ADD",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed), (1, 1))
            added = next(detail for detail in result.details if detail["change"] == "Added")
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(added), "Added iPhone · SN-ADD")
            self.assertEqual(detail_identity(removed), "Removed iPhone · SN-REMOVE")

    def test_sensitive_ids_are_not_used_as_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    ios_device_row(
                        device_name="",
                        serial="",
                        entra_id="",
                        intune_id=IOS_INTUNE_ONE,
                        udid="",
                        imei="IMEI-ONLY",
                        phone="555-0100",
                        iccid="ICCID-ONLY",
                    ),
                ],
                [
                    ios_device_row(
                        device_name="",
                        serial="",
                        entra_id="",
                        intune_id=IOS_INTUNE_ONE,
                        udid="",
                        imei="IMEI-ONLY",
                        phone="555-0100",
                        iccid="ICCID-ONLY",
                        compliance="noncompliant",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            identity = detail_identity(changed)
            self.assertEqual(identity, IOS_INTUNE_ONE)
            self.assertNotIn("IMEI", identity)
            self.assertNotIn("555-0100", identity)
            self.assertNotIn("ICCID", identity)

    def test_days_since_last_sync_only_drift_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(days_since_sync="5", last_sync="2026-08-01T00:00:00Z")],
                [ios_device_row(days_since_sync="6", last_sync="2026-08-01T00:00:00Z")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_last_sync_date_time_excluded_from_semantic_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(last_sync="2026-08-01T00:00:00Z")],
                [ios_device_row(last_sync="2026-08-04T12:00:00Z")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_free_storage_gb_excluded_from_semantic_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(free_storage="64")],
                [ios_device_row(free_storage="32")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_total_storage_gb_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(total_storage="128")],
                [ios_device_row(total_storage="256")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "TotalStorageGB")
            self.assertEqual(changed["before"], "128")
            self.assertEqual(changed["after"], "256")

    def test_device_activity_status_threshold_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(activity="Active <=30d", days_since_sync="10")],
                [ios_device_row(activity="Stale 31-90d", days_since_sync="45")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "DeviceActivityStatus")
            self.assertEqual(changed["before"], "Active <=30d")
            self.assertEqual(changed["after"], "Stale 31-90d")

    def test_posture_property_changes_remain_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    ios_device_row(
                        compliance="compliant",
                        os_version="17.5",
                        supervised="True",
                        encrypted="True",
                        jailbroken="False",
                    ),
                ],
                [
                    ios_device_row(
                        compliance="noncompliant",
                        os_version="18.0",
                        supervised="False",
                        encrypted="False",
                        jailbroken="True",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertIn("ComplianceState", columns)
            self.assertIn("OSVersion", columns)
            self.assertIn("IsSupervised", columns)
            self.assertIn("IsEncrypted", columns)
            self.assertIn("JailBroken", columns)

    def test_activation_lock_bypass_code_never_appears_in_semantic_details(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(bypass_code="", has_bypass_code="No")],
                [ios_device_row(bypass_code="SECRET-CODE", has_bypass_code="Yes")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details}
            self.assertNotIn("ActivationLockBypassCode", columns)
            for detail in result.details:
                self.assertNotIn("SECRET-CODE", detail.get("before", ""))
                self.assertNotIn("SECRET-CODE", detail.get("after", ""))

    def test_has_activation_bypass_code_remains_comparable(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [ios_device_row(has_bypass_code="No")],
                [ios_device_row(has_bypass_code="Yes", bypass_code="SECRET-CODE")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(
                detail for detail in result.details if detail["column"] == "HasActivationBypassCode"
            )
            self.assertEqual(changed["before"], "No")
            self.assertEqual(changed["after"], "Yes")

    def test_property_frequency_after_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline_rows = [
                ios_device_row(
                    entra_id=IOS_AAD_ONE,
                    compliance="compliant",
                    os_version="17.5",
                    free_storage="64",
                    last_sync="2026-08-01T00:00:00Z",
                    days_since_sync="5",
                ),
                ios_device_row(
                    entra_id=IOS_AAD_TWO,
                    intune_id=IOS_INTUNE_TWO,
                    udid="00000000-0000-0000-0000-000000000002",
                    device_name="iPad-10",
                    serial="SN-IOS-2",
                    compliance="compliant",
                    os_version="17.5",
                    free_storage="48",
                    last_sync="2026-08-01T00:00:00Z",
                    days_since_sync="5",
                ),
            ]
            latest_rows = [
                ios_device_row(
                    entra_id=IOS_AAD_ONE,
                    compliance="noncompliant",
                    os_version="17.5",
                    free_storage="64",
                    last_sync="2026-08-04T12:00:00Z",
                    days_since_sync="8",
                ),
                ios_device_row(
                    entra_id=IOS_AAD_TWO,
                    intune_id=IOS_INTUNE_TWO,
                    udid="00000000-0000-0000-0000-000000000002",
                    device_name="iPad-10",
                    serial="SN-IOS-2",
                    compliance="compliant",
                    os_version="18.0",
                    free_storage="40",
                    last_sync="2026-08-04T12:00:00Z",
                    days_since_sync="8",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), baseline_rows, latest_rows)
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 2)
            histogram: dict[str, int] = {}
            for detail in result.details:
                if detail["change"] != "Changed":
                    continue
                histogram[detail["column"]] = histogram.get(detail["column"], 0) + 1
            self.assertNotIn("LastSyncDateTime", histogram)
            self.assertNotIn("DaysSinceLastSync", histogram)
            self.assertNotIn("FreeStorageGB", histogram)
            self.assertEqual(histogram.get("ComplianceState"), 1)
            self.assertEqual(histogram.get("OSVersion"), 1)


class IntuneIOSDevicesPresentationTests(unittest.TestCase):
    FAMILY = "Intune_iOS_Devices"
    REPORT_FAMILY = "Intune_iOS_Devices_Report"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        self.assertEqual(property_display_text("DeviceName", self.FAMILY), "Device name")
        self.assertEqual(property_display_text("ComplianceState", self.FAMILY), "Compliance state")
        self.assertEqual(property_display_text("OSVersion", self.FAMILY), "OS version")
        self.assertEqual(property_display_text("JailBroken", self.FAMILY), "Jailbroken")
        self.assertEqual(
            property_display_text("HasActivationBypassCode", self.FAMILY),
            "Has activation bypass code",
        )
        self.assertEqual(property_display_text("", self.FAMILY, change="Added"), "Device")
        self.assertEqual(property_display_text("", self.REPORT_FAMILY, change="Removed"), "Device")
        self.assertEqual(property_display_text("FutureField", self.FAMILY), "FutureField")
        tooltip = property_tooltip("WiFiMacAddress", self.FAMILY)
        self.assertIn("Wi-Fi MAC address", tooltip)
        self.assertIn("CSV field: WiFiMacAddress", tooltip)

    def test_identity_tooltip_excludes_sensitive_ids(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "iPhone-15 · SN-IOS-1",
            "device_name": "iPhone-15",
            "serial_number": "SN-IOS-1",
            "entra_device_id": IOS_AAD_ONE,
            "intune_device_id": IOS_INTUNE_ONE,
            "udid": IOS_UDID_ONE,
            "UserPrincipalName": "ada@example.com",
            "IMEI": "secret-imei",
            "PhoneNumber": "555-0100",
            "ICCID": "secret-iccid",
            "ActivationLockBypassCode": "secret-code",
            "key": IOS_AAD_ONE,
        }
        tooltip = identity_tooltip(detail, self.FAMILY)
        self.assertIn("iPhone-15 · SN-IOS-1", tooltip)
        self.assertIn("EntraDeviceId:", tooltip)
        self.assertIn("UDID:", tooltip)
        self.assertIn("UserPrincipalName:", tooltip)
        self.assertNotIn("IMEI", tooltip)
        self.assertNotIn("555-0100", tooltip)
        self.assertNotIn("ICCID", tooltip)
        self.assertNotIn("secret-code", tooltip)

    def test_recent_changes_detail_table_uses_friendly_labels(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": IOS_AAD_ONE,
                    "identity": "iPhone-15 · SN-IOS-1",
                    "column": "ComplianceState",
                    "before": "compliant",
                    "after": "noncompliant",
                    "device_name": "iPhone-15",
                    "serial_number": "SN-IOS-1",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(section.detail_table.item(0, 1).text(), "iPhone-15 · SN-IOS-1")
        self.assertEqual(section.detail_table.item(0, 2).text(), "Compliance state")

    def test_recent_changes_summary_uses_device_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family=self.REPORT_FAMILY,
            status="changed",
            baseline=None,
            latest=None,
            key_column="EntraDeviceId",
            summary=ComparisonSummary(added=2, removed=0, changed=5, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "2 devices added · 0 devices removed · 5 devices changed",
        )

    def test_comparison_summary_unit_uses_devices(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit(self.FAMILY), "devices")
        self.assertEqual(comparison_summary_unit(self.REPORT_FAMILY), "devices")


MANAGED_MD_ONE = "11111111-1111-1111-1111-111111111111"
MANAGED_MD_TWO = "22222222-2222-2222-2222-222222222222"
MANAGED_AAD_ONE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MANAGED_AAD_TWO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
MANAGED_USER_ONE = "user-11111111-1111-1111-1111-111111111111"
MANAGED_USER_TWO = "user-22222222-2222-2222-2222-222222222222"

MANAGED_DEVICE_HEADERS = (
    "UserPrincipalName",
    "UserDisplayName",
    "UserId",
    "DeviceName",
    "ManagedDeviceId",
    "AzureADDeviceId",
    "SerialNumber",
    "Manufacturer",
    "Model",
    "OperatingSystem",
    "OSVersion",
    "ManagementAgent",
    "EnrolledDateTime",
    "LastSyncDateTime",
    "ComplianceState",
    "JailBroken",
    "OwnerType",
    "DaysSinceLastSync",
    "DeviceActivityStatus",
    "EmailAddress",
    "PhoneNumber",
)


def managed_device_row(
    *,
    upn: str = "ada@example.com",
    user_id: str = MANAGED_USER_ONE,
    display_name: str = "Ada Lovelace",
    managed_id: str = MANAGED_MD_ONE,
    azure_id: str = MANAGED_AAD_ONE,
    device_name: str = "Laptop-7",
    serial: str = "SN-MANAGED-1",
    os_version: str = "11",
    compliance: str = "Compliant",
    jailbroken: str = "False",
    owner: str = "company",
    activity: str = "Active<=30d",
    last_sync: str = "2026-08-01T00:00:00Z",
    days_since_sync: str = "5",
    phone: str = "",
    email: str = "ada@example.com",
    **extra,
) -> dict[str, str]:
    row = {
        "UserPrincipalName": upn,
        "UserDisplayName": display_name,
        "UserId": user_id,
        "DeviceName": device_name,
        "ManagedDeviceId": managed_id,
        "AzureADDeviceId": azure_id,
        "SerialNumber": serial,
        "Manufacturer": "Dell",
        "Model": "XPS 13",
        "OperatingSystem": "Windows",
        "OSVersion": os_version,
        "ManagementAgent": "mdm",
        "EnrolledDateTime": "2026-01-01T00:00:00Z",
        "LastSyncDateTime": last_sync,
        "ComplianceState": compliance,
        "JailBroken": jailbroken,
        "OwnerType": owner,
        "DaysSinceLastSync": days_since_sync,
        "DeviceActivityStatus": activity,
        "EmailAddress": email,
        "PhoneNumber": phone,
    }
    row.update(extra)
    return row


class IntuneManagedDevicesComparisonTests(unittest.TestCase):
    FAMILY = "Intune_ManagedDevices_Compliance"

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = managed_device_row()
        for path, rows in (
            (root / f"{self.FAMILY}_20260731-042100.csv", baseline_rows),
            (root / f"{self.FAMILY}_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, self.FAMILY),
            self.FAMILY,
        )

    def test_upn_collision_collapses_sibling_devices(self):
        rows = [
            managed_device_row(managed_id=MANAGED_MD_ONE, device_name="Laptop-A", serial="SN-A"),
            managed_device_row(managed_id=MANAGED_MD_TWO, device_name="Laptop-B", serial="SN-B"),
        ]
        keyed = {}
        for row in rows:
            key = str(row.get("UserPrincipalName", "") or "").strip()
            keyed[key] = row["DeviceName"]
        self.assertEqual(len(keyed), 1)
        self.assertEqual(keyed["ada@example.com"], "Laptop-B")

    def test_multiple_devices_for_same_user_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    managed_device_row(managed_id=MANAGED_MD_ONE, device_name="Laptop-A", serial="SN-A"),
                    managed_device_row(managed_id=MANAGED_MD_TWO, device_name="Laptop-B", serial="SN-B"),
                ],
                [
                    managed_device_row(managed_id=MANAGED_MD_ONE, device_name="Laptop-A", serial="SN-A"),
                    managed_device_row(managed_id=MANAGED_MD_TWO, device_name="Laptop-B", serial="SN-B"),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_managed_device_id_is_preferred_key(self):
        headers = list(MANAGED_DEVICE_HEADERS)
        self.assertEqual(suggested_key(headers, self.FAMILY), "ManagedDeviceId")

    def test_fallback_to_azure_ad_device_id_when_managed_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    managed_device_row(
                        managed_id="",
                        azure_id=MANAGED_AAD_ONE,
                        compliance="Compliant",
                    ),
                ],
                [
                    managed_device_row(
                        managed_id="",
                        azure_id=MANAGED_AAD_ONE,
                        compliance="NonCompliant",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_fallback_to_serial_number_when_managed_and_azure_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    managed_device_row(
                        managed_id="",
                        azure_id="",
                        serial="SN-ONLY-1",
                        compliance="Compliant",
                    ),
                ],
                [
                    managed_device_row(
                        managed_id="",
                        azure_id="",
                        serial="SN-ONLY-1",
                        compliance="NonCompliant",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_device_name_rename_remains_one_changed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [managed_device_row(device_name="Old Laptop Name")],
                [managed_device_row(device_name="New Laptop Name")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "DeviceName")
            self.assertEqual(detail_identity(changed), "New Laptop Name · SN-MANAGED-1")

    def test_user_reassignment_remains_one_changed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [managed_device_row(user_id=MANAGED_USER_ONE, upn="ada@example.com")],
                [
                    managed_device_row(
                        user_id=MANAGED_USER_TWO,
                        upn="bob@example.com",
                        display_name="Bob Builder",
                        email="bob@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertIn("UserId", columns)
            self.assertNotIn("UserPrincipalName", columns)
            self.assertNotIn("UserDisplayName", columns)
            self.assertNotIn("EmailAddress", columns)

    def test_same_user_id_with_upn_rename_does_not_create_device_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    managed_device_row(
                        user_id=MANAGED_USER_ONE,
                        upn="ada@example.com",
                        display_name="Ada Lovelace",
                        email="ada@example.com",
                    ),
                ],
                [
                    managed_device_row(
                        user_id=MANAGED_USER_ONE,
                        upn="ada.lovelace@example.com",
                        display_name="Ada L.",
                        email="ada.lovelace@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_legacy_rows_without_user_id_use_upn_for_association_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    managed_device_row(
                        user_id="",
                        upn="ada@example.com",
                        display_name="Ada Lovelace",
                        email="ada@example.com",
                    ),
                ],
                [
                    managed_device_row(
                        user_id="",
                        upn="bob@example.com",
                        display_name="Bob Builder",
                        email="bob@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertIn("UserPrincipalName", columns)

    def test_friendly_added_and_removed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [managed_device_row(managed_id=MANAGED_MD_ONE, device_name="Removed Laptop", serial="SN-REMOVE")],
                [managed_device_row(managed_id=MANAGED_MD_TWO, device_name="Added Laptop", serial="SN-ADD")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed), (1, 1))
            added = next(detail for detail in result.details if detail["change"] == "Added")
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(added), "Added Laptop · SN-ADD")
            self.assertEqual(detail_identity(removed), "Removed Laptop · SN-REMOVE")

    def test_last_sync_date_time_excluded_from_semantic_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [managed_device_row(last_sync="2026-08-01T00:00:00Z")],
                [managed_device_row(last_sync="2026-08-04T12:00:00Z")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_days_since_last_sync_only_drift_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [managed_device_row(days_since_sync="5", last_sync="2026-08-01T00:00:00Z")],
                [managed_device_row(days_since_sync="6", last_sync="2026-08-01T00:00:00Z")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_device_activity_status_threshold_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [managed_device_row(activity="Active<=30d", days_since_sync="10")],
                [managed_device_row(activity="Stale31-90d", days_since_sync="45")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "DeviceActivityStatus")
            self.assertEqual(changed["before"], "Active<=30d")
            self.assertEqual(changed["after"], "Stale31-90d")

    def test_posture_property_changes_remain_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    managed_device_row(
                        compliance="Compliant",
                        os_version="11",
                        jailbroken="False",
                        owner="company",
                    ),
                ],
                [
                    managed_device_row(
                        compliance="NonCompliant",
                        os_version="11.1",
                        jailbroken="True",
                        owner="personal",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertIn("ComplianceState", columns)
            self.assertIn("OSVersion", columns)
            self.assertIn("JailBroken", columns)
            self.assertIn("OwnerType", columns)

    def test_phone_number_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [managed_device_row(phone="")],
                [managed_device_row(phone="555-0100")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "PhoneNumber")
            self.assertEqual(changed["before"], "")
            self.assertEqual(changed["after"], "555-0100")

    def test_property_frequency_after_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline_rows = [
                managed_device_row(
                    managed_id=MANAGED_MD_ONE,
                    compliance="Compliant",
                    os_version="11",
                    last_sync="2026-08-01T00:00:00Z",
                    days_since_sync="5",
                ),
                managed_device_row(
                    managed_id=MANAGED_MD_TWO,
                    device_name="Laptop-8",
                    serial="SN-MANAGED-2",
                    compliance="Compliant",
                    os_version="11",
                    last_sync="2026-08-01T00:00:00Z",
                    days_since_sync="5",
                ),
            ]
            latest_rows = [
                managed_device_row(
                    managed_id=MANAGED_MD_ONE,
                    compliance="NonCompliant",
                    os_version="11",
                    last_sync="2026-08-04T12:00:00Z",
                    days_since_sync="8",
                ),
                managed_device_row(
                    managed_id=MANAGED_MD_TWO,
                    device_name="Laptop-8",
                    serial="SN-MANAGED-2",
                    compliance="Compliant",
                    os_version="11.1",
                    last_sync="2026-08-04T12:00:00Z",
                    days_since_sync="8",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), baseline_rows, latest_rows)
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 2)
            histogram: dict[str, int] = {}
            for detail in result.details:
                if detail["change"] != "Changed":
                    continue
                histogram[detail["column"]] = histogram.get(detail["column"], 0) + 1
            self.assertNotIn("LastSyncDateTime", histogram)
            self.assertNotIn("DaysSinceLastSync", histogram)
            self.assertEqual(histogram.get("ComplianceState"), 1)
            self.assertEqual(histogram.get("OSVersion"), 1)

    def test_android_last_sync_exclusion_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = android_device_row(last_sync="2026-08-01T00:00:00Z")
            for stamp, last_sync in (
                ("20260731-042100", "2026-08-01T00:00:00Z"),
                ("20260804-042100", "2026-08-04T12:00:00Z"),
            ):
                updated = dict(row)
                updated["LastSyncDateTime"] = last_sync
                write_report(root / f"Intune_Android_Devices_{stamp}.csv", [updated])
            snapshots = scan_report_history(root)["Intune_Android_Devices"]
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                suggested_key(snapshots[0].headers, "Intune_Android_Devices"),
                "Intune_Android_Devices",
            )
            self.assertEqual(result.total_changes, 0)

    def test_ios_free_storage_exclusion_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = ios_device_row()
            for path, rows in (
                (root / "Intune_iOS_Devices_20260731-042100.csv", [ios_device_row(free_storage="64")]),
                (root / "Intune_iOS_Devices_20260804-042100.csv", [ios_device_row(free_storage="32")]),
            ):
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            snapshots = scan_report_history(root)["Intune_iOS_Devices"]
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                suggested_key(snapshots[0].headers, "Intune_iOS_Devices"),
                "Intune_iOS_Devices",
            )
            self.assertEqual(result.total_changes, 0)

    def test_generic_family_key_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")


class IntuneManagedDevicesPresentationTests(unittest.TestCase):
    FAMILY = "Intune_ManagedDevices_Compliance"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        self.assertEqual(property_display_text("DeviceName", self.FAMILY), "Device name")
        self.assertEqual(property_display_text("ComplianceState", self.FAMILY), "Compliance state")
        self.assertEqual(property_display_text("UserId", self.FAMILY), "User ID")
        self.assertEqual(property_display_text("", self.FAMILY, change="Added"), "Device")
        tooltip = property_tooltip("DeviceActivityStatus", self.FAMILY)
        self.assertIn("Activity status", tooltip)
        self.assertIn("CSV field: DeviceActivityStatus", tooltip)

    def test_identity_tooltip_excludes_phone_and_email(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "Laptop-7 · SN-MANAGED-1",
            "device_name": "Laptop-7",
            "serial_number": "SN-MANAGED-1",
            "managed_device_id": MANAGED_MD_ONE,
            "azure_ad_device_id": MANAGED_AAD_ONE,
            "user_display_name": "Ada Lovelace",
            "UserPrincipalName": "ada@example.com",
            "user_id": MANAGED_USER_ONE,
            "PhoneNumber": "555-0100",
            "EmailAddress": "ada@example.com",
            "key": MANAGED_MD_ONE,
        }
        tooltip = identity_tooltip(detail, self.FAMILY)
        self.assertIn("Laptop-7 · SN-MANAGED-1", tooltip)
        self.assertIn("ManagedDeviceId:", tooltip)
        self.assertIn("UserId:", tooltip)
        self.assertNotIn("555-0100", tooltip)
        self.assertNotIn("EmailAddress:", tooltip)

    def test_recent_changes_detail_table_uses_friendly_labels(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": MANAGED_MD_ONE,
                    "identity": "Laptop-7 · SN-MANAGED-1",
                    "column": "ComplianceState",
                    "before": "Compliant",
                    "after": "NonCompliant",
                    "device_name": "Laptop-7",
                    "serial_number": "SN-MANAGED-1",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(section.detail_table.item(0, 1).text(), "Laptop-7 · SN-MANAGED-1")
        self.assertEqual(section.detail_table.item(0, 2).text(), "Compliance state")

    def test_recent_changes_summary_uses_device_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family=self.FAMILY,
            status="changed",
            baseline=None,
            latest=None,
            key_column="ManagedDeviceId",
            summary=ComparisonSummary(added=6, removed=4, changed=20, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "6 devices added · 4 devices removed · 20 devices changed",
        )

    def test_comparison_summary_unit_uses_devices(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit(self.FAMILY), "devices")


MAILBOX_EXT_ONE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MAILBOX_EXT_TWO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

SHARED_MAILBOX_HEADERS = (
    "DisplayName",
    "PrimarySmtpAddress",
    "Alias",
    "ExternalDirectoryObjectId",
    "RecipientTypeDetails",
    "HiddenFromAddressListsEnabled",
    "WhenCreated",
    "HasFullAccessDelegates",
    "FullAccessDelegates",
    "FullAccessDelegatesCount",
    "HasSendAsDelegates",
    "SendAsDelegates",
    "SendAsDelegatesCount",
    "HasSendOnBehalfDelegates",
    "SendOnBehalfDelegates",
    "SendOnBehalfDelegatesCount",
    "HasAnyDelegation",
    "ForwardingAddress",
    "ForwardingSmtpAddress",
    "DeliverToMailboxAndForward",
    "HasForwarding",
    "LitigationHoldEnabled",
    "RetentionPolicy",
)


def shared_mailbox_row(
    *,
    external_id: str = MAILBOX_EXT_ONE,
    display_name: str = "Finance Shared",
    primary_smtp: str = "finance@example.com",
    alias: str = "finance",
    full_access: str = "ada@example.com",
    send_as: str = "ada@example.com",
    send_on_behalf: str = "",
    forwarding_smtp: str = "",
    deliver_and_forward: str = "False",
    litigation_hold: str = "False",
    retention_policy: str = "",
    hidden: str = "False",
    **extra,
) -> dict[str, str]:
    has_full_access = bool(full_access) and not full_access.upper().startswith("ERROR:")
    has_send_as = bool(send_as) and not send_as.upper().startswith("ERROR:")
    has_send_on_behalf = bool(send_on_behalf)
    full_access_count = 0 if full_access.upper().startswith("ERROR:") else len(
        [part for part in full_access.split(";") if part.strip()]
    )
    send_as_count = 0 if send_as.upper().startswith("ERROR:") else len(
        [part for part in send_as.split(";") if part.strip()]
    )
    send_on_behalf_count = len([part for part in send_on_behalf.split(";") if part.strip()])
    has_forwarding = bool(str(extra.get("ForwardingAddress", "") or forwarding_smtp).strip())
    row = {
        "DisplayName": display_name,
        "PrimarySmtpAddress": primary_smtp,
        "Alias": alias,
        "ExternalDirectoryObjectId": external_id,
        "RecipientTypeDetails": "SharedMailbox",
        "HiddenFromAddressListsEnabled": hidden,
        "WhenCreated": "2026-01-01T00:00:00Z",
        "HasFullAccessDelegates": str(has_full_access),
        "FullAccessDelegates": full_access,
        "FullAccessDelegatesCount": str(full_access_count),
        "HasSendAsDelegates": str(has_send_as),
        "SendAsDelegates": send_as,
        "SendAsDelegatesCount": str(send_as_count),
        "HasSendOnBehalfDelegates": str(has_send_on_behalf),
        "SendOnBehalfDelegates": send_on_behalf,
        "SendOnBehalfDelegatesCount": str(send_on_behalf_count),
        "HasAnyDelegation": str(has_full_access or has_send_as or has_send_on_behalf),
        "ForwardingAddress": "",
        "ForwardingSmtpAddress": forwarding_smtp,
        "DeliverToMailboxAndForward": deliver_and_forward,
        "HasForwarding": str(has_forwarding),
        "LitigationHoldEnabled": litigation_hold,
        "RetentionPolicy": retention_policy,
    }
    row.update(extra)
    return row


class ExchangeSharedMailboxesComparisonTests(unittest.TestCase):
    FAMILY = "Exchange_SharedMailboxes"

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = shared_mailbox_row()
        for path, rows in (
            (root / f"{self.FAMILY}_20260731-042100.csv", baseline_rows),
            (root / f"{self.FAMILY}_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, self.FAMILY),
            self.FAMILY,
        )

    def test_external_directory_object_id_is_preferred_key(self):
        headers = list(SHARED_MAILBOX_HEADERS)
        self.assertEqual(suggested_key(headers, self.FAMILY), "ExternalDirectoryObjectId")

    def test_primary_smtp_address_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    shared_mailbox_row(
                        external_id="",
                        primary_smtp="finance@example.com",
                        litigation_hold="False",
                    ),
                ],
                [
                    shared_mailbox_row(
                        external_id="",
                        primary_smtp="finance@example.com",
                        litigation_hold="True",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_smtp_rename_remains_one_changed_mailbox(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    shared_mailbox_row(
                        display_name="Finance Shared",
                        primary_smtp="old-finance@example.com",
                    ),
                ],
                [
                    shared_mailbox_row(
                        display_name="Finance Shared",
                        primary_smtp="finance@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "PrimarySmtpAddress")
            self.assertEqual(detail_identity(changed), "Finance Shared · finance@example.com")

    def test_friendly_added_and_removed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    shared_mailbox_row(
                        external_id=MAILBOX_EXT_ONE,
                        display_name="Removed Mailbox",
                        primary_smtp="removed@example.com",
                    ),
                ],
                [
                    shared_mailbox_row(
                        external_id=MAILBOX_EXT_TWO,
                        display_name="Added Mailbox",
                        primary_smtp="added@example.com",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed), (1, 1))
            added = next(detail for detail in result.details if detail["change"] == "Added")
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(added), "Added Mailbox · added@example.com")
            self.assertEqual(detail_identity(removed), "Removed Mailbox · removed@example.com")

    def test_full_access_reorder_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="a@example.com; b@example.com")],
                [shared_mailbox_row(full_access="b@example.com; a@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_full_access_membership_change_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="a@example.com; b@example.com")],
                [shared_mailbox_row(full_access="a@example.com; c@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "FullAccessDelegates")
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"FullAccessDelegates"})

    def test_send_as_reorder_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(send_as="a@example.com; b@example.com")],
                [shared_mailbox_row(send_as="b@example.com; a@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_send_as_membership_change_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(send_as="a@example.com; b@example.com")],
                [shared_mailbox_row(send_as="a@example.com; c@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"SendAsDelegates"})

    def test_send_on_behalf_reorder_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(send_on_behalf="a@example.com; b@example.com")],
                [shared_mailbox_row(send_on_behalf="b@example.com; a@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_send_on_behalf_membership_change_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(send_on_behalf="a@example.com; b@example.com")],
                [shared_mailbox_row(send_on_behalf="a@example.com; c@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"SendOnBehalfDelegates"})

    def test_delegate_count_only_difference_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="a@example.com; b@example.com", send_as="a@example.com")],
                [
                    shared_mailbox_row(
                        full_access="a@example.com; b@example.com",
                        send_as="a@example.com",
                        FullAccessDelegatesCount="99",
                        SendAsDelegatesCount="99",
                        HasAnyDelegation="True",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_has_forwarding_difference_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(forwarding_smtp="external@example.net")],
                [
                    shared_mailbox_row(
                        forwarding_smtp="external@example.net",
                        HasForwarding="False",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_full_access_success_to_error_does_not_create_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="a@example.com; b@example.com")],
                [shared_mailbox_row(full_access="ERROR: transient failure")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertNotIn("FullAccessDelegates", columns)

    def test_full_access_error_to_success_does_not_create_addition(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="ERROR: transient failure")],
                [shared_mailbox_row(full_access="a@example.com; b@example.com")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertNotIn("FullAccessDelegates", columns)

    def test_full_access_error_to_error_has_no_semantic_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="ERROR: first failure")],
                [shared_mailbox_row(full_access="ERROR: second failure")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_send_as_success_to_error_does_not_create_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(send_as="a@example.com")],
                [shared_mailbox_row(send_as="ERROR: transient failure")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertNotIn("SendAsDelegates", columns)

    def test_send_as_error_to_success_does_not_create_addition(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(send_as="ERROR: transient failure")],
                [shared_mailbox_row(send_as="a@example.com")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertNotIn("SendAsDelegates", columns)

    def test_empty_to_populated_delegates_is_real_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="")],
                [shared_mailbox_row(full_access="a@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "FullAccessDelegates")
            self.assertEqual(changed["before"], "")
            self.assertEqual(changed["after"], "a@example.com")

    def test_populated_to_empty_delegates_is_real_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(full_access="a@example.com")],
                [shared_mailbox_row(full_access="")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_forwarding_smtp_address_change_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(forwarding_smtp="")],
                [shared_mailbox_row(forwarding_smtp="external@example.net")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "ForwardingSmtpAddress")
            self.assertEqual(changed["after"], "external@example.net")

    def test_deliver_to_mailbox_and_forward_change_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(deliver_and_forward="False")],
                [shared_mailbox_row(deliver_and_forward="True")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertIn("DeliverToMailboxAndForward", columns)

    def test_litigation_hold_and_retention_changes_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(litigation_hold="False", retention_policy="")],
                [shared_mailbox_row(litigation_hold="True", retention_policy="Default MRM Policy")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertIn("LitigationHoldEnabled", columns)
            self.assertIn("RetentionPolicy", columns)

    def test_hidden_from_address_lists_change_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [shared_mailbox_row(hidden="False")],
                [shared_mailbox_row(hidden="True")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(
                detail for detail in result.details if detail["column"] == "HiddenFromAddressListsEnabled"
            )
            self.assertEqual(changed["before"], "False")
            self.assertEqual(changed["after"], "True")

    def test_generic_family_key_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")


class ExchangeSharedMailboxesPresentationTests(unittest.TestCase):
    FAMILY = "Exchange_SharedMailboxes"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        self.assertEqual(property_display_text("FullAccessDelegates", self.FAMILY), "Full Access delegates")
        self.assertEqual(property_display_text("ForwardingSmtpAddress", self.FAMILY), "Forwarding SMTP address")
        self.assertEqual(property_display_text("", self.FAMILY, change="Added"), "Shared mailbox")
        tooltip = property_tooltip("RetentionPolicy", self.FAMILY)
        self.assertIn("Retention policy", tooltip)
        self.assertIn("CSV field: RetentionPolicy", tooltip)

    def test_identity_tooltip_includes_mailbox_fields(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "Finance Shared · finance@example.com",
            "display_name": "Finance Shared",
            "primary_smtp": "finance@example.com",
            "alias": "finance",
            "external_directory_object_id": MAILBOX_EXT_ONE,
            "key": MAILBOX_EXT_ONE,
        }
        tooltip = identity_tooltip(detail, self.FAMILY)
        self.assertIn("Finance Shared · finance@example.com", tooltip)
        self.assertIn("PrimarySmtpAddress:", tooltip)
        self.assertIn("ExternalDirectoryObjectId:", tooltip)

    def test_recent_changes_detail_table_uses_friendly_labels(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": MAILBOX_EXT_ONE,
                    "identity": "Finance Shared · finance@example.com",
                    "column": "FullAccessDelegates",
                    "before": "a@example.com",
                    "after": "a@example.com; b@example.com",
                    "display_name": "Finance Shared",
                    "primary_smtp": "finance@example.com",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(section.detail_table.item(0, 1).text(), "Finance Shared · finance@example.com")
        self.assertEqual(section.detail_table.item(0, 2).text(), "Full Access delegates")

    def test_recent_changes_summary_uses_shared_mailbox_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family=self.FAMILY,
            status="changed",
            baseline=None,
            latest=None,
            key_column="ExternalDirectoryObjectId",
            summary=ComparisonSummary(added=1, removed=2, changed=3, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "1 shared mailbox added · 2 shared mailboxes removed · 3 shared mailboxes changed",
        )

    def test_comparison_summary_unit_uses_shared_mailboxes(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit(self.FAMILY), "shared mailboxes")

    def test_delegate_delta_summary(self):
        from diffasaurus.core.report_history import delegate_collection_delta_summary

        summary = delegate_collection_delta_summary(
            "a@example.com; b@example.com",
            "a@example.com; c@example.com",
        )
        self.assertIn("Removed delegates:", summary)
        self.assertIn("b@example.com", summary)
        self.assertIn("Added delegates:", summary)
        self.assertIn("c@example.com", summary)


AUTOPILOT_OBJECT_ONE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AUTOPILOT_OBJECT_TWO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
AUTOPILOT_AAD_ONE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
AUTOPILOT_AAD_TWO = "dddddddd-dddd-dddd-dddd-dddddddddddd"
AUTOPILOT_MANAGED_ONE = "11111111-1111-1111-1111-111111111111"

AUTOPILOT_HEADERS = (
    "DisplayName",
    "SerialNumber",
    "Manufacturer",
    "Model",
    "GroupTag",
    "PurchaseOrderIdentifier",
    "EnrollmentState",
    "LastContactedDateTime",
    "UserPrincipalName",
    "AddressableUserName",
    "ResourceName",
    "SkuNumber",
    "SystemFamily",
    "AzureADDeviceId",
    "ManagedDeviceId",
    "AutopilotObjectId",
    "AssignedUser",
    "AssignmentStatus",
    "RecommendedAction",
)


def autopilot_device_row(
    *,
    autopilot_id: str = AUTOPILOT_OBJECT_ONE,
    serial: str = "SN-AUTO-1",
    display_name: str = "Surface-Laptop",
    manufacturer: str = "Microsoft",
    model: str = "Surface Laptop 5",
    group_tag: str = "Finance",
    enrollment_state: str = "notContacted",
    last_contacted: str = "2026-08-01T00:00:00Z",
    user_principal_name: str = "",
    addressable_user_name: str = "",
    resource_name: str = "OEM-RESOURCE-1",
    azure_ad_device_id: str = "",
    managed_device_id: str = "",
    **extra,
) -> dict[str, str]:
    if user_principal_name:
        assigned_user = user_principal_name
    elif addressable_user_name:
        assigned_user = addressable_user_name
    else:
        assigned_user = "Unassigned"
    assignment_status = "Assigned" if assigned_user != "Unassigned" else "NotAssigned"
    if assignment_status == "Assigned":
        recommended_action = "Review" if enrollment_state == "enrolled" else "ReadyToUnassign"
    else:
        recommended_action = "ReadyToAssign"
    row = {
        "DisplayName": display_name,
        "SerialNumber": serial,
        "Manufacturer": manufacturer,
        "Model": model,
        "GroupTag": group_tag,
        "PurchaseOrderIdentifier": "PO-1001",
        "EnrollmentState": enrollment_state,
        "LastContactedDateTime": last_contacted,
        "UserPrincipalName": user_principal_name,
        "AddressableUserName": addressable_user_name,
        "ResourceName": resource_name,
        "SkuNumber": "SKU-1",
        "SystemFamily": "Windows",
        "AzureADDeviceId": azure_ad_device_id,
        "ManagedDeviceId": managed_device_id,
        "AutopilotObjectId": autopilot_id,
        "AssignedUser": assigned_user,
        "AssignmentStatus": assignment_status,
        "RecommendedAction": recommended_action,
    }
    row.update(extra)
    return row


class IntuneAutopilotDevicesComparisonTests(unittest.TestCase):
    FAMILY = "Intune_Devices_Autopilot"

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = autopilot_device_row()
        for path, rows in (
            (root / f"{self.FAMILY}_20260731-042100.csv", baseline_rows),
            (root / f"{self.FAMILY}_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, self.FAMILY),
            self.FAMILY,
        )

    def test_autopilot_object_id_preferred_over_azure_ad_device_id(self):
        headers = list(AUTOPILOT_HEADERS)
        self.assertEqual(suggested_key(headers, self.FAMILY), "AutopilotObjectId")
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    autopilot_device_row(
                        autopilot_id=AUTOPILOT_OBJECT_ONE,
                        azure_ad_device_id=AUTOPILOT_AAD_ONE,
                    ),
                ],
                [
                    autopilot_device_row(
                        autopilot_id=AUTOPILOT_OBJECT_ONE,
                        azure_ad_device_id=AUTOPILOT_AAD_TWO,
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "AzureADDeviceId")

    def test_serial_number_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    autopilot_device_row(
                        autopilot_id="",
                        serial="SN-LEGACY-1",
                        group_tag="Finance",
                    ),
                ],
                [
                    autopilot_device_row(
                        autopilot_id="",
                        serial="SN-LEGACY-1",
                        group_tag="Engineering",
                    ),
                ],
            )
            self.assertEqual(suggested_key(baseline.headers, self.FAMILY), "AutopilotObjectId")
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)

    def test_blank_azure_ad_device_id_does_not_collapse_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    autopilot_device_row(
                        autopilot_id=AUTOPILOT_OBJECT_ONE,
                        azure_ad_device_id="",
                        group_tag="Finance",
                    ),
                ],
                [
                    autopilot_device_row(
                        autopilot_id=AUTOPILOT_OBJECT_ONE,
                        azure_ad_device_id="",
                        group_tag="Engineering",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(detail_identity(changed), "Surface-Laptop · SN-AUTO-1")

    def test_display_name_rename_remains_one_changed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(display_name="Old Surface Name")],
                [autopilot_device_row(display_name="New Surface Name")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "DisplayName")
            self.assertEqual(detail_identity(changed), "New Surface Name · SN-AUTO-1")

    def test_user_principal_name_assignment_remains_one_changed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(user_principal_name="")],
                [autopilot_device_row(user_principal_name="ada@example.com")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"UserPrincipalName"})
            self.assertNotIn("AssignedUser", columns)
            self.assertNotIn("AssignmentStatus", columns)
            self.assertNotIn("RecommendedAction", columns)

    def test_addressable_user_name_assignment_remains_one_changed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(addressable_user_name="")],
                [autopilot_device_row(addressable_user_name="corp\\ada")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"AddressableUserName"})

    def test_friendly_added_and_removed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(autopilot_id=AUTOPILOT_OBJECT_ONE, display_name="Removed Surface", serial="SN-REMOVE")],
                [autopilot_device_row(autopilot_id=AUTOPILOT_OBJECT_TWO, display_name="Added Surface", serial="SN-ADD")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed), (1, 1))
            added = next(detail for detail in result.details if detail["change"] == "Added")
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(detail_identity(added), "Added Surface · SN-ADD")
            self.assertEqual(detail_identity(removed), "Removed Surface · SN-REMOVE")

    def test_last_contacted_date_time_only_movement_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(last_contacted="2026-08-01T00:00:00Z")],
                [autopilot_device_row(last_contacted="2026-08-04T12:00:00Z")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_last_contacted_plus_group_tag_reports_group_tag_only(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(last_contacted="2026-08-01T00:00:00Z", group_tag="Finance")],
                [autopilot_device_row(last_contacted="2026-08-04T12:00:00Z", group_tag="Engineering")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"GroupTag"})

    def test_enrollment_state_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(enrollment_state="notContacted")],
                [autopilot_device_row(enrollment_state="enrolled")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "EnrollmentState")
            self.assertEqual(changed["before"], "notContacted")
            self.assertEqual(changed["after"], "enrolled")

    def test_resource_name_change_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(resource_name="OEM-RESOURCE-1")],
                [autopilot_device_row(resource_name="OEM-RESOURCE-2")],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "ResourceName")
            self.assertEqual(changed["before"], "OEM-RESOURCE-1")
            self.assertEqual(changed["after"], "OEM-RESOURCE-2")

    def test_assignment_derived_fields_do_not_duplicate_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(user_principal_name="", enrollment_state="notContacted")],
                [autopilot_device_row(user_principal_name="ada@example.com", enrollment_state="notContacted")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"UserPrincipalName"})

    def test_recommended_action_excluded_when_enrollment_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [autopilot_device_row(user_principal_name="ada@example.com", enrollment_state="notContacted")],
                [autopilot_device_row(user_principal_name="ada@example.com", enrollment_state="enrolled")],
            )
            result = self._compare(baseline, latest)
            columns = {detail["column"] for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, {"EnrollmentState"})
            self.assertNotIn("RecommendedAction", columns)

    def test_collection_time_last_contacted_does_not_flood_changed_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline_rows = []
            latest_rows = []
            for index in range(45):
                autopilot_id = f"{index:08x}-0000-0000-0000-000000000001"
                baseline_rows.append(
                    autopilot_device_row(
                        autopilot_id=autopilot_id,
                        serial=f"SN-{index}",
                        display_name=f"Device-{index}",
                        last_contacted="2026-08-01T00:00:00Z",
                    )
                )
                latest_rows.append(
                    autopilot_device_row(
                        autopilot_id=autopilot_id,
                        serial=f"SN-{index}",
                        display_name=f"Device-{index}",
                        last_contacted=f"2026-08-0{index % 4 + 1}T12:00:00Z",
                    )
                )
            baseline, latest = self._write_pair(Path(directory), baseline_rows, latest_rows)
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 0))

    def test_android_last_sync_exclusion_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = android_device_row(last_sync="2026-08-01T00:00:00Z")
            for stamp, last_sync in (
                ("20260731-042100", "2026-08-01T00:00:00Z"),
                ("20260804-042100", "2026-08-04T12:00:00Z"),
            ):
                updated = dict(row)
                updated["LastSyncDateTime"] = last_sync
                write_report(root / f"Intune_Android_Devices_{stamp}.csv", [updated])
            snapshots = scan_report_history(root)["Intune_Android_Devices"]
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                suggested_key(snapshots[0].headers, "Intune_Android_Devices"),
                "Intune_Android_Devices",
            )
            self.assertEqual(result.total_changes, 0)

    def test_ios_last_sync_excluded_from_semantic_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = android_device_row(last_sync="2026-08-01T00:00:00Z")
            for stamp, last_sync in (
                ("20260731-042100", "2026-08-01T00:00:00Z"),
                ("20260804-042100", "2026-08-04T12:00:00Z"),
            ):
                updated = dict(row)
                updated["LastSyncDateTime"] = last_sync
                write_report(root / f"Intune_iOS_Devices_{stamp}.csv", [updated])
            snapshots = scan_report_history(root)["Intune_iOS_Devices"]
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                suggested_key(snapshots[0].headers, "Intune_iOS_Devices"),
                "Intune_iOS_Devices",
            )
            self.assertEqual(result.total_changes, 0)

    def test_generic_family_key_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")


class IntuneAutopilotDevicesPresentationTests(unittest.TestCase):
    FAMILY = "Intune_Devices_Autopilot"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        self.assertEqual(property_display_text("GroupTag", self.FAMILY), "Group tag")
        self.assertEqual(property_display_text("EnrollmentState", self.FAMILY), "Enrollment state")
        self.assertEqual(property_display_text("UserPrincipalName", self.FAMILY), "User principal name")
        self.assertEqual(property_display_text("", self.FAMILY, change="Added"), "Device")
        tooltip = property_tooltip("RecommendedAction", self.FAMILY)
        self.assertIn("Recommended action", tooltip)
        self.assertIn("CSV field: RecommendedAction", tooltip)

    def test_identity_tooltip_includes_autopilot_fields(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        detail = {
            "identity": "Surface-Laptop · SN-AUTO-1",
            "display_name": "Surface-Laptop",
            "serial_number": "SN-AUTO-1",
            "autopilot_object_id": AUTOPILOT_OBJECT_ONE,
            "azure_ad_device_id": AUTOPILOT_AAD_ONE,
            "managed_device_id": AUTOPILOT_MANAGED_ONE,
            "UserPrincipalName": "ada@example.com",
            "key": AUTOPILOT_OBJECT_ONE,
        }
        tooltip = identity_tooltip(detail, self.FAMILY)
        self.assertIn("Surface-Laptop · SN-AUTO-1", tooltip)
        self.assertIn("AutopilotObjectId:", tooltip)
        self.assertIn("AzureADDeviceId:", tooltip)
        self.assertIn("ManagedDeviceId:", tooltip)
        self.assertIn("UserPrincipalName:", tooltip)

    def test_recent_changes_detail_table_uses_friendly_labels(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": AUTOPILOT_OBJECT_ONE,
                    "identity": "Surface-Laptop · SN-AUTO-1",
                    "column": "GroupTag",
                    "before": "Finance",
                    "after": "Engineering",
                    "display_name": "Surface-Laptop",
                    "serial_number": "SN-AUTO-1",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(section.detail_table.item(0, 1).text(), "Surface-Laptop · SN-AUTO-1")
        self.assertEqual(section.detail_table.item(0, 2).text(), "Group tag")

    def test_recent_changes_summary_uses_device_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family=self.FAMILY,
            status="changed",
            baseline=None,
            latest=None,
            key_column="AutopilotObjectId",
            summary=ComparisonSummary(added=1, removed=1, changed=3, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "1 device added · 1 device removed · 3 devices changed",
        )

    def test_comparison_summary_unit_uses_devices(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit(self.FAMILY), "devices")


ROLE_USER_ONE = "11111111-1111-1111-1111-111111111111"
ROLE_USER_TWO = "22222222-2222-2222-2222-222222222222"
ROLE_DEF_GLOBAL_READER = "33333333-3333-3333-3333-333333333333"
ROLE_DEF_SECURITY_READER = "44444444-4444-4444-4444-444444444444"
SCHEDULE_ACTIVE_DIRECT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SCHEDULE_ELIGIBLE_DIRECT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SCHEDULE_ACTIVE_GROUP = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SCHEDULE_ELIGIBLE_GROUP = "dddddddd-dddd-dddd-dddd-dddddddddddd"
GROUP_ONE = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
GROUP_TWO = "ffffffff-ffff-ffff-ffff-ffffffffffff"
SCOPE_TENANT = "/"
SCOPE_APP = "app-scope-1"

LEGACY_ROLE_HEADERS = (
    "UserPrincipalName",
    "DisplayName",
    "Mail",
    "AccountEnabled",
    "RoleName",
    "RoleState",
    "AssignmentSource",
    "SourceGroup",
)

STABLE_ROLE_HEADERS = LEGACY_ROLE_HEADERS + (
    "UserId",
    "RoleDefinitionId",
    "AssignmentScheduleId",
    "SourcePrincipalId",
    "SourceGroupId",
    "DirectoryScopeId",
    "AppScopeId",
)


def legacy_role_assignment_row(
    *,
    upn: str = "ada@example.com",
    display_name: str = "Ada Lovelace",
    mail: str = "ada@example.com",
    account_enabled: str = "True",
    role_name: str = "Global Reader",
    role_state: str = "Active",
    assignment_source: str = "Direct",
    source_group: str = "",
) -> dict[str, str]:
    return {
        "UserPrincipalName": upn,
        "DisplayName": display_name,
        "Mail": mail,
        "AccountEnabled": account_enabled,
        "RoleName": role_name,
        "RoleState": role_state,
        "AssignmentSource": assignment_source,
        "SourceGroup": source_group,
    }


def stable_role_assignment_row(
    *,
    upn: str = "ada@example.com",
    display_name: str = "Ada Lovelace",
    mail: str = "ada@example.com",
    account_enabled: str = "True",
    role_name: str = "Global Reader",
    role_state: str = "Active",
    assignment_source: str = "Direct",
    source_group: str = "",
    user_id: str = ROLE_USER_ONE,
    role_definition_id: str = ROLE_DEF_GLOBAL_READER,
    assignment_schedule_id: str = SCHEDULE_ACTIVE_DIRECT,
    source_principal_id: str = ROLE_USER_ONE,
    source_group_id: str = "",
    directory_scope_id: str = SCOPE_TENANT,
    app_scope_id: str = "",
) -> dict[str, str]:
    row = legacy_role_assignment_row(
        upn=upn,
        display_name=display_name,
        mail=mail,
        account_enabled=account_enabled,
        role_name=role_name,
        role_state=role_state,
        assignment_source=assignment_source,
        source_group=source_group,
    )
    if assignment_source == "Group" and source_group_id:
        source_principal_id = source_group_id
    row.update(
        {
            "UserId": user_id,
            "RoleDefinitionId": role_definition_id,
            "AssignmentScheduleId": assignment_schedule_id,
            "SourcePrincipalId": source_principal_id,
            "SourceGroupId": source_group_id,
            "DirectoryScopeId": directory_scope_id,
            "AppScopeId": app_scope_id,
        }
    )
    return row


class EntraRoleAssignmentsComparisonTests(unittest.TestCase):
    FAMILY = "Entra_Role_Assignments"

    def _write_pair(
        self,
        root: Path,
        baseline_rows,
        latest_rows,
        *,
        legacy: bool = True,
    ):
        headers = list(LEGACY_ROLE_HEADERS if legacy else STABLE_ROLE_HEADERS)
        for path, rows in (
            (root / f"{self.FAMILY}_20260731-042100.csv", baseline_rows),
            (root / f"{self.FAMILY}_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            suggested_key(baseline.headers, self.FAMILY),
            self.FAMILY,
        )

    def test_upn_only_collision_overwrites_sibling_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                legacy_role_assignment_row(
                    role_name="Security Reader",
                    role_state="Eligible",
                    assignment_source="Group",
                    source_group="PIM-Readers",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, rows)
            broken_map = {}
            for row in rows:
                key = row["UserPrincipalName"]
                broken_map[key] = row
            self.assertEqual(len(broken_map), 1)
            fixed = self._compare(baseline, latest)
            self.assertEqual(fixed.stable, 2)

    def test_legacy_multiple_roles_for_one_user_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                    legacy_role_assignment_row(
                        role_name="Security Reader",
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                    ),
                ],
                [
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                    legacy_role_assignment_row(
                        role_name="Security Reader",
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                    ),
                ],
            )
            self.assertEqual(
                suggested_key(baseline.headers, self.FAMILY),
                "Role assignment (legacy)",
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_legacy_active_and_eligible_rows_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Eligible"),
                ],
                [
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Eligible"),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_legacy_direct_and_group_assignments_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                    legacy_role_assignment_row(
                        role_name="Global Reader",
                        role_state="Active",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                    ),
                ],
                [
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                    legacy_role_assignment_row(
                        role_name="Global Reader",
                        role_state="Active",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_legacy_same_role_via_two_groups_remains_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    legacy_role_assignment_row(
                        role_name="Security Reader",
                        assignment_source="Group",
                        source_group="Group A",
                    ),
                    legacy_role_assignment_row(
                        role_name="Security Reader",
                        assignment_source="Group",
                        source_group="Group B",
                    ),
                ],
                [
                    legacy_role_assignment_row(
                        role_name="Security Reader",
                        assignment_source="Group",
                        source_group="Group A",
                    ),
                    legacy_role_assignment_row(
                        role_name="Security Reader",
                        assignment_source="Group",
                        source_group="Group B",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_stable_schedule_and_user_key_keeps_rows_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    stable_role_assignment_row(
                        assignment_schedule_id=SCHEDULE_ACTIVE_DIRECT,
                        user_id=ROLE_USER_ONE,
                    ),
                    stable_role_assignment_row(
                        role_name="Security Reader",
                        role_definition_id=ROLE_DEF_SECURITY_READER,
                        assignment_schedule_id=SCHEDULE_ELIGIBLE_GROUP,
                        user_id=ROLE_USER_ONE,
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                    ),
                ],
                [
                    stable_role_assignment_row(
                        assignment_schedule_id=SCHEDULE_ACTIVE_DIRECT,
                        user_id=ROLE_USER_ONE,
                    ),
                    stable_role_assignment_row(
                        role_name="Security Reader",
                        role_definition_id=ROLE_DEF_SECURITY_READER,
                        assignment_schedule_id=SCHEDULE_ELIGIBLE_GROUP,
                        user_id=ROLE_USER_ONE,
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                    ),
                ],
                legacy=False,
            )
            self.assertEqual(
                suggested_key(baseline.headers, self.FAMILY),
                "Assignment schedule + User",
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_stable_user_id_distinguishes_group_members(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    stable_role_assignment_row(
                        upn="ada@example.com",
                        display_name="Ada",
                        user_id=ROLE_USER_ONE,
                        assignment_schedule_id=SCHEDULE_ACTIVE_GROUP,
                        role_state="Active",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                    ),
                    stable_role_assignment_row(
                        upn="grace@example.com",
                        display_name="Grace",
                        user_id=ROLE_USER_TWO,
                        assignment_schedule_id=SCHEDULE_ACTIVE_GROUP,
                        role_state="Active",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                    ),
                ],
                [
                    stable_role_assignment_row(
                        upn="ada@example.com",
                        display_name="Ada",
                        user_id=ROLE_USER_ONE,
                        assignment_schedule_id=SCHEDULE_ACTIVE_GROUP,
                        role_state="Active",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                    ),
                    stable_role_assignment_row(
                        upn="grace@example.com",
                        display_name="Grace",
                        user_id=ROLE_USER_TWO,
                        assignment_schedule_id=SCHEDULE_ACTIVE_GROUP,
                        role_state="Active",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                    ),
                ],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_stable_upn_rename_does_not_change_assignment_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [stable_role_assignment_row(upn="ada.old@example.com")],
                [stable_role_assignment_row(upn="ada.new@example.com")],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_stable_display_name_rename_does_not_create_assignment_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [stable_role_assignment_row(display_name="Ada")],
                [stable_role_assignment_row(display_name="Ada Lovelace")],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_stable_account_enabled_change_does_not_flood_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [stable_role_assignment_row(account_enabled="True")],
                [stable_role_assignment_row(account_enabled="False")],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_stable_role_name_rename_does_not_create_assignment_change(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [stable_role_assignment_row(role_name="Global Reader")],
                [stable_role_assignment_row(role_name="Global Reader Renamed")],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_stable_source_group_rename_with_same_group_id_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    stable_role_assignment_row(
                        role_name="Security Reader",
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="Old Group Name",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                        assignment_schedule_id=SCHEDULE_ELIGIBLE_GROUP,
                        role_definition_id=ROLE_DEF_SECURITY_READER,
                    ),
                ],
                [
                    stable_role_assignment_row(
                        role_name="Security Reader",
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="New Group Name",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                        assignment_schedule_id=SCHEDULE_ELIGIBLE_GROUP,
                        role_definition_id=ROLE_DEF_SECURITY_READER,
                    ),
                ],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.total_changes, 0)

    def test_stable_different_source_group_id_is_different_assignment_path(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    stable_role_assignment_row(
                        role_name="Security Reader",
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="Group A",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                        assignment_schedule_id=SCHEDULE_ELIGIBLE_GROUP,
                        role_definition_id=ROLE_DEF_SECURITY_READER,
                    ),
                ],
                [
                    stable_role_assignment_row(
                        role_name="Security Reader",
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="Group B",
                        source_group_id=GROUP_TWO,
                        source_principal_id=GROUP_TWO,
                        assignment_schedule_id=SCHEDULE_ACTIVE_GROUP,
                        role_definition_id=ROLE_DEF_SECURITY_READER,
                    ),
                ],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed), (1, 1))
            self.assertEqual(result.changed, 0)

    def test_stable_directory_scope_difference_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [stable_role_assignment_row(directory_scope_id=SCOPE_TENANT)],
                [stable_role_assignment_row(directory_scope_id="/different-scope")],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "DirectoryScopeId")
            self.assertEqual(changed["before"], SCOPE_TENANT)
            self.assertEqual(changed["after"], "/different-scope")

    def test_stable_app_scope_difference_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [stable_role_assignment_row(app_scope_id="")],
                [stable_role_assignment_row(app_scope_id=SCOPE_APP)],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["column"] == "AppScopeId")
            self.assertEqual(changed["after"], SCOPE_APP)

    def test_active_to_eligible_becomes_removed_and_added_not_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    stable_role_assignment_row(
                        assignment_schedule_id=SCHEDULE_ACTIVE_DIRECT,
                        role_state="Active",
                    ),
                ],
                [
                    stable_role_assignment_row(
                        assignment_schedule_id=SCHEDULE_ELIGIBLE_DIRECT,
                        role_state="Eligible",
                        role_definition_id=ROLE_DEF_GLOBAL_READER,
                    ),
                ],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (1, 1, 0))
            columns = {detail.get("column") for detail in result.details if detail["change"] == "Changed"}
            self.assertEqual(columns, set())

    def test_multiple_changed_properties_count_as_one_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    stable_role_assignment_row(
                        assignment_schedule_id=SCHEDULE_ACTIVE_GROUP,
                        role_state="Active",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                        directory_scope_id=SCOPE_TENANT,
                    ),
                ],
                [
                    stable_role_assignment_row(
                        assignment_schedule_id=SCHEDULE_ACTIVE_GROUP,
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                        directory_scope_id="/different-scope",
                    ),
                ],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.changed, 1)
            changed_columns = {
                detail["column"] for detail in result.details if detail["change"] == "Changed"
            }
            self.assertEqual(changed_columns, {"RoleState", "DirectoryScopeId"})

    def test_schema_boundary_uses_legacy_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_rows = [legacy_role_assignment_row(role_name="Global Reader", role_state="Active")]
            latest_rows = [stable_role_assignment_row(role_name="Global Reader", role_state="Active")]
            with (root / f"{self.FAMILY}_20260731-042100.csv").open(
                "w", encoding="utf-8-sig", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=list(LEGACY_ROLE_HEADERS))
                writer.writeheader()
                writer.writerows(baseline_rows)
            with (root / f"{self.FAMILY}_20260804-042100.csv").open(
                "w", encoding="utf-8-sig", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=list(STABLE_ROLE_HEADERS))
                writer.writeheader()
                writer.writerows(latest_rows)
            snapshots = scan_report_history(root)[self.FAMILY]
            baseline, latest = snapshots[0], snapshots[1]
            self.assertEqual(
                suggested_key(latest.headers, self.FAMILY),
                "Assignment schedule + User",
            )
            result = compare_snapshots(
                baseline,
                latest,
                role_assignment_suggested_key_label(baseline.headers),
                self.FAMILY,
            )
            self.assertEqual(result.stable, 1)

    def test_friendly_direct_and_group_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [
                    stable_role_assignment_row(
                        assignment_schedule_id=SCHEDULE_ACTIVE_DIRECT,
                        role_state="Active",
                        assignment_source="Direct",
                    ),
                    stable_role_assignment_row(
                        role_name="Security Reader",
                        role_definition_id=ROLE_DEF_SECURITY_READER,
                        assignment_schedule_id=SCHEDULE_ELIGIBLE_GROUP,
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="PIM-Security-Readers",
                        source_group_id=GROUP_ONE,
                        source_principal_id=GROUP_ONE,
                    ),
                ],
                legacy=False,
            )
            result = self._compare(baseline, latest)
            added = [d for d in result.details if d["change"] == "Added"]
            self.assertEqual(len(added), 2)
            self.assertEqual(detail_identity(added[0]), "Ada Lovelace → Global Reader")
            self.assertEqual(added[0]["after"], "Active · Direct")
            self.assertEqual(
                detail_identity(added[1]),
                "Ada Lovelace → Security Reader",
            )
            self.assertEqual(added[1]["after"], "Eligible · via PIM-Security-Readers")
            self.assertEqual((result.added, result.removed, result.changed), (2, 0, 0))

    def test_comparison_counts_unchanged_with_compact_presentation(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [legacy_role_assignment_row(role_name="Global Reader", role_state="Active")],
                [
                    legacy_role_assignment_row(role_name="Global Reader", role_state="Active"),
                    legacy_role_assignment_row(
                        role_name="Security Reader",
                        role_state="Eligible",
                        assignment_source="Group",
                        source_group="PIM-Readers",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (1, 0, 0))
            added = next(d for d in result.details if d["change"] == "Added")
            self.assertEqual(detail_identity(added), "Ada Lovelace → Security Reader")
            self.assertEqual(added["after"], "Eligible · via PIM-Readers")

    def test_generic_family_key_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "Department": "R&D"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")


class EntraRoleAssignmentsPresentationTests(unittest.TestCase):
    FAMILY = "Entra_Role_Assignments"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_property_labels_and_tooltips(self):
        from diffasaurus.ui.comparison_presentation import (
            property_display_text,
            property_tooltip,
        )

        self.assertEqual(property_display_text("RoleState", self.FAMILY), "Role state")
        self.assertEqual(property_display_text("DirectoryScopeId", self.FAMILY), "Directory scope")
        self.assertEqual(property_display_text("", self.FAMILY, change="Added"), "Role assignment")
        tooltip = property_tooltip("AssignmentScheduleId", self.FAMILY)
        self.assertIn("Assignment schedule ID", tooltip)
        self.assertIn("CSV field: AssignmentScheduleId", tooltip)

    def test_recent_changes_detail_table_uses_friendly_labels(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=0,
            removed=0,
            changed=1,
            stable=0,
            details=(
                {
                    "change": "Changed",
                    "key": f"{SCHEDULE_ACTIVE_DIRECT}{chr(31)}{ROLE_USER_ONE}",
                    "identity": "Ada Lovelace → Global Reader",
                    "column": "DirectoryScopeId",
                    "before": "/",
                    "after": "/administrativeUnits/abc",
                    "display_name": "Ada Lovelace",
                    "UPN": "ada@example.com",
                    "role_name": "Global Reader",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(
            section.detail_table.item(0, 1).text(),
            "Ada Lovelace → Global Reader",
        )
        self.assertEqual(section.detail_table.item(0, 2).text(), "Directory scope")

    def test_added_direct_presentation_includes_role_and_path(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.comparison_presentation import identity_tooltip
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        detail = {
            "change": "Added",
            "key": f"{SCHEDULE_ACTIVE_DIRECT}{chr(31)}{ROLE_USER_ONE}",
            "identity": "Théo DESCHAMPS → Global Reader",
            "column": "",
            "before": "",
            "after": "Active · Direct",
            "display_name": "Théo DESCHAMPS",
            "UPN": "theo.deschamps@floa.com",
            "user_id": ROLE_USER_ONE,
            "role_name": "Global Reader",
            "role_state": "Active",
            "assignment_source": "Direct",
        }
        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=1,
            removed=0,
            changed=0,
            stable=0,
            details=(detail,),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(
            section.detail_table.item(0, 1).text(),
            "Théo DESCHAMPS → Global Reader",
        )
        self.assertEqual(section.detail_table.item(0, 2).text(), "Role assignment")
        self.assertEqual(section.detail_table.item(0, 3).text(), "")
        self.assertEqual(section.detail_table.item(0, 4).text(), "Active · Direct")
        tooltip = identity_tooltip(detail, self.FAMILY)
        self.assertIn("UserPrincipalName: theo.deschamps@floa.com", tooltip)
        self.assertIn("UserId:", tooltip)

    def test_removed_group_presentation_includes_role_and_path(self):
        from diffasaurus.core.report_history import ComparisonSummary
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        section._family = self.FAMILY
        section._details = ComparisonSummary(
            added=0,
            removed=1,
            changed=0,
            stable=0,
            details=(
                {
                    "change": "Removed",
                    "key": f"{SCHEDULE_ELIGIBLE_GROUP}{chr(31)}{ROLE_USER_ONE}",
                    "identity": "Jane DOE → Security Reader",
                    "column": "",
                    "before": "Eligible · via PIM-Security-Readers",
                    "after": "",
                    "display_name": "Jane DOE",
                    "role_name": "Security Reader",
                    "role_state": "Eligible",
                    "assignment_source": "Group",
                    "source_group": "PIM-Security-Readers",
                },
            ),
        )
        section._expanded = True
        section._filter = "All"
        section._apply_detail_filters()
        self.assertEqual(
            section.detail_table.item(0, 1).text(),
            "Jane DOE → Security Reader",
        )
        self.assertEqual(
            section.detail_table.item(0, 3).text(),
            "Eligible · via PIM-Security-Readers",
        )
        self.assertEqual(section.detail_table.item(0, 4).text(), "")

    def test_compact_identity_fallbacks_without_display_name_or_role_name(self):
        from diffasaurus.core.report_history import _role_assignment_identity_label

        key = f"{SCHEDULE_ACTIVE_DIRECT}{chr(31)}{ROLE_USER_ONE}"
        after_map = {
            key: {
                "UserPrincipalName": "ada@example.com",
                "RoleName": "",
                "RoleDefinitionId": ROLE_DEF_GLOBAL_READER,
            }
        }
        self.assertEqual(
            _role_assignment_identity_label(key, "Added", {}, after_map),
            "ada@example.com → " + ROLE_DEF_GLOBAL_READER,
        )
        after_map = {
            key: {
                "DisplayName": "",
                "UserPrincipalName": "",
                "UserId": ROLE_USER_ONE,
                "RoleName": "Global Reader",
            }
        }
        self.assertEqual(
            _role_assignment_identity_label(key, "Added", {}, after_map),
            f"{ROLE_USER_ONE} → Global Reader",
        )

    def test_recent_changes_summary_uses_assignment_wording(self):
        from diffasaurus.core.report_history import ComparisonSummary, FamilyChangeStatus
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        section = FamilyChangeSection()
        status = FamilyChangeStatus(
            family=self.FAMILY,
            status="changed",
            baseline=None,
            latest=None,
            key_column="Assignment schedule + User",
            summary=ComparisonSummary(added=1, removed=1, changed=3, stable=0, details=()),
            reason="",
        )
        section.apply_status(status, datetime(2026, 8, 4, 12))
        self.assertEqual(
            section.counts_label.text(),
            "1 assignment added · 1 assignment removed · 3 assignments changed",
        )

    def test_comparison_summary_unit_uses_assignments(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(comparison_summary_unit(self.FAMILY), "assignments")


class EntraGroupMembershipComparisonTests(unittest.TestCase):
    FAMILY = "Entra_Group_User_Memberships"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _write_pair(self, root: Path, baseline_rows, latest_rows):
        template = membership_row("template-user", "template-group")
        for path, rows in (
            (root / "Entra_Group_User_Memberships_20260731-042100.csv", baseline_rows),
            (root / "Entra_Group_User_Memberships_20260804-042100.csv", latest_rows),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(template.keys()))
                writer.writeheader()
                writer.writerows(rows)
        snapshots = scan_report_history(root)[self.FAMILY]
        self.assertEqual(
            suggested_key(snapshots[0].headers, self.FAMILY),
            "User + Group",
        )
        return snapshots[0], snapshots[1]

    def _compare(self, baseline, latest):
        return compare_snapshots(
            baseline,
            latest,
            "User + Group",
            self.FAMILY,
        )

    def test_one_user_in_two_groups_is_two_memberships(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                membership_row(
                    "user-1",
                    "group-a",
                    user_display="Aissatou Ba",
                    group_name="Group A",
                ),
                membership_row(
                    "user-1",
                    "group-b",
                    user_display="Aissatou Ba",
                    group_name="Group B",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, rows)
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed, result.stable), (0, 0, 0, 2))

    def test_two_users_in_one_group_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                membership_row(
                    "user-1",
                    "group-a",
                    user_display="User One",
                    group_name="Developers",
                ),
                membership_row(
                    "user-2",
                    "group-a",
                    user_display="User Two",
                    group_name="Developers",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, rows)
            result = self._compare(baseline, latest)
            self.assertEqual(result.stable, 2)

    def test_added_membership_uses_user_id_and_group_id(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Aissatou Ba",
                        group_name="Group A",
                    ),
                ],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Aissatou Ba",
                        group_name="Group A",
                    ),
                    membership_row(
                        "user-1",
                        "group-b",
                        user_display="Aissatou Ba",
                        group_name="Group B",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.added, 1)
            added = next(detail for detail in result.details if detail["change"] == "Added")
            self.assertEqual(added["user_id"], "user-1")
            self.assertEqual(added["group_id"], "group-b")
            self.assertEqual(detail_identity(added), "Aissatou Ba → Group B")

    def test_removed_membership_uses_user_id_and_group_id(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="John Doe",
                        group_name="Legacy VPN",
                    ),
                ],
                [],
            )
            result = self._compare(baseline, latest)
            self.assertEqual(result.removed, 1)
            removed = next(detail for detail in result.details if detail["change"] == "Removed")
            self.assertEqual(removed["user_id"], "user-1")
            self.assertEqual(removed["group_id"], "group-a")
            self.assertEqual(detail_identity(removed), "John Doe → Legacy VPN")

    def test_unchanged_membership_remains_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                membership_row(
                    "user-1",
                    "group-a",
                    user_display="Jane Doe",
                    group_name="Developers",
                    membership_type="Direct",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, list(rows))
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed, result.stable), (0, 0, 0, 1))

    def test_group_name_rename_remains_changed_not_remove_add(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        group_name="Developers",
                    ),
                ],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        group_name="Engineering",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "GroupName")
            self.assertEqual(detail_identity(changed), "Jane Doe → Engineering")

    def test_upn_change_remains_changed_not_remove_add(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        upn="old.upn@example.com",
                        group_name="Developers",
                    ),
                ],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        upn="new.upn@example.com",
                        group_name="Developers",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (0, 0, 1))
            changed = next(
                detail
                for detail in result.details
                if detail["change"] == "Changed"
                and detail["column"] == "UserPrincipalName"
            )
            self.assertEqual(changed["before"], "old.upn@example.com")
            self.assertEqual(changed["after"], "new.upn@example.com")

    def test_membership_type_change_remains_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        group_name="Developers",
                        membership_type="Direct",
                    ),
                ],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        group_name="Developers",
                        membership_type="Dynamic",
                    ),
                ],
            )
            result = self._compare(baseline, latest)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed["column"], "MembershipType")
            self.assertEqual(changed["before"], "Direct")
            self.assertEqual(changed["after"], "Dynamic")
            self.assertEqual(detail_identity(changed), "Jane Doe → Developers")

    def test_missing_user_display_name_falls_back_to_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="",
                        upn="jane.doe@example.com",
                        group_name="Developers",
                    ),
                ],
            )
            added = next(detail for detail in self._compare(baseline, latest).details if detail["change"] == "Added")
            self.assertEqual(detail_identity(added), "jane.doe@example.com → Developers")

    def test_missing_group_name_falls_back_to_group_id(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        group_name="",
                    ),
                ],
            )
            added = next(detail for detail in self._compare(baseline, latest).details if detail["change"] == "Added")
            self.assertEqual(detail_identity(added), "Jane Doe → group-a")

    def test_same_upn_does_not_collapse_membership_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                membership_row(
                    "user-1",
                    "group-a",
                    upn="shared@example.com",
                    user_display="Shared User",
                    group_name="Group A",
                ),
                membership_row(
                    "user-1",
                    "group-b",
                    upn="shared@example.com",
                    user_display="Shared User",
                    group_name="Group B",
                ),
            ]
            baseline, latest = self._write_pair(Path(directory), rows, rows)
            composite = self._compare(baseline, latest)
            upn_only = compare_snapshots(baseline, latest, "UserPrincipalName")
            self.assertEqual(composite.stable, 2)
            self.assertEqual(upn_only.stable, 1)

    def test_family_change_status_uses_composite_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            rows = [
                membership_row("user-1", "group-a", user_display="A", group_name="Group A"),
                membership_row("user-1", "group-b", user_display="A", group_name="Group B"),
            ]
            write_report(
                root / "Entra_Group_User_Memberships_20260731-042100.csv",
                rows,
            )
            write_report(
                root / "Entra_Group_User_Memberships_20260804-042100.csv",
                rows,
            )
            status = family_change_status(
                self.FAMILY,
                scan_report_history(root)[self.FAMILY],
                timedelta(hours=48),
                reference=reference,
            )
            self.assertEqual(status.key_column, "User + Group")
            self.assertEqual(status.summary.stable, 2)

    def test_recent_changes_search_matches_user_group_and_ids(self):
        from diffasaurus.ui.recent_changes import FamilyChangeSection

        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        upn="jane.doe@example.com",
                        group_name="Developers",
                        membership_type="Direct",
                    ),
                ],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        user_display="Jane Doe",
                        upn="jane.doe@example.com",
                        group_name="Developers",
                        membership_type="Dynamic",
                    ),
                ],
            )
            summary = self._compare(baseline, latest)
            section = FamilyChangeSection()
            section._details = summary
            section._filter = "All"

            for needle in ("Jane Doe", "jane.doe@example.com", "Developers", "user-1", "group-a"):
                section.detail_search.setText(needle)
                section._apply_detail_filters()
                self.assertEqual(section.detail_table.rowCount(), 1, needle)
            section.close()

    def test_other_families_retain_single_key_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")
            self.assertEqual(
                composite_key_label("Entra_Users_Properties", snapshots[0].headers),
                "",
            )

    def test_compare_without_family_still_allows_manual_upn_key(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        upn="shared@example.com",
                        group_name="Group A",
                    ),
                ],
                [
                    membership_row(
                        "user-1",
                        "group-a",
                        upn="shared@example.com",
                        group_name="Group A",
                    ),
                ],
            )
            result = compare_snapshots(baseline, latest, "UserPrincipalName")
            self.assertEqual(result.stable, 1)

    def test_comparison_summary_unit_uses_memberships_for_membership_family(self):
        from diffasaurus.core.report_history import comparison_summary_unit

        self.assertEqual(
            comparison_summary_unit("Entra_Group_User_Memberships"),
            "memberships",
        )
        self.assertEqual(comparison_summary_unit("Entra_Users_Properties"), "users")

    def test_identity_tooltip_contains_full_relationship_text(self):
        from diffasaurus.ui.comparison_presentation import identity_tooltip

        identity = "Aude ZIMMERMANN → GD_PRD_MyGroup"
        detail = {
            "change": "Added",
            "key": "user-1\x1fgroup-1",
            "identity": identity,
            "user_id": "user-1",
            "group_id": "group-1",
            "column": "",
            "before": "",
            "after": "New row",
        }
        tooltip = identity_tooltip(detail)
        self.assertIn(identity, tooltip)
        self.assertIn("UserId: user-1", tooltip)
        self.assertIn("GroupId: group-1", tooltip)

    def test_long_membership_identity_is_not_truncated_in_detail(self):
        user_name = "Aude ZIMMERMANN"
        group_name = "GD_PRD_" + ("MyGroup" * 12)
        identity = f"{user_name} → {group_name}"
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [
                    membership_row(
                        "user-1",
                        "group-1",
                        user_display=user_name,
                        group_name=group_name,
                    ),
                ],
            )
            added = next(
                detail for detail in self._compare(baseline, latest).details
                if detail["change"] == "Added"
            )
            self.assertEqual(detail_identity(added), identity)
            self.assertNotIn("...", detail_identity(added))
            self.assertGreater(len(detail_identity(added)), 80)

    def test_membership_display_text_splits_user_and_group_without_ellipsis(self):
        from diffasaurus.ui.comparison_presentation import (
            identity_display_text,
            membership_identity_display_text,
        )

        identity = "Aude ZIMMERMANN → GD_PRD_Very_Long_Group_Name"
        self.assertEqual(
            membership_identity_display_text(identity),
            "Aude ZIMMERMANN\n→ GD_PRD_Very_Long_Group_Name",
        )
        detail = {"identity": identity, "key": "user-1\x1fgroup-1"}
        self.assertEqual(identity_display_text(detail, self.FAMILY), membership_identity_display_text(identity))
        self.assertNotIn("...", identity_display_text(detail, self.FAMILY))

    def test_membership_table_item_preserves_full_group_name(self):
        from PyQt6.QtWidgets import QTableWidget

        from diffasaurus.ui.comparison_presentation import (
            MEMBERSHIP_FAMILY,
            identity_tooltip,
            populate_comparison_detail_table,
        )

        user_name = "Alexandre GOMEZ"
        group_name = "GD_PRD_Extended_Access_Package_Administrators"
        identity = f"{user_name} → {group_name}"
        detail = {
            "change": "Added",
            "key": "user-1\x1fgroup-1",
            "identity": identity,
            "user_id": "user-1",
            "group_id": "group-1",
            "column": "",
            "before": "",
            "after": "New row",
        }
        table = QTableWidget(0, 5)
        populate_comparison_detail_table(table, [detail], family=MEMBERSHIP_FAMILY)
        item = table.item(0, 1)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertIn(group_name, item.text())
        self.assertNotIn("...", item.text())
        self.assertIn(identity, identity_tooltip(detail))
        table.close()

    def test_missing_group_name_falls_back_to_group_mail(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [],
                [
                    membership_row(
                        "user-1",
                        "group-1",
                        user_display="Jane Doe",
                        group_name="",
                        group_mail="finance@contoso.com",
                    ),
                ],
            )
            added = next(
                detail for detail in self._compare(baseline, latest).details
                if detail["change"] == "Added"
            )
            self.assertEqual(detail_identity(added), "Jane Doe → finance@contoso.com")

    def test_non_membership_identity_display_remains_single_line(self):
        from diffasaurus.ui.comparison_presentation import identity_display_text

        detail = {"identity": "Ada Example", "key": "ada@example.com"}
        self.assertEqual(
            identity_display_text(detail, "Entra_Users_Properties"),
            "Ada Example",
        )

    def test_membership_compare_counts_unchanged_by_presentation_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, latest = self._write_pair(
                Path(directory),
                [
                    membership_row("user-1", "group-a", group_name="Group A"),
                    membership_row("user-1", "group-b", group_name="Group B"),
                ],
                [
                    membership_row("user-1", "group-a", group_name="Group A Renamed"),
                    membership_row("user-1", "group-b", group_name="Group B"),
                    membership_row("user-2", "group-c", group_name="Group C"),
                ],
            )
            result = self._compare(baseline, latest)
            self.assertEqual((result.added, result.removed, result.changed), (1, 0, 1))


class StandaloneHistoryTests(unittest.TestCase):
    def test_timestamp_families(self):
        self.assertEqual(
            report_family("Entra_Users_Properties_20260610-041113.csv"),
            "Entra_Users_Properties",
        )
        self.assertEqual(
            report_family("Intune_Devices_Autopilot_20260610_044056.csv"),
            "Intune_Devices_Autopilot",
        )

    def test_full_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "Entra_Users_Properties_20260601-010000.csv"
            after = root / "Entra_Users_Properties_20260602-010000.csv"
            write_report(
                before,
                [
                    {"UPN": "ada@example.com", "Department": "R&D"},
                    {"UPN": "grace@example.com", "Department": "IT"},
                ],
            )
            write_report(
                after,
                [
                    {"UPN": "ada@example.com", "Department": "Engineering"},
                    {"UPN": "linus@example.com", "Department": "IT"},
                ],
            )
            snapshots = scan_report_history(root)["Entra_Users_Properties"]
            self.assertEqual(suggested_key(snapshots[0].headers), "UPN")
            result = compare_snapshots(snapshots[0], snapshots[1], "UPN")
            self.assertEqual(
                (result.added, result.removed, result.changed, result.stable),
                (1, 1, 1, 0),
            )

    def test_weekday_schedule_health_uses_csv_arrival_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stamp in ("20260724-013000", "20260727-081500", "20260728-011500"):
                write_report(
                    root / f"Entra_Users_Properties_{stamp}.csv",
                    [{"UPN": "ada@example.com", "Department": "Engineering"}],
                )
            families = scan_report_history(root)
            days = expected_business_days(datetime(2026, 7, 28, 12), count=3)
            self.assertEqual([day.isoformat() for day in days], ["2026-07-24", "2026-07-27", "2026-07-28"])
            health = report_run_health(
                families,
                reference=datetime(2026, 7, 28, 12),
                business_day_count=3,
            )[0]
            self.assertEqual((health.expected, health.observed, health.missing, health.late), (3, 3, 0, 1))
            self.assertEqual(health.status, "Completed late")

    def test_large_folder_index_does_not_parse_csv_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(500):
                captured = datetime(2026, 1, 1) + timedelta(minutes=index)
                path = root / f"Entra_Users_Properties_{captured:%Y%m%d-%H%M%S}.csv"
                path.write_text("UPN;Department\n", encoding="utf-8")

            progress = []
            with patch(
                "diffasaurus.core.report_history.read_csv_rows",
                side_effect=AssertionError("indexing must stay lazy"),
            ):
                families = scan_report_index(
                    root,
                    progress=lambda current, total, label: progress.append(
                        (current, total, label)
                    ),
                )

            snapshots = families["Entra_Users_Properties"]
            self.assertEqual(len(snapshots), 500)
            self.assertTrue(all(snapshot.row_count == -1 for snapshot in snapshots))
            self.assertTrue(all(not snapshot.headers for snapshot in snapshots))
            self.assertEqual(progress[-1][:2], (500, 500))

    def test_analysis_cache_survives_process_memory_reset(self):
        import diffasaurus.core.report_history as history

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_path = root / "analysis-cache.json"
            report = root / "Entra_Users_Properties_20260728-010000.csv"
            write_report(
                report,
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            snapshot = scan_report_index(root)["Entra_Users_Properties"][0]
            with patch.dict(
                os.environ,
                {"DIFFASAURUS_ANALYSIS_CACHE": str(cache_path)},
            ):
                hydrated, _title, _metrics = analyze_snapshot(snapshot)
                save_analysis_cache()
                self.assertEqual(hydrated.row_count, 1)
                self.assertTrue(cache_path.is_file())
                self.assertTrue(cache_path.read_bytes().startswith(b"SQLite format 3"))

                with history._CACHE_LOCK:
                    history._ANALYSIS_CACHE.clear()
                with patch.object(
                    history.CsvTableModel,
                    "load_csv",
                    side_effect=AssertionError("persistent cache should avoid parsing"),
                ):
                    cached, _title, _metrics = analyze_snapshot(snapshot)
                self.assertEqual(cached.row_count, 1)

    def test_cache_survives_report_folder_relocation(self):
        import diffasaurus.core.report_history as history

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "Original"
            relocated = root / "Relocated"
            original.mkdir()
            relocated.mkdir()
            cache_path = root / "history.sqlite3"
            name = "Entra_Users_Properties_20260728-010000.csv"
            write_report(
                original / name,
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            with patch.dict(os.environ, {"DIFFASAURUS_CACHE_DB": str(cache_path)}):
                first = scan_report_index(original)["Entra_Users_Properties"][0]
                hydrated, _title, _metrics = analyze_snapshot(first)
                self.assertEqual(hydrated.row_count, 1)
                shutil.copy2(original / name, relocated / name)
                with history._CACHE_LOCK:
                    history._ANALYSIS_CACHE.clear()
                moved = scan_report_index(relocated)["Entra_Users_Properties"][0]
                with patch.object(
                    history.CsvTableModel,
                    "load_csv",
                    side_effect=AssertionError("relocated snapshot should use SQLite cache"),
                ):
                    cached, _title, _metrics = analyze_snapshot(moved)
                self.assertEqual(cached.row_count, 1)

    def test_legacy_json_cache_is_migrated(self):
        import diffasaurus.core.report_history as history

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "Entra_Users_Properties_20260728-010000.csv"
            legacy_cache = root / "analysis-cache.json"
            write_report(
                report,
                [{"UPN": "ada@example.com", "Department": "Engineering"}],
            )
            stat = report.stat()
            old_signature = json.dumps(
                (str(report.resolve()), stat.st_size, stat.st_mtime_ns),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            legacy_cache.write_text(
                json.dumps(
                    {
                        "snapshots": {
                            old_signature: {
                                "row_count": 1,
                                "headers": ["UPN", "Department"],
                                "title": "Users",
                                "metrics": {"Report rows": 1},
                            }
                        },
                        "comparisons": {},
                    }
                ),
                encoding="utf-8",
            )
            snapshot = scan_report_index(root)["Entra_Users_Properties"][0]
            with patch.dict(
                os.environ,
                {"DIFFASAURUS_ANALYSIS_CACHE": str(legacy_cache)},
            ):
                with history._CACHE_LOCK:
                    history._ANALYSIS_CACHE.clear()
                with patch.object(
                    history.CsvTableModel,
                    "load_csv",
                    side_effect=AssertionError("migrated cache should avoid parsing"),
                ):
                    cached, title, metrics = analyze_snapshot(snapshot)
                self.assertEqual(cached.row_count, 1)
                self.assertEqual(title, "Users")
                self.assertEqual(metrics["Report rows"], 1)
                self.assertTrue(legacy_cache.with_suffix(".sqlite3").is_file())

    def test_long_history_auto_aggregates_weekly_and_filters_range(self):
        start = datetime(2024, 1, 1, 1)
        history = []
        for index in range(520):
            captured = start + timedelta(days=index)
            snapshot = history_snapshot(captured, index)
            history.append((snapshot, {"Devices": float(index)}))

        values, labels, resolution, source_points = metric_series(
            history,
            "Devices",
            aggregation="auto",
        )
        self.assertEqual(resolution, "weekly")
        self.assertLess(len(values), 90)
        self.assertEqual(source_points, 520)
        self.assertEqual(values[-1], 519)
        self.assertEqual(len(values), len(labels))

        trailing = filter_history_by_days(history, 30)
        self.assertEqual(len(trailing), 31)
        self.assertEqual(trailing[-1][1]["Devices"], 519)

    def test_schema_changes_report_added_and_removed_columns(self):
        first = history_snapshot(datetime(2026, 1, 1, 1), 1, ("Id", "Name"))
        second = history_snapshot(
            datetime(2026, 1, 2, 1),
            2,
            ("Id", "DisplayName", "Department"),
        )
        changes = schema_changes([first, second])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][1], ("DisplayName", "Department"))
        self.assertEqual(changes[0][2], ("Name",))


if __name__ == "__main__":
    unittest.main()
