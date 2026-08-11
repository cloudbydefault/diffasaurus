from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Literal

from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.core.paths import user_data_dir
from diffasaurus.models.csv_model import CsvTableModel


TIMESTAMP_RE = re.compile(r"(?P<date>\d{8})[-_](?P<time>\d{6})$")

RECENT_CHANGE_PERIODS: tuple[tuple[str, timedelta], ...] = (
    ("24 hours", timedelta(hours=24)),
    ("48 hours", timedelta(hours=48)),
    ("3 days", timedelta(days=3)),
    ("7 days", timedelta(days=7)),
    ("15 days", timedelta(days=15)),
    ("30 days", timedelta(days=30)),
)

REASON_NOT_ENOUGH_SNAPSHOTS = "Not enough snapshots to compare."
REASON_STALE_LATEST = "No snapshot was collected during the selected period."
REASON_NO_BASELINE = "No baseline snapshot exists at or before the period cutoff."
REASON_SINGLE_SNAPSHOT = "Only one distinct snapshot spans the selected period."
REASON_UNABLE_TO_COMPARE = "Unable to compare snapshots."

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

FAMILY_IDENTITY_DISPLAY: dict[str, tuple[str, ...]] = {
    "Entra_Groups_Dependencies": ("DisplayName",),
}

_ANALYSIS_CACHE: dict[
    tuple[str, str, str, int, int],
    tuple["ReportSnapshot", str, dict[str, float]],
] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_SCHEMA_VERSION = 1
_INITIALIZED_CACHE_PATHS: set[Path] = set()


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
class PeriodPairResult:
    baseline: ReportSnapshot | None
    latest: ReportSnapshot | None
    reason: str
    reference: datetime
    cutoff: datetime


@dataclass(frozen=True)
class FamilyChangeStatus:
    family: str
    status: Literal["changed", "unchanged", "no_data"]
    baseline: ReportSnapshot | None
    latest: ReportSnapshot | None
    key_column: str
    summary: ComparisonSummary | None
    reason: str


@dataclass(frozen=True)
class RecentChangesReport:
    period_label: str
    reference: datetime
    cutoff: datetime
    families: tuple[FamilyChangeStatus, ...]

    @property
    def changed_count(self) -> int:
        return sum(1 for item in self.families if item.status == "changed")

    @property
    def unchanged_count(self) -> int:
        return sum(1 for item in self.families if item.status == "unchanged")

    @property
    def no_data_count(self) -> int:
        return sum(1 for item in self.families if item.status == "no_data")

    @property
    def total_added(self) -> int:
        return sum(item.summary.added for item in self.families if item.summary)

    @property
    def total_removed(self) -> int:
        return sum(item.summary.removed for item in self.families if item.summary)

    @property
    def total_changed(self) -> int:
        return sum(item.summary.changed for item in self.families if item.summary)


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


def snapshot_with_headers(snapshot: ReportSnapshot) -> ReportSnapshot:
    if snapshot.headers:
        return snapshot
    try:
        headers, rows = read_csv_rows(snapshot.path)
    except Exception:
        return snapshot
    row_count = snapshot.row_count if snapshot.row_count >= 0 else len(rows)
    return ReportSnapshot(
        path=snapshot.path,
        family=snapshot.family,
        captured_at=snapshot.captured_at,
        row_count=row_count,
        headers=tuple(headers),
    )


def suggested_key(headers: list[str] | tuple[str, ...]) -> str:
    normalized = {header.lower(): header for header in headers}
    for candidate in PREFERRED_KEYS:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return headers[0] if headers else ""


def identity_display_column(
    family: str | None,
    headers: list[str] | tuple[str, ...],
) -> str:
    if not family:
        return ""
    candidates = FAMILY_IDENTITY_DISPLAY.get(family, ())
    normalized = {header.lower(): header for header in headers}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return ""


def detail_identity(detail: dict[str, str]) -> str:
    return detail.get("identity") or detail.get("key", "")


def _identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
    display_column: str,
) -> str:
    before_row = before_map.get(key, {})
    after_row = after_map.get(key, {})
    if change == "Added":
        display = str(after_row.get(display_column, "") or "").strip()
    elif change == "Removed":
        display = str(before_row.get(display_column, "") or "").strip()
    else:
        display = str(after_row.get(display_column, "") or "").strip()
        if not display:
            display = str(before_row.get(display_column, "") or "").strip()
    return display or key


def _compare_snapshots(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
    include_details: bool,
    family: str | None = None,
) -> ComparisonSummary:
    common = common_headers(baseline, latest)
    if not key_column or key_column not in common:
        raise ValueError("Choose a key column shared by both reports.")

    comparison_cache_key = ""
    if not include_details:
        comparison_cache_key = _comparison_signature(baseline, latest, key_column)
        with _CACHE_LOCK:
            try:
                with _cache_connection_locked() as connection:
                    cached = connection.execute(
                        """
                        SELECT added, removed, changed, stable
                        FROM comparison_cache WHERE signature=?
                        """,
                        (comparison_cache_key,),
                    ).fetchone()
            except (OSError, sqlite3.Error):
                cached = None
        if cached is not None:
            try:
                return ComparisonSummary(
                    added=int(cached[0]),
                    removed=int(cached[1]),
                    changed=int(cached[2]),
                    stable=int(cached[3]),
                    details=(),
                )
            except (TypeError, ValueError):
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
    display_column = identity_display_column(family, common) if include_details else ""

    if include_details:
        for key in added_keys:
            detail = {
                "change": "Added",
                "key": key,
                "column": "",
                "before": "",
                "after": "New row",
            }
            if display_column:
                detail["identity"] = _identity_label(
                    key, "Added", before_map, after_map, display_column
                )
            details.append(detail)
        for key in removed_keys:
            detail = {
                "change": "Removed",
                "key": key,
                "column": "",
                "before": "Existing row",
                "after": "",
            }
            if display_column:
                detail["identity"] = _identity_label(
                    key, "Removed", before_map, after_map, display_column
                )
            details.append(detail)

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
                    detail = {
                        "change": "Changed",
                        "key": key,
                        "column": column,
                        "before": before_value,
                        "after": after_value,
                    }
                    if display_column:
                        detail["identity"] = _identity_label(
                            key, "Changed", before_map, after_map, display_column
                        )
                    details.append(detail)
        changed_rows += int(row_changed)

    summary = ComparisonSummary(
        added=len(added_keys),
        removed=len(removed_keys),
        changed=changed_rows,
        stable=len(shared_keys) - changed_rows,
        details=tuple(details),
    )
    if comparison_cache_key:
        with _CACHE_LOCK:
            try:
                with _cache_connection_locked() as connection:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO comparison_cache(
                            signature, added, removed, changed, stable, updated_at
                        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (
                            comparison_cache_key,
                            summary.added,
                            summary.removed,
                            summary.changed,
                            summary.stable,
                        ),
                    )
            except (OSError, sqlite3.Error):
                pass
    return summary


def compare_snapshots(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
    family: str | None = None,
) -> ComparisonSummary:
    return _compare_snapshots(
        baseline,
        latest,
        key_column,
        include_details=True,
        family=family,
    )


def compare_snapshot_counts(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
) -> ComparisonSummary:
    return _compare_snapshots(baseline, latest, key_column, include_details=False)


def _snapshot_signature(snapshot: ReportSnapshot) -> tuple[str, str, str, int, int]:
    stat = snapshot.path.stat()
    return (
        snapshot.path.name,
        snapshot.family,
        snapshot.captured_at.isoformat(),
        stat.st_size,
        stat.st_mtime_ns,
    )


def _signature_key(signature: tuple) -> str:
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
    database_override = os.environ.get("DIFFASAURUS_CACHE_DB")
    if database_override:
        return Path(database_override).expanduser()
    legacy_override = os.environ.get("DIFFASAURUS_ANALYSIS_CACHE")
    if legacy_override:
        candidate = Path(legacy_override).expanduser()
        try:
            if candidate.is_file():
                with candidate.open("rb") as handle:
                    if handle.read(1) == b"{":
                        return candidate.with_suffix(".sqlite3")
        except OSError:
            pass
        return candidate
    return user_data_dir() / "config" / "history_cache.sqlite3"


def _legacy_cache_path() -> Path:
    override = os.environ.get("DIFFASAURUS_ANALYSIS_CACHE")
    if override:
        return Path(override).expanduser()
    return user_data_dir() / "config" / "analysis_cache.json"


def _open_cache_locked() -> sqlite3.Connection:
    destination = analysis_cache_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    needs_initialization = (
        destination not in _INITIALIZED_CACHE_PATHS or not destination.is_file()
    )
    connection = sqlite3.connect(destination, timeout=15)
    connection.execute("PRAGMA synchronous=NORMAL")
    if needs_initialization:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshot_cache (
                signature TEXT PRIMARY KEY,
                row_count INTEGER NOT NULL,
                headers_json TEXT NOT NULL,
                title TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS comparison_cache (
                signature TEXT PRIMARY KEY,
                added INTEGER NOT NULL,
                removed INTEGER NOT NULL,
                changed INTEGER NOT NULL,
                stable INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(_CACHE_SCHEMA_VERSION),),
        )
        _migrate_legacy_cache_locked(connection, destination)
        _INITIALIZED_CACHE_PATHS.add(destination)
    return connection


@contextmanager
def _cache_connection_locked():
    connection = _open_cache_locked()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _portable_legacy_snapshot_signature(raw_signature: str) -> str | None:
    try:
        path, size, modified = json.loads(raw_signature)
        filename = Path(path).name
        return _signature_key(
            (
                filename,
                report_family(filename),
                report_timestamp(Path(filename)).isoformat(),
                int(size),
                int(modified),
            )
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _portable_legacy_comparison_signature(raw_signature: str) -> str | None:
    try:
        baseline, latest, key_column = json.loads(raw_signature)

        def portable(parts) -> tuple[str, str, str, int, int]:
            path, size, modified = parts
            filename = Path(path).name
            return (
                filename,
                report_family(filename),
                report_timestamp(Path(filename)).isoformat(),
                int(size),
                int(modified),
            )

        return _signature_key((portable(baseline), portable(latest), key_column))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _migrate_legacy_cache_locked(
    connection: sqlite3.Connection,
    destination: Path,
) -> None:
    migrated = connection.execute(
        "SELECT value FROM metadata WHERE key='legacy_json_migrated'"
    ).fetchone()
    if migrated:
        return
    legacy = _legacy_cache_path()
    if legacy == destination or not legacy.is_file():
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('legacy_json_migrated', 'none')"
        )
        connection.commit()
        return
    try:
        payload = json.loads(legacy.read_text(encoding="utf-8"))
        snapshots = payload.get("snapshots", {})
        comparisons = payload.get("comparisons", {})
        if not isinstance(snapshots, dict) or not isinstance(comparisons, dict):
            raise ValueError("Invalid legacy cache")
        for raw_signature, cached in snapshots.items():
            signature = _portable_legacy_snapshot_signature(raw_signature)
            if not signature or not isinstance(cached, dict):
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO snapshot_cache(
                    signature, row_count, headers_json, title, metrics_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    signature,
                    int(cached["row_count"]),
                    json.dumps(cached["headers"], ensure_ascii=False),
                    str(cached["title"]),
                    json.dumps(cached["metrics"], ensure_ascii=False),
                ),
            )
        for raw_signature, cached in comparisons.items():
            signature = _portable_legacy_comparison_signature(raw_signature)
            if not signature or not isinstance(cached, dict):
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO comparison_cache(
                    signature, added, removed, changed, stable
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    signature,
                    int(cached["added"]),
                    int(cached["removed"]),
                    int(cached["changed"]),
                    int(cached["stable"]),
                ),
            )
        migration_result = "complete"
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        migration_result = "invalid"
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('legacy_json_migrated', ?)",
        (migration_result,),
    )
    connection.commit()


def save_analysis_cache() -> None:
    """Compatibility hook; SQLite entries are committed incrementally."""


def analyze_snapshot(
    snapshot: ReportSnapshot,
) -> tuple[ReportSnapshot, str, dict[str, float]]:
    signature = _snapshot_signature(snapshot)
    signature_key = _signature_key(signature)
    with _CACHE_LOCK:
        cached = _ANALYSIS_CACHE.get(signature)
    if cached is not None:
        hydrated, title, metrics = cached
        return hydrated, title, metrics.copy()
    with _CACHE_LOCK:
        try:
            with _cache_connection_locked() as connection:
                persistent = connection.execute(
                    """
                    SELECT row_count, headers_json, title, metrics_json
                    FROM snapshot_cache WHERE signature=?
                    """,
                    (signature_key,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            persistent = None
    if persistent is not None:
        try:
            row_count, headers_json, title, metrics_json = persistent
            hydrated = ReportSnapshot(
                path=snapshot.path,
                family=snapshot.family,
                captured_at=snapshot.captured_at,
                row_count=int(row_count),
                headers=tuple(str(header) for header in json.loads(headers_json)),
            )
            metrics = {
                str(name): float(value)
                for name, value in json.loads(metrics_json).items()
            }
            with _CACHE_LOCK:
                _ANALYSIS_CACHE[signature] = (hydrated, str(title), metrics.copy())
            return hydrated, str(title), metrics
        except (TypeError, ValueError, json.JSONDecodeError):
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
    with _CACHE_LOCK:
        _ANALYSIS_CACHE[signature] = (hydrated, detected_title, metrics.copy())
        try:
            with _cache_connection_locked() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO snapshot_cache(
                        signature, row_count, headers_json, title, metrics_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        signature_key,
                        hydrated.row_count,
                        json.dumps(list(hydrated.headers), ensure_ascii=False),
                        detected_title,
                        json.dumps(metrics, ensure_ascii=False),
                    ),
                )
        except (OSError, sqlite3.Error):
            pass
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


def filter_history_by_days(
    history: list[tuple[ReportSnapshot, dict[str, float]]],
    days: int | None,
) -> list[tuple[ReportSnapshot, dict[str, float]]]:
    """Return a trailing date window anchored to the newest available snapshot."""
    if not history or days is None:
        return list(history)
    cutoff = history[-1][0].captured_at - timedelta(days=days)
    return [entry for entry in history if entry[0].captured_at >= cutoff]


def metric_series(
    history: list[tuple[ReportSnapshot, dict[str, float]]],
    metric: str,
    days: int | None = None,
    aggregation: str = "auto",
    max_daily_points: int = 140,
) -> tuple[list[float], list[str], str, int]:
    """Build a readable stock-metric series, keeping the last value per period."""
    filtered = filter_history_by_days(history, days)
    points = [
        (snapshot.captured_at, float(metrics[metric]))
        for snapshot, metrics in filtered
        if metric in metrics
    ]
    if not points:
        return [], [], "daily", 0

    effective = aggregation.lower()
    if effective == "auto":
        span_days = max((points[-1][0] - points[0][0]).days, 0)
        if len(points) <= max_daily_points:
            effective = "daily"
        elif span_days <= 730:
            effective = "weekly"
        else:
            effective = "monthly"
    if effective not in {"daily", "weekly", "monthly"}:
        raise ValueError(f"Unknown history aggregation: {aggregation}")

    selected = points
    if effective != "daily":
        buckets: dict[tuple[int, ...], tuple[datetime, float]] = {}
        for captured_at, value in points:
            if effective == "weekly":
                iso = captured_at.isocalendar()
                bucket = (iso.year, iso.week)
            else:
                bucket = (captured_at.year, captured_at.month)
            buckets[bucket] = (captured_at, value)
        selected = list(buckets.values())

    label_format = "%d %b" if effective != "monthly" else "%b %y"
    return (
        [value for _captured_at, value in selected],
        [captured_at.strftime(label_format) for captured_at, _value in selected],
        effective,
        len(points),
    )


def schema_changes(
    snapshots: list[ReportSnapshot],
) -> list[tuple[datetime, tuple[str, ...], tuple[str, ...]]]:
    """Describe adjacent header changes so long histories expose schema drift."""
    changes = []
    hydrated = [snapshot for snapshot in snapshots if snapshot.headers]
    for baseline, latest in zip(hydrated[:-1], hydrated[1:]):
        before = set(baseline.headers)
        after = set(latest.headers)
        added = tuple(header for header in latest.headers if header not in before)
        removed = tuple(header for header in baseline.headers if header not in after)
        if added or removed:
            changes.append((latest.captured_at, added, removed))
    return changes


def period_window(
    period: timedelta,
    reference: datetime | None = None,
) -> tuple[datetime, datetime]:
    resolved = reference or datetime.now()
    return resolved, resolved - period


def resolve_period_pair(
    snapshots: list[ReportSnapshot],
    period: timedelta,
    reference: datetime | None = None,
) -> PeriodPairResult:
    reference_at, cutoff = period_window(period, reference)
    if not snapshots:
        return PeriodPairResult(
            baseline=None,
            latest=None,
            reason=REASON_NOT_ENOUGH_SNAPSHOTS,
            reference=reference_at,
            cutoff=cutoff,
        )
    if len(snapshots) < 2:
        return PeriodPairResult(
            baseline=None,
            latest=snapshots[-1],
            reason=REASON_NOT_ENOUGH_SNAPSHOTS,
            reference=reference_at,
            cutoff=cutoff,
        )

    latest = snapshots[-1]
    if latest.captured_at <= cutoff:
        return PeriodPairResult(
            baseline=None,
            latest=latest,
            reason=REASON_STALE_LATEST,
            reference=reference_at,
            cutoff=cutoff,
        )

    baselines = [snapshot for snapshot in snapshots if snapshot.captured_at <= cutoff]
    if not baselines:
        return PeriodPairResult(
            baseline=None,
            latest=latest,
            reason=REASON_NO_BASELINE,
            reference=reference_at,
            cutoff=cutoff,
        )

    baseline = baselines[-1]
    if baseline.path == latest.path:
        return PeriodPairResult(
            baseline=baseline,
            latest=latest,
            reason=REASON_SINGLE_SNAPSHOT,
            reference=reference_at,
            cutoff=cutoff,
        )

    return PeriodPairResult(
        baseline=baseline,
        latest=latest,
        reason="",
        reference=reference_at,
        cutoff=cutoff,
    )


def select_period_snapshots(
    snapshots: list[ReportSnapshot],
    period: timedelta,
    reference: datetime | None = None,
) -> tuple[ReportSnapshot, ReportSnapshot] | None:
    result = resolve_period_pair(snapshots, period, reference)
    if result.reason or result.baseline is None or result.latest is None:
        return None
    return result.baseline, result.latest


def family_change_status(
    family: str,
    snapshots: list[ReportSnapshot],
    period: timedelta,
    reference: datetime | None = None,
    include_details: bool = False,
) -> FamilyChangeStatus:
    pairing = resolve_period_pair(snapshots, period, reference)
    if pairing.reason:
        return FamilyChangeStatus(
            family=family,
            status="no_data",
            baseline=pairing.baseline,
            latest=pairing.latest,
            key_column="",
            summary=None,
            reason=pairing.reason,
        )

    baseline = pairing.baseline
    latest = pairing.latest
    assert baseline is not None and latest is not None

    baseline = snapshot_with_headers(baseline)
    latest = snapshot_with_headers(latest)
    headers = common_headers(baseline, latest)
    key_column = suggested_key(headers)
    if not key_column:
        return FamilyChangeStatus(
            family=family,
            status="no_data",
            baseline=baseline,
            latest=latest,
            key_column="",
            summary=None,
            reason=REASON_UNABLE_TO_COMPARE,
        )

    try:
        if include_details:
            summary = compare_snapshots(baseline, latest, key_column, family)
        else:
            summary = compare_snapshot_counts(baseline, latest, key_column)
    except Exception:
        return FamilyChangeStatus(
            family=family,
            status="no_data",
            baseline=baseline,
            latest=latest,
            key_column=key_column,
            summary=None,
            reason=REASON_UNABLE_TO_COMPARE,
        )

    status: Literal["changed", "unchanged"] = (
        "changed" if summary.total_changes else "unchanged"
    )
    return FamilyChangeStatus(
        family=family,
        status=status,
        baseline=baseline,
        latest=latest,
        key_column=key_column,
        summary=summary,
        reason="",
    )


def _recent_changes_sort_key(item: FamilyChangeStatus) -> tuple[int, str]:
    status_rank = {"changed": 0, "unchanged": 1, "no_data": 2}
    return status_rank.get(item.status, 3), item.family.lower()


def aggregate_recent_changes(
    families: dict[str, list[ReportSnapshot]],
    period: timedelta,
    reference: datetime | None = None,
    period_label: str = "",
    family_order: tuple[str, ...] | None = None,
    include_details: bool = False,
) -> RecentChangesReport:
    reference_at, cutoff = period_window(period, reference)
    catalog = list(family_order or ())
    catalog_set = set(catalog)
    ordered_families = [
        family for family in catalog if families.get(family)
    ]
    ordered_families.extend(
        sorted(family for family in families if family not in catalog_set)
    )

    results: list[FamilyChangeStatus] = []
    for family in ordered_families:
        snapshots = families[family]
        results.append(
            family_change_status(
                family,
                snapshots,
                period,
                reference=reference_at,
                include_details=include_details,
            )
        )

    results.sort(key=_recent_changes_sort_key)
    label = period_label or next(
        (name for name, value in RECENT_CHANGE_PERIODS if value == period),
        str(period),
    )
    return RecentChangesReport(
        period_label=label,
        reference=reference_at,
        cutoff=cutoff,
        families=tuple(results),
    )


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
