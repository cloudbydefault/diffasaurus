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
            self.assertNotIn("identity", added)
            self.assertEqual(detail_identity(added), "linus@example.com")

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
        self.assertEqual(comparison_summary_unit("Entra_Users_Properties"), "rows")

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
