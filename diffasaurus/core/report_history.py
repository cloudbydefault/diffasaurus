from __future__ import annotations

import csv
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.core.paths import project_root
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

_ANALYSIS_CACHE: dict[
    tuple[str, int, int],
    tuple["ReportSnapshot", str, dict[str, float]],
] = {}
_PERSISTENT_CACHE: dict[str, dict] = {"snapshots": {}, "comparisons": {}}
_PERSISTENT_CACHE_SOURCE: Path | None = None
_PERSISTENT_CACHE_DIRTY = False
_CACHE_LOCK = threading.Lock()


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


def scan_report_index(
    reports_dir: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, list[ReportSnapshot]]:
    """Index filenames without downloading or parsing OneDrive-backed CSV data."""
    families: dict[str, list[ReportSnapshot]] = {}
    paths = list(reports_dir.glob("*.csv"))
    total = len(paths)
    if progress:
        progress(0, total, "Finding CSV snapshots")
    for index, path in enumerate(paths, start=1):
        try:
            captured_at = report_timestamp(path)
        except (OSError, ValueError):
            continue
        snapshot = ReportSnapshot(
            path=path,
            family=report_family(path),
            captured_at=captured_at,
            row_count=-1,
            headers=(),
        )
        families.setdefault(snapshot.family, []).append(snapshot)
        if progress and (index == total or index % 10 == 0):
            progress(index, total, path.name)

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


def _compare_snapshots(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
    include_details: bool,
) -> ComparisonSummary:
    common = common_headers(baseline, latest)
    if not key_column or key_column not in common:
        raise ValueError("Choose a key column shared by both reports.")

    comparison_cache_key = ""
    if not include_details:
        comparison_cache_key = _comparison_signature(baseline, latest, key_column)
        with _CACHE_LOCK:
            _load_persistent_cache_locked()
            cached = _PERSISTENT_CACHE["comparisons"].get(comparison_cache_key)
        if isinstance(cached, dict):
            try:
                return ComparisonSummary(
                    added=int(cached["added"]),
                    removed=int(cached["removed"]),
                    changed=int(cached["changed"]),
                    stable=int(cached["stable"]),
                    details=(),
                )
            except (KeyError, TypeError, ValueError):
                pass

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

    if include_details:
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
                if include_details:
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

    summary = ComparisonSummary(
        added=len(added_keys),
        removed=len(removed_keys),
        changed=changed_rows,
        stable=len(shared_keys) - changed_rows,
        details=tuple(details),
    )
    if comparison_cache_key:
        global _PERSISTENT_CACHE_DIRTY
        with _CACHE_LOCK:
            _PERSISTENT_CACHE["comparisons"][comparison_cache_key] = {
                "added": summary.added,
                "removed": summary.removed,
                "changed": summary.changed,
                "stable": summary.stable,
            }
            _PERSISTENT_CACHE_DIRTY = True
    return summary


def compare_snapshots(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
) -> ComparisonSummary:
    return _compare_snapshots(baseline, latest, key_column, include_details=True)


def compare_snapshot_counts(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
) -> ComparisonSummary:
    return _compare_snapshots(baseline, latest, key_column, include_details=False)


def _snapshot_signature(snapshot: ReportSnapshot) -> tuple[str, int, int]:
    stat = snapshot.path.stat()
    return str(snapshot.path.resolve()), stat.st_size, stat.st_mtime_ns


def _signature_key(signature: tuple[str, int, int]) -> str:
    return json.dumps(signature, ensure_ascii=False, separators=(",", ":"))


def _comparison_signature(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
) -> str:
    return json.dumps(
        (_snapshot_signature(baseline), _snapshot_signature(latest), key_column),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def analysis_cache_path() -> Path:
    override = os.environ.get("DIFFASAURUS_ANALYSIS_CACHE")
    return Path(override).expanduser() if override else project_root() / "config" / "analysis_cache.json"


def _load_persistent_cache_locked() -> None:
    global _PERSISTENT_CACHE, _PERSISTENT_CACHE_DIRTY, _PERSISTENT_CACHE_SOURCE
    source = analysis_cache_path()
    if _PERSISTENT_CACHE_SOURCE == source:
        return
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        snapshots = payload.get("snapshots", {})
        comparisons = payload.get("comparisons", {})
        if not isinstance(snapshots, dict) or not isinstance(comparisons, dict):
            raise ValueError("Invalid cache structure")
        _PERSISTENT_CACHE = {"snapshots": snapshots, "comparisons": comparisons}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        _PERSISTENT_CACHE = {"snapshots": {}, "comparisons": {}}
    _PERSISTENT_CACHE_SOURCE = source
    _PERSISTENT_CACHE_DIRTY = False


def save_analysis_cache() -> None:
    """Persist reusable metrics after a batch without delaying every snapshot."""
    global _PERSISTENT_CACHE_DIRTY
    with _CACHE_LOCK:
        _load_persistent_cache_locked()
        if not _PERSISTENT_CACHE_DIRTY:
            return
        destination = analysis_cache_path()
        temporary = destination.with_suffix(".tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(_PERSISTENT_CACHE, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError:
            return
        _PERSISTENT_CACHE_DIRTY = False


def analyze_snapshot(
    snapshot: ReportSnapshot,
) -> tuple[ReportSnapshot, str, dict[str, float]]:
    signature = _snapshot_signature(snapshot)
    signature_key = _signature_key(signature)
    with _CACHE_LOCK:
        _load_persistent_cache_locked()
        cached = _ANALYSIS_CACHE.get(signature)
        persistent = _PERSISTENT_CACHE["snapshots"].get(signature_key)
    if cached is not None:
        hydrated, title, metrics = cached
        return hydrated, title, metrics.copy()
    if isinstance(persistent, dict):
        try:
            hydrated = ReportSnapshot(
                path=snapshot.path,
                family=snapshot.family,
                captured_at=snapshot.captured_at,
                row_count=int(persistent["row_count"]),
                headers=tuple(str(header) for header in persistent["headers"]),
            )
            title = str(persistent["title"])
            metrics = {
                str(name): float(value)
                for name, value in persistent["metrics"].items()
            }
            with _CACHE_LOCK:
                _ANALYSIS_CACHE[signature] = (hydrated, title, metrics.copy())
            return hydrated, title, metrics
        except (KeyError, TypeError, ValueError):
            pass

    model = CsvTableModel()
    model.load_csv(snapshot.path)
    headers = model.headers
    hydrated = ReportSnapshot(
        path=snapshot.path,
        family=snapshot.family,
        captured_at=snapshot.captured_at,
        row_count=model.rowCount(),
        headers=headers,
    )
    title, stats = get_dashboard_definition(model, list(headers))
    metrics: dict[str, float] = {"Report rows": float(hydrated.row_count)}
    for stat in stats or []:
        value = stat.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[str(stat.get("title", "Metric"))] = float(value)
    detected_title = title or snapshot.family
    global _PERSISTENT_CACHE_DIRTY
    with _CACHE_LOCK:
        _ANALYSIS_CACHE[signature] = (hydrated, detected_title, metrics.copy())
        _PERSISTENT_CACHE["snapshots"][signature_key] = {
            "row_count": hydrated.row_count,
            "headers": list(hydrated.headers),
            "title": detected_title,
            "metrics": metrics,
        }
        _PERSISTENT_CACHE_DIRTY = True
    return hydrated, detected_title, metrics


def snapshot_metrics(snapshot: ReportSnapshot) -> tuple[str, dict[str, float]]:
    _hydrated, title, metrics = analyze_snapshot(snapshot)
    return title, metrics


def history_metrics(
    snapshots: list[ReportSnapshot],
) -> tuple[str, list[tuple[ReportSnapshot, dict[str, float]]]]:
    title = snapshots[0].family if snapshots else "Report history"
    history = []
    for snapshot in snapshots:
        hydrated, detected_title, metrics = analyze_snapshot(snapshot)
        title = detected_title
        history.append((hydrated, metrics))
    return title, history


def recent_movement(
    snapshots: list[ReportSnapshot],
    max_intervals: int = 12,
) -> tuple[list[tuple[str, int, int, int]], ComparisonSummary | None]:
    series: list[tuple[str, int, int, int]] = []
    latest_summary = None
    window = snapshots[-(max_intervals + 1):]
    for baseline, latest in zip(window[:-1], window[1:]):
        headers = common_headers(baseline, latest)
        key = suggested_key(headers)
        if not key:
            continue
        try:
            summary = compare_snapshot_counts(baseline, latest, key)
        except Exception:
            continue
        latest_summary = summary
        series.append(
            (
                latest.captured_at.strftime("%d %b"),
                summary.added,
                summary.removed,
                summary.changed,
            )
        )
    save_analysis_cache()
    return series, latest_summary
