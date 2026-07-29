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
    ReportSnapshot,
    analyze_snapshot,
    compare_snapshots,
    expected_business_days,
    filter_history_by_days,
    metric_series,
    report_family,
    report_run_health,
    scan_report_index,
    scan_report_history,
    save_analysis_cache,
    schema_changes,
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
