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
