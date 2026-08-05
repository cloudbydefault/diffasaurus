from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

SUPPORTED_FAMILIES = (
    "Entra_Users_Properties",
    "Entra_Users_Activity",
    "Entra_Users_AuthenticationMethods",
)


def write_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def generate_user_properties_history(
    root: Path,
    *,
    file_count: int,
    rows_per_file: int,
    start: datetime | None = None,
) -> list[Path]:
    start = start or datetime(2026, 1, 1, 1, 0, 0)
    paths: list[Path] = []
    for index in range(file_count):
        captured_at = start + timedelta(days=index)
        path = root / f"Entra_Users_Properties_{captured_at:%Y%m%d-%H%M%S}.csv"
        rows = [
            {
                "Id": f"user-{row_index % max(rows_per_file, 1)}",
                "UPN": f"user{row_index % max(rows_per_file, 1)}@example.com",
                "DisplayName": f"User {row_index % max(rows_per_file, 1)}",
                "Department": "Engineering",
            }
            for row_index in range(rows_per_file)
        ]
        write_report(path, rows)
        paths.append(path)
    return paths


def generate_multi_family_manifest(
    root: Path,
    *,
    families: tuple[str, ...] = SUPPORTED_FAMILIES,
    snapshots_per_family: int = 2,
) -> list[Path]:
    paths: list[Path] = []
    start = datetime(2026, 6, 1, 1, 0, 0)
    for family_index, family in enumerate(families):
        for snapshot_index in range(snapshots_per_family):
            captured_at = start + timedelta(days=family_index * 10 + snapshot_index)
            path = root / f"{family}_{captured_at:%Y%m%d-%H%M%S}.csv"
            if family.startswith("Entra_Users"):
                rows = [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada Lovelace",
                    }
                ]
            else:
                rows = [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}]
            write_report(path, rows)
            paths.append(path)
    unsupported = root / "Unknown_ReportFamily_20260701-010000.csv"
    write_report(unsupported, [{"Column": "value"}])
    paths.append(unsupported)
    return paths
