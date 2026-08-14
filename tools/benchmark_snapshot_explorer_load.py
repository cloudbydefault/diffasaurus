#!/usr/bin/env python3
"""Development benchmark for Snapshot Explorer large-CSV GUI handoff."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.core.settings import get_active_reports_dir
from diffasaurus.models.csv_model import read_csv_table
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer, load_snapshot_payload

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

ROW_COUNT = 60_000


def generate_membership_rows(count: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for index in range(count):
        group = index % 250
        rows.append(
            [
                f"user{index % 5000}@example.com",
                f"User {index % 5000}",
                f"user{index % 5000}@example.com",
                f"user-id-{index % 5000}",
                "Member" if index % 3 else "Guest",
                "True" if index % 5 else "False",
                f"group-id-{group}",
                f"Group {group}",
                f"group{group}@example.com",
                "Security" if group % 2 else "Microsoft365",
                "Assigned" if index % 4 else "Dynamic",
                "2026-01-01",
                "Entra",
                "Engineering",
                "Analyst",
                "Paris",
                f"note-{index}",
            ]
        )
    return rows


def write_synthetic_csv(path: Path, row_count: int = ROW_COUNT) -> None:
    lines = [",".join(MEMBERSHIP_HEADERS)]
    for row in generate_membership_rows(row_count):
        lines.append(",".join(row))
    path.write_text("\n".join(lines), encoding="utf-8")


def _timed(label: str, function) -> float:
    start = time.perf_counter()
    function()
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"{label:<34} {elapsed_ms:10.2f} ms")
    return elapsed_ms


def benchmark_snapshot(path: Path, row_count: int) -> None:
    _APP = QApplication.instance() or QApplication([])
    print(f"\n=== {path.name} ({row_count:,} rows) ===")

    payload_box: list = []

    def _parse():
        payload_box.append(load_snapshot_payload(path))

    _timed("CSV worker parse", _parse)
    headers, rows, delimiter, title, stats = payload_box[0]

    explorer = SnapshotExplorer()
    explorer._generation = 1
    snapshot = ReportSnapshot(
        path=path,
        family="Entra_Group_User_Memberships",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        row_count=row_count,
        headers=tuple(headers),
    )

    gui_total = _timed(
        "GUI handoff total",
        lambda: explorer._snapshot_loaded(1, snapshot, payload_box[0]),
    )
    sort_ms = _timed(
        "explicit sort UPN",
        lambda: explorer.table.sortByColumn(
            headers.index("UserPrincipalName"),
            Qt.SortOrder.AscendingOrder,
        ),
    )
    _timed(
        "smart search user42",
        lambda: (explorer.search.setText("user42"), explorer._apply_search()),
    )
    print(f"{'search visible rows':<34} {explorer.proxy.rowCount():10,d}")
    print(f"{'GUI total + explicit sort':<34} {gui_total + sort_ms:10.2f} ms")


def main() -> None:
    _APP = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        synthetic = Path(directory) / "Entra_Group_User_Memberships_20260101-010000.csv"
        print(f"Generating {ROW_COUNT:,} synthetic rows...")
        write_synthetic_csv(synthetic)
        benchmark_snapshot(synthetic, ROW_COUNT)

    reports_dir = get_active_reports_dir()
    matches = sorted(reports_dir.glob("Entra_Group_User_Memberships_*.csv"))
    if matches:
        real_path = matches[-1]
        _, real_rows, _ = read_csv_table(real_path)
        benchmark_snapshot(real_path, len(real_rows))


if __name__ == "__main__":
    main()
