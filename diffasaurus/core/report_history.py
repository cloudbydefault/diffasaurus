from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.models.csv_model import CsvTableModel


TIMESTAMP_RE = re.compile(r"(?P<date>\d{8})[-_](?P<time>\d{6})$")

PREFERRED_KEYS = (
    "UserPrincipalName",
    "UPN",
    "Id",
    "DeviceId",
    "AzureADDeviceId",
    "SerialNumber",
    "PrimarySmtpAddress",
    "GroupId",
    "AccessPackageId",
)


@dataclass(frozen=True)
class ReportSnapshot:
    path: Path
    family: str
    captured_at: datetime
    row_count: int
    headers: tuple[str, ...]

    @property
    def label(self) -> str:
        return self.captured_at.strftime("%d %b %Y · %H:%M")


@dataclass(frozen=True)
class ComparisonSummary:
    added: int
    removed: int
    changed: int
    stable: int
    details: tuple[dict[str, str], ...]

    @property
    def total_changes(self) -> int:
        return self.added + self.removed + self.changed


@dataclass(frozen=True)
class FamilyRunHealth:
    family: str
    expected: int
    observed: int
    missing: int
    late: int
    latest: datetime | None
    days: tuple[tuple[date, ReportSnapshot | None], ...]

    @property
    def coverage(self) -> float:
        return self.observed / self.expected if self.expected else 1.0

    @property
    def status(self) -> str:
        if not self.expected:
            return "No schedule window"
        if not self.missing and not self.late:
            return "Healthy"
        if not self.missing:
            return "Completed late" if self.late else "Healthy"
        if self.coverage >= 0.8:
            return "Attention"
        return "Missing runs"


def report_family(path: Path | str) -> str:
    stem = Path(path).stem
    return re.sub(r"[-_]?\d{8}[-_]\d{6}$", "", stem).rstrip("_- ")


def report_timestamp(path: Path) -> datetime:
    match = TIMESTAMP_RE.search(path.stem)
    if match:
        return datetime.strptime(
            match.group("date") + match.group("time"),
            "%Y%m%d%H%M%S",
        )
    return datetime.fromtimestamp(path.stat().st_mtime)


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                sample = handle.read(8192).replace("\x00", "")
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
                except csv.Error:
                    delimiter = max((";", ",", "\t"), key=sample.count)
                    dialect = csv.excel
                    dialect.delimiter = delimiter
                reader = csv.DictReader(handle, dialect=dialect)
                headers = [str(value or "").strip() for value in (reader.fieldnames or [])]
                rows = [
                    {header: str(row.get(header, "") or "") for header in headers}
                    for row in reader
                ]
                return headers, rows
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Unable to read {path.name}: {last_error}")


def scan_report_history(reports_dir: Path) -> dict[str, list[ReportSnapshot]]:
    families: dict[str, list[ReportSnapshot]] = {}
    for path in reports_dir.glob("*.csv"):
        try:
            headers, rows = read_csv_rows(path)
        except Exception:
            continue
        snapshot = ReportSnapshot(
            path=path,
            family=report_family(path),
            captured_at=report_timestamp(path),
            row_count=len(rows),
            headers=tuple(headers),
        )
        families.setdefault(snapshot.family, []).append(snapshot)

    for snapshots in families.values():
        snapshots.sort(key=lambda item: item.captured_at)
    return dict(sorted(families.items(), key=lambda item: item[0].lower()))


def expected_business_days(
    reference: datetime | None = None,
    count: int = 10,
    scheduled_hour: int = 1,
) -> list[date]:
    reference = reference or datetime.now()
    cursor = reference.date()
    scheduled_today = datetime.combine(cursor, time(scheduled_hour))
    if reference < scheduled_today:
        cursor -= timedelta(days=1)

    result: list[date] = []
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(result))


def report_run_health(
    families: dict[str, list[ReportSnapshot]],
    reference: datetime | None = None,
    business_day_count: int = 10,
    scheduled_hour: int = 1,
    grace_hours: int = 6,
) -> list[FamilyRunHealth]:
    days = expected_business_days(reference, business_day_count, scheduled_hour)
    health: list[FamilyRunHealth] = []
    for family, snapshots in families.items():
        snapshots_by_day: dict[date, ReportSnapshot] = {}
        for snapshot in snapshots:
            captured_day = snapshot.captured_at.date()
            current = snapshots_by_day.get(captured_day)
            if current is None or snapshot.captured_at > current.captured_at:
                snapshots_by_day[captured_day] = snapshot

        observations = tuple((day, snapshots_by_day.get(day)) for day in days)
        observed = sum(snapshot is not None for _, snapshot in observations)
        late = sum(
            snapshot is not None
            and snapshot.captured_at.time() > time(min(scheduled_hour + grace_hours, 23), 0)
            for _, snapshot in observations
        )
        health.append(
            FamilyRunHealth(
                family=family,
                expected=len(days),
                observed=observed,
                missing=len(days) - observed,
                late=late,
                latest=snapshots[-1].captured_at if snapshots else None,
                days=observations,
            )
        )
    return sorted(health, key=lambda item: (item.status == "Healthy", item.family.lower()))


def common_headers(baseline: ReportSnapshot, latest: ReportSnapshot) -> list[str]:
    baseline_headers = set(baseline.headers)
    return [header for header in latest.headers if header in baseline_headers]


def suggested_key(headers: list[str] | tuple[str, ...]) -> str:
    normalized = {header.lower(): header for header in headers}
    for candidate in PREFERRED_KEYS:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return headers[0] if headers else ""


def compare_snapshots(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
) -> ComparisonSummary:
    common = common_headers(baseline, latest)
    if not key_column or key_column not in common:
        raise ValueError("Choose a key column shared by both reports.")

    _, baseline_rows = read_csv_rows(baseline.path)
    _, latest_rows = read_csv_rows(latest.path)

    def keyed(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        result = {}
        for row in rows:
            key = str(row.get(key_column, "") or "").strip()
            if key:
                result[key] = row
        return result

    before_map = keyed(baseline_rows)
    after_map = keyed(latest_rows)
    before_keys = set(before_map)
    after_keys = set(after_map)
    added_keys = sorted(after_keys - before_keys, key=str.lower)
    removed_keys = sorted(before_keys - after_keys, key=str.lower)
    shared_keys = sorted(before_keys & after_keys, key=str.lower)
    details: list[dict[str, str]] = []

    for key in added_keys:
        details.append(
            {"change": "Added", "key": key, "column": "", "before": "", "after": "New row"}
        )
    for key in removed_keys:
        details.append(
            {"change": "Removed", "key": key, "column": "", "before": "Existing row", "after": ""}
        )

    changed_rows = 0
    for key in shared_keys:
        row_changed = False
        for column in common:
            if column == key_column:
                continue
            before_value = str(before_map[key].get(column, "") or "").strip()
            after_value = str(after_map[key].get(column, "") or "").strip()
            if before_value != after_value:
                row_changed = True
                details.append(
                    {
                        "change": "Changed",
                        "key": key,
                        "column": column,
                        "before": before_value,
                        "after": after_value,
                    }
                )
        changed_rows += int(row_changed)

    return ComparisonSummary(
        added=len(added_keys),
        removed=len(removed_keys),
        changed=changed_rows,
        stable=len(shared_keys) - changed_rows,
        details=tuple(details),
    )


def snapshot_metrics(snapshot: ReportSnapshot) -> tuple[str, dict[str, float]]:
    model = CsvTableModel()
    model.load_csv(snapshot.path)
    title, stats = get_dashboard_definition(model, list(snapshot.headers))
    metrics: dict[str, float] = {"Report rows": float(snapshot.row_count)}
    for stat in stats or []:
        value = stat.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[str(stat.get("title", "Metric"))] = float(value)
    return title or snapshot.family, metrics


def history_metrics(
    snapshots: list[ReportSnapshot],
) -> tuple[str, list[tuple[ReportSnapshot, dict[str, float]]]]:
    title = snapshots[0].family if snapshots else "Report history"
    history = []
    for snapshot in snapshots:
        detected_title, metrics = snapshot_metrics(snapshot)
        title = detected_title
        history.append((snapshot, metrics))
    return title, history
