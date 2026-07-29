import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.report_history import (
    analyze_snapshot,
    compare_snapshots,
    expected_business_days,
    report_family,
    report_run_health,
    scan_report_index,
    scan_report_history,
    save_analysis_cache,
    suggested_key,
)


def write_report(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


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

                with history._CACHE_LOCK:
                    history._ANALYSIS_CACHE.clear()
                    history._PERSISTENT_CACHE_SOURCE = None
                with patch.object(
                    history.CsvTableModel,
                    "load_csv",
                    side_effect=AssertionError("persistent cache should avoid parsing"),
                ):
                    cached, _title, _metrics = analyze_snapshot(snapshot)
                self.assertEqual(cached.row_count, 1)


if __name__ == "__main__":
    unittest.main()
