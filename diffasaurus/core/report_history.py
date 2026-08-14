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
    "EntraDeviceId",
    "IntuneDeviceId",
    "SerialNumber",
    "PrimarySmtpAddress",
    "GroupId",
    "AccessPackageId",
)

FAMILY_PREFERRED_KEYS: dict[str, tuple[str, ...]] = {
    "Entra_Users_Activity": ("UserId", "UPN"),
    "Entra_Users_AuthenticationMethods_Hybrid": ("MicrosoftReportId", "UPN"),
    "Entra_Users_Properties": ("Id", "UPN"),
    "Intune_Android_Devices": ("EntraDeviceId", "IntuneDeviceId", "SerialNumber"),
    "Intune_iOS_Devices": ("EntraDeviceId", "IntuneDeviceId", "SerialNumber"),
    "Intune_ManagedDevices_Compliance": ("ManagedDeviceId", "AzureADDeviceId", "SerialNumber"),
    "Intune_Devices_Autopilot": ("AutopilotObjectId", "SerialNumber"),
}

FAMILY_IDENTITY_DISPLAY: dict[str, tuple[str, ...]] = {
    "Entra_Groups_Dependencies": ("DisplayName",),
    "Intune_Android_Devices": (
        "DeviceName",
        "SerialNumber",
        "EntraDeviceId",
        "IntuneDeviceId",
    ),
    "Intune_iOS_Devices": (
        "DeviceName",
        "SerialNumber",
        "EntraDeviceId",
        "IntuneDeviceId",
    ),
    "Intune_Devices_Autopilot": (
        "DisplayName",
        "SerialNumber",
        "AutopilotObjectId",
        "AzureADDeviceId",
        "ManagedDeviceId",
    ),
    "Intune_ManagedDevices_Compliance": (
        "DeviceName",
        "SerialNumber",
        "ManagedDeviceId",
        "AzureADDeviceId",
    ),
}

_DEVICE_COMPARISON_FAMILIES = frozenset(
    {
        "Intune_Android_Devices",
        "Intune_iOS_Devices",
        "Intune_ManagedDevices_Compliance",
        "Intune_Devices_Autopilot",
    }
)

COMPOSITE_KEY_DELIMITER = "\x1f"

FAMILY_COMPOSITE_KEYS: dict[str, tuple[str, ...]] = {
    "Entra_Group_User_Memberships": ("UserId", "GroupId"),
    "Entra_Access_Packages": ("AccessPackageId", "PolicyId"),
}

FAMILY_COMPOSITE_KEY_LABELS: dict[str, str] = {
    "Entra_Group_User_Memberships": "User + Group",
    "Entra_Access_Packages": "Access Package + Policy",
}

FAMILY_RELATIONSHIP_IDENTITY: dict[str, dict[str, tuple[str, ...]]] = {
    "Entra_Group_User_Memberships": {
        "user": ("UserDisplayName", "UserPrincipalName", "UserId"),
        "group": ("GroupName", "GroupMail", "GroupId"),
    },
    "Entra_Access_Packages": {
        "user": ("AccessPackageName", "AccessPackageId"),
        "group": ("PolicyName", "PolicyId"),
    },
}

AUTH_METHODS_HYBRID_FAMILY = "Entra_Users_AuthenticationMethods_Hybrid"
USER_PROPERTIES_FAMILY = "Entra_Users_Properties"
ANDROID_DEVICES_FAMILY = "Intune_Android_Devices"
ANDROID_DEVICES_REPORT_FAMILY = "Intune_Android_Devices_Report"
IOS_DEVICES_FAMILY = "Intune_iOS_Devices"
IOS_DEVICES_REPORT_FAMILY = "Intune_iOS_Devices_Report"
AUTOPILOT_DEVICES_FAMILY = "Intune_Devices_Autopilot"
MANAGED_DEVICES_FAMILY = "Intune_ManagedDevices_Compliance"
ROLE_ASSIGNMENTS_FAMILY = "Entra_Role_Assignments"
ROLE_ASSIGNMENT_STABLE_KEY_COLUMNS = ("AssignmentScheduleId", "UserId")
ROLE_ASSIGNMENT_LEGACY_KEY_COLUMNS = (
    "UserPrincipalName",
    "RoleName",
    "RoleState",
    "AssignmentSource",
    "SourceGroup",
)
ROLE_ASSIGNMENT_STABLE_KEY_LABEL = "Assignment schedule + User"
ROLE_ASSIGNMENT_LEGACY_KEY_LABEL = "Role assignment (legacy)"

RECENT_CHANGES_FAMILY_ALIASES: dict[str, str] = {
    ANDROID_DEVICES_REPORT_FAMILY: ANDROID_DEVICES_FAMILY,
    IOS_DEVICES_REPORT_FAMILY: IOS_DEVICES_FAMILY,
}

ANDROID_DEVICES_EXCLUDED_COLUMNS = frozenset(
    {
        "DaysSinceLastSync",
        "LastSyncDateTime",
    }
)

IOS_DEVICES_EXCLUDED_COLUMNS = frozenset(
    {
        "DaysSinceLastSync",
        "LastSyncDateTime",
        "FreeStorageGB",
        "ActivationLockBypassCode",
    }
)

AUTOPILOT_DEVICES_EXCLUDED_COLUMNS = frozenset(
    {
        "LastContactedDateTime",
        "AssignedUser",
        "AssignmentStatus",
        "RecommendedAction",
    }
)

MANAGED_DEVICES_EXCLUDED_COLUMNS = frozenset(
    {
        "DaysSinceLastSync",
        "LastSyncDateTime",
    }
)

MANAGED_DEVICES_USER_PRESENTATION_COLUMNS = frozenset(
    {
        "UserPrincipalName",
        "UserDisplayName",
        "EmailAddress",
    }
)

ROLE_ASSIGNMENTS_EXCLUDED_COLUMNS = frozenset(
    {
        "DisplayName",
        "UserPrincipalName",
        "Mail",
        "AccountEnabled",
        "RoleName",
        "SourceGroup",
    }
)

FAMILY_COMPARISON_EXCLUDED_COLUMNS: dict[str, frozenset[str]] = {
    AUTH_METHODS_HYBRID_FAMILY: frozenset({"LastUpdatedDateTime"}),
    USER_PROPERTIES_FAMILY: frozenset(
        {"ManagerStatus", "ManagerError", "SponsorsStatus", "SponsorsError"}
    ),
    ANDROID_DEVICES_FAMILY: ANDROID_DEVICES_EXCLUDED_COLUMNS,
    IOS_DEVICES_FAMILY: IOS_DEVICES_EXCLUDED_COLUMNS,
    AUTOPILOT_DEVICES_FAMILY: AUTOPILOT_DEVICES_EXCLUDED_COLUMNS,
    MANAGED_DEVICES_FAMILY: MANAGED_DEVICES_EXCLUDED_COLUMNS,
    ROLE_ASSIGNMENTS_FAMILY: ROLE_ASSIGNMENTS_EXCLUDED_COLUMNS,
}

AUTH_METHODS_HYBRID_COLLECTION_COLUMNS = frozenset(
    {
        "AuthenticationMethods",
        "MethodsRegistered",
        "SystemPreferredAuthenticationMethods",
        "SystemPreferredAuthenticationMethod",
    }
)

USER_PROPERTIES_COLLECTION_COLUMNS = frozenset(
    {
        "Identities",
        "BusinessPhones",
        "OtherMails",
        "ProxyAddresses",
        "IMAddresses",
        "Sponsors",
    }
)

USER_PROPERTIES_MANAGER_COLUMNS = frozenset({"ManagerDisplayName", "ManagerUPN"})
USER_PROPERTIES_SPONSOR_COLUMNS = frozenset({"Sponsors"})

_COLLECTION_VALUE_SPLIT_RE = re.compile(r"\s*;\s*")


def canonical_comparison_family(family: str | None) -> str | None:
    if not family:
        return family
    return RECENT_CHANGES_FAMILY_ALIASES.get(family, family)


def is_android_devices_family(family: str | None) -> bool:
    return canonical_comparison_family(family) == ANDROID_DEVICES_FAMILY


def is_ios_devices_family(family: str | None) -> bool:
    return canonical_comparison_family(family) == IOS_DEVICES_FAMILY


def is_inventory_device_family(family: str | None) -> bool:
    return is_android_devices_family(family) or is_ios_devices_family(family)


def is_autopilot_devices_family(family: str | None) -> bool:
    return canonical_comparison_family(family) == AUTOPILOT_DEVICES_FAMILY


def is_managed_devices_family(family: str | None) -> bool:
    return canonical_comparison_family(family) == MANAGED_DEVICES_FAMILY


def is_role_assignments_family(family: str | None) -> bool:
    return canonical_comparison_family(family) == ROLE_ASSIGNMENTS_FAMILY


def _role_assignment_stable_key_available(
    headers: list[str] | tuple[str, ...],
) -> bool:
    normalized = {header.lower(): header for header in headers}
    return (
        "assignmentscheduleid" in normalized
        and "userid" in normalized
    )


def role_assignment_key_columns_for_comparison(
    baseline_headers: list[str] | tuple[str, ...],
    latest_headers: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if _role_assignment_stable_key_available(
        baseline_headers
    ) and _role_assignment_stable_key_available(latest_headers):
        return ROLE_ASSIGNMENT_STABLE_KEY_COLUMNS
    return ROLE_ASSIGNMENT_LEGACY_KEY_COLUMNS


def role_assignment_suggested_key_label(
    headers: list[str] | tuple[str, ...],
) -> str:
    if _role_assignment_stable_key_available(headers):
        return ROLE_ASSIGNMENT_STABLE_KEY_LABEL
    return ROLE_ASSIGNMENT_LEGACY_KEY_LABEL


def _comparison_excluded_columns(family: str | None) -> frozenset[str]:
    canonical = canonical_comparison_family(family)
    return FAMILY_COMPARISON_EXCLUDED_COLUMNS.get(canonical or "", frozenset())

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
    status: Literal["changed", "unchanged", "no_data", "partial"]
    baseline: ReportSnapshot | None
    latest: ReportSnapshot | None
    key_column: str
    summary: ComparisonSummary | None
    reason: str
    policy_summary: object | None = None
    semantic_details: tuple[dict[str, str], ...] = ()
    partial_coverage: bool = False
    policy_comparison: object | None = None
    policy_baseline_descriptor: object | None = None
    policy_target_descriptor: object | None = None


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
    attention: int = 0

    @property
    def coverage(self) -> float:
        return self.observed / self.expected if self.expected else 1.0

    @property
    def status(self) -> str:
        if not self.expected:
            return "No schedule window"
        if self.attention:
            return "Attention"
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
    report_dir: Path | str | None = None,
) -> list[FamilyRunHealth]:
    from diffasaurus.core.configuration_policies.integration import (
        is_configuration_policy_family,
        policy_run_health_observation,
    )

    days = expected_business_days(reference, business_day_count, scheduled_hour)
    health: list[FamilyRunHealth] = []
    for family, snapshots in families.items():
        snapshots_by_day: dict[date, ReportSnapshot] = {}
        for snapshot in snapshots:
            captured_day = snapshot.captured_at.date()
            current = snapshots_by_day.get(captured_day)
            if current is None or snapshot.captured_at > current.captured_at:
                snapshots_by_day[captured_day] = snapshot

        raw_observations = tuple((day, snapshots_by_day.get(day)) for day in days)
        policy_mode = is_configuration_policy_family(family) and report_dir is not None
        observations: list[tuple[date, ReportSnapshot | None]] = []
        observed = 0
        attention = 0
        late = 0
        for day, snapshot in raw_observations:
            if snapshot is None:
                observations.append((day, None))
                continue
            health_snapshot = snapshot
            needs_attention = False
            if policy_mode:
                health_snapshot, needs_attention = policy_run_health_observation(
                    report_dir,
                    snapshot,
                )
            observations.append((day, health_snapshot))
            if health_snapshot is None:
                continue
            observed += 1
            if needs_attention:
                attention += 1
            if health_snapshot.captured_at.time() > time(
                min(scheduled_hour + grace_hours, 23),
                0,
            ):
                late += 1
        health.append(
            FamilyRunHealth(
                family=family,
                expected=len(days),
                observed=observed,
                missing=len(days) - observed,
                late=late,
                latest=snapshots[-1].captured_at if snapshots else None,
                days=tuple(observations),
                attention=attention,
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


def suggested_key(
    headers: list[str] | tuple[str, ...],
    family: str | None = None,
) -> str:
    if is_role_assignments_family(family):
        return role_assignment_suggested_key_label(headers)
    composite_label = composite_key_label(family, headers)
    if composite_label:
        return composite_label
    normalized = {header.lower(): header for header in headers}
    preferred = (
        FAMILY_PREFERRED_KEYS[canonical]
        if family
        and (canonical := canonical_comparison_family(family)) in FAMILY_PREFERRED_KEYS
        else PREFERRED_KEYS
    )
    for candidate in preferred:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return headers[0] if headers else ""


def composite_key_columns(
    family: str | None,
    headers: list[str] | tuple[str, ...],
) -> tuple[str, ...] | None:
    if not family:
        return None
    wanted = FAMILY_COMPOSITE_KEYS.get(family, ())
    if not wanted:
        return None
    normalized = {header.lower(): header for header in headers}
    resolved = tuple(
        normalized[column.lower()]
        for column in wanted
        if column.lower() in normalized
    )
    return resolved if len(resolved) == len(wanted) else None


def composite_key_label(
    family: str | None,
    headers: list[str] | tuple[str, ...],
) -> str:
    columns = composite_key_columns(family, headers)
    if not columns:
        return ""
    return FAMILY_COMPOSITE_KEY_LABELS.get(family or "", " + ".join(columns))


def uses_composite_key(
    family: str | None,
    headers: list[str] | tuple[str, ...],
    key_column: str,
) -> bool:
    label = composite_key_label(family, headers)
    return bool(label and key_column == label)


def comparison_key_columns(
    family: str | None,
    headers: list[str] | tuple[str, ...],
    key_column: str,
) -> tuple[str, ...]:
    composite_columns = composite_key_columns(family, headers)
    if composite_columns and uses_composite_key(family, headers, key_column):
        return composite_columns
    return (key_column,)


def _comparison_row_key(
    row: dict[str, str],
    *,
    family: str | None,
    key_column: str,
    key_columns: tuple[str, ...],
    use_composite: bool,
) -> str:
    if family == "Entra_Access_Packages":
        package_id = str(row.get("AccessPackageId", "") or "").strip()
        if not package_id:
            return ""
        policy_id = str(row.get("PolicyId", "") or "").strip()
        if policy_id:
            return f"{package_id}{COMPOSITE_KEY_DELIMITER}{policy_id}"
        return package_id
    if is_android_devices_family(family):
        for column in ("EntraDeviceId", "IntuneDeviceId", "SerialNumber"):
            value = str(row.get(column, "") or "").strip()
            if value:
                return value
        return ""
    if is_ios_devices_family(family):
        for column in ("EntraDeviceId", "IntuneDeviceId", "SerialNumber"):
            value = str(row.get(column, "") or "").strip()
            if value:
                return value
        udid = str(row.get("UDID", "") or "").strip()
        if udid:
            return udid
        return ""
    if is_autopilot_devices_family(family):
        for column in ("AutopilotObjectId", "SerialNumber"):
            value = str(row.get(column, "") or "").strip()
            if value:
                return value
        return ""
    if is_managed_devices_family(family):
        for column in ("ManagedDeviceId", "AzureADDeviceId", "SerialNumber"):
            value = str(row.get(column, "") or "").strip()
            if value:
                return value
        return ""
    if is_role_assignments_family(family):
        parts = [str(row.get(column, "") or "").strip() for column in key_columns]
        if key_columns == ROLE_ASSIGNMENT_LEGACY_KEY_COLUMNS:
            if not all(parts[:-1]):
                return ""
            return COMPOSITE_KEY_DELIMITER.join(parts)
        if all(parts):
            return COMPOSITE_KEY_DELIMITER.join(parts)
        return ""
    if use_composite:
        parts = [str(row.get(column, "") or "").strip() for column in key_columns]
        return COMPOSITE_KEY_DELIMITER.join(parts) if all(parts) else ""
    return str(row.get(key_column, "") or "").strip()


def identity_display_column(
    family: str | None,
    headers: list[str] | tuple[str, ...],
) -> str:
    if not family:
        return ""
    candidates = FAMILY_IDENTITY_DISPLAY.get(
        canonical_comparison_family(family) or family or "",
        (),
    )
    normalized = {header.lower(): header for header in headers}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return ""


def detail_identity(detail: dict[str, str]) -> str:
    return detail.get("identity") or detail.get("key", "")


def comparison_summary_unit(family: str | None) -> str:
    if family == "Entra_Group_User_Memberships":
        return "memberships"
    if is_role_assignments_family(family):
        return "assignments"
    if family in {
        "Entra_Users_Activity",
        AUTH_METHODS_HYBRID_FAMILY,
        USER_PROPERTIES_FAMILY,
    }:
        return "users"
    if family in _DEVICE_COMPARISON_FAMILIES or is_inventory_device_family(family):
        return "devices"
    return "rows"


def _graph_status_code(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _manager_coverage_known(row: dict[str, str]) -> bool:
    status = _graph_status_code(row.get("ManagerStatus", ""))
    if status is None:
        return False
    if 200 <= status < 300:
        return True
    return status == 404


def _sponsors_coverage_known(row: dict[str, str]) -> bool:
    status = _graph_status_code(row.get("SponsorsStatus", ""))
    return status is not None and 200 <= status < 300


def _should_skip_column_comparison(
    column: str,
    family: str | None,
    before_row: dict[str, str],
    after_row: dict[str, str],
) -> bool:
    if is_managed_devices_family(family):
        if column in MANAGED_DEVICES_USER_PRESENTATION_COLUMNS:
            before_user_id = str(before_row.get("UserId", "") or "").strip()
            after_user_id = str(after_row.get("UserId", "") or "").strip()
            if before_user_id or after_user_id:
                return True
        return False
    if family != USER_PROPERTIES_FAMILY:
        return False
    if column in USER_PROPERTIES_MANAGER_COLUMNS:
        return not (
            _manager_coverage_known(before_row) and _manager_coverage_known(after_row)
        )
    if column in USER_PROPERTIES_SPONSOR_COLUMNS:
        return not (
            _sponsors_coverage_known(before_row) and _sponsors_coverage_known(after_row)
        )
    return False


def _parse_collection_tokens(value: str) -> frozenset[str]:
    if not value or not value.strip():
        return frozenset()
    return frozenset(
        token.strip()
        for token in _COLLECTION_VALUE_SPLIT_RE.split(value.strip())
        if token.strip()
    )


def _column_values_equal(
    before_value: str,
    after_value: str,
    *,
    column: str,
    family: str | None,
) -> bool:
    if family == AUTH_METHODS_HYBRID_FAMILY and column in AUTH_METHODS_HYBRID_COLLECTION_COLUMNS:
        return _parse_collection_tokens(before_value) == _parse_collection_tokens(after_value)
    if family == USER_PROPERTIES_FAMILY and column in USER_PROPERTIES_COLLECTION_COLUMNS:
        return _parse_collection_tokens(before_value) == _parse_collection_tokens(after_value)
    return before_value == after_value


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


def _pick_identity_label(
    primary_row: dict[str, str],
    fallback_row: dict[str, str],
    columns: tuple[str, ...],
) -> str:
    for column in columns:
        value = str(primary_row.get(column, "") or "").strip()
        if value:
            return value
    for column in columns:
        value = str(fallback_row.get(column, "") or "").strip()
        if value:
            return value
    return ""


def _relationship_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
    spec: dict[str, tuple[str, ...]],
    composite_columns: tuple[str, ...] = (),
) -> str:
    before_row = before_map.get(key, {})
    after_row = after_map.get(key, {})
    if change == "Added":
        primary_row, fallback_row = after_row, after_row
    elif change == "Removed":
        primary_row, fallback_row = before_row, before_row
    else:
        primary_row, fallback_row = after_row, before_row
    user = _pick_identity_label(primary_row, fallback_row, spec["user"])
    group = _pick_identity_label(primary_row, fallback_row, spec["group"])
    if not user and composite_columns:
        user = str(
            after_row.get(composite_columns[0], "")
            or before_row.get(composite_columns[0], "")
            or ""
        ).strip()
    if not group and len(composite_columns) > 1:
        group = str(
            after_row.get(composite_columns[1], "")
            or before_row.get(composite_columns[1], "")
            or ""
        ).strip()
    if user and group:
        return f"{user} → {group}"
    return user or group or key


def _display_name_upn_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
) -> str:
    if change == "Added":
        primary_row, fallback_row = after_map.get(key, {}), after_map.get(key, {})
    elif change == "Removed":
        primary_row, fallback_row = before_map.get(key, {}), before_map.get(key, {})
    else:
        primary_row, fallback_row = after_map.get(key, {}), before_map.get(key, {})
    display_name = _pick_identity_label(primary_row, fallback_row, ("DisplayName",))
    upn = _pick_identity_label(primary_row, fallback_row, ("UPN",))
    if display_name and upn:
        return f"{display_name} · {upn}"
    if upn:
        return upn
    if display_name:
        return display_name
    return key


def _android_device_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
) -> str:
    if change == "Added":
        primary_row, fallback_row = after_map.get(key, {}), after_map.get(key, {})
    elif change == "Removed":
        primary_row, fallback_row = before_map.get(key, {}), before_map.get(key, {})
    else:
        primary_row, fallback_row = after_map.get(key, {}), before_map.get(key, {})
    device_name = _pick_identity_label(primary_row, fallback_row, ("DeviceName",))
    serial = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
    if device_name and serial:
        return f"{device_name} · {serial}"
    if device_name:
        return device_name
    if serial:
        return serial
    entra_id = _pick_identity_label(primary_row, fallback_row, ("EntraDeviceId",))
    if entra_id:
        return entra_id
    intune_id = _pick_identity_label(primary_row, fallback_row, ("IntuneDeviceId",))
    if intune_id:
        return intune_id
    return key


def _ios_device_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
) -> str:
    if change == "Added":
        primary_row, fallback_row = after_map.get(key, {}), after_map.get(key, {})
    elif change == "Removed":
        primary_row, fallback_row = before_map.get(key, {}), before_map.get(key, {})
    else:
        primary_row, fallback_row = after_map.get(key, {}), before_map.get(key, {})
    device_name = _pick_identity_label(primary_row, fallback_row, ("DeviceName",))
    serial = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
    if device_name and serial:
        return f"{device_name} · {serial}"
    if device_name:
        return device_name
    if serial:
        return serial
    entra_id = _pick_identity_label(primary_row, fallback_row, ("EntraDeviceId",))
    if entra_id:
        return entra_id
    intune_id = _pick_identity_label(primary_row, fallback_row, ("IntuneDeviceId",))
    if intune_id:
        return intune_id
    udid = _pick_identity_label(primary_row, fallback_row, ("UDID",))
    if udid:
        return udid
    return key


def _managed_device_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
) -> str:
    if change == "Added":
        primary_row, fallback_row = after_map.get(key, {}), after_map.get(key, {})
    elif change == "Removed":
        primary_row, fallback_row = before_map.get(key, {}), before_map.get(key, {})
    else:
        primary_row, fallback_row = after_map.get(key, {}), before_map.get(key, {})
    device_name = _pick_identity_label(primary_row, fallback_row, ("DeviceName",))
    serial = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
    if device_name and serial:
        return f"{device_name} · {serial}"
    if device_name:
        return device_name
    if serial:
        return serial
    azure_ad_device_id = _pick_identity_label(primary_row, fallback_row, ("AzureADDeviceId",))
    if azure_ad_device_id:
        return azure_ad_device_id
    managed_device_id = _pick_identity_label(primary_row, fallback_row, ("ManagedDeviceId",))
    if managed_device_id:
        return managed_device_id
    return key


def _autopilot_device_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
) -> str:
    if change == "Added":
        primary_row, fallback_row = after_map.get(key, {}), after_map.get(key, {})
    elif change == "Removed":
        primary_row, fallback_row = before_map.get(key, {}), before_map.get(key, {})
    else:
        primary_row, fallback_row = after_map.get(key, {}), before_map.get(key, {})
    display_name = _pick_identity_label(primary_row, fallback_row, ("DisplayName",))
    serial = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
    if display_name and serial:
        return f"{display_name} · {serial}"
    if serial:
        return serial
    if display_name:
        return display_name
    autopilot_id = _pick_identity_label(primary_row, fallback_row, ("AutopilotObjectId",))
    if autopilot_id:
        return autopilot_id
    return key


def _role_assignment_path_label(row: dict[str, str]) -> str:
    role_state = str(row.get("RoleState", "") or "").strip()
    assignment_source = str(row.get("AssignmentSource", "") or "").strip()
    source_group = str(row.get("SourceGroup", "") or "").strip()
    if assignment_source == "Group" and source_group:
        return f"{role_state} · via {source_group}"
    if assignment_source:
        return f"{role_state} · {assignment_source}"
    return role_state


def _role_assignment_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
) -> str:
    if change == "Added":
        primary_row, fallback_row = after_map.get(key, {}), after_map.get(key, {})
    elif change == "Removed":
        primary_row, fallback_row = before_map.get(key, {}), before_map.get(key, {})
    else:
        primary_row, fallback_row = after_map.get(key, {}), before_map.get(key, {})
    display_name = _pick_identity_label(primary_row, fallback_row, ("DisplayName",))
    upn = _pick_identity_label(primary_row, fallback_row, ("UserPrincipalName",))
    user_id = _pick_identity_label(primary_row, fallback_row, ("UserId",))
    role_name = _pick_identity_label(primary_row, fallback_row, ("RoleName",))
    role_definition_id = _pick_identity_label(
        primary_row,
        fallback_row,
        ("RoleDefinitionId",),
    )
    user_part = display_name or upn or user_id
    role_part = role_name or role_definition_id
    if user_part and role_part:
        return f"{user_part} → {role_part}"
    if user_part:
        return user_part
    if role_part:
        return role_part
    return key.split(COMPOSITE_KEY_DELIMITER, 1)[0]


def _user_activity_identity_label(
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
) -> str:
    return _display_name_upn_identity_label(key, change, before_map, after_map)


def _attach_detail_identity(
    detail: dict[str, str],
    key: str,
    change: str,
    before_map: dict[str, dict[str, str]],
    after_map: dict[str, dict[str, str]],
    family: str | None,
    composite_columns: tuple[str, ...],
    display_column: str,
) -> None:
    relationship_spec = FAMILY_RELATIONSHIP_IDENTITY.get(family or "")
    if relationship_spec:
        detail["identity"] = _relationship_identity_label(
            key,
            change,
            before_map,
            after_map,
            relationship_spec,
            composite_columns,
        )
        if composite_columns:
            parts = key.split(COMPOSITE_KEY_DELIMITER)
            if family == "Entra_Group_User_Memberships" and len(parts) == len(composite_columns):
                detail["user_id"] = parts[0]
                detail["group_id"] = parts[1]
        if family == "Entra_Access_Packages":
            if COMPOSITE_KEY_DELIMITER in key:
                package_id, policy_id = key.split(COMPOSITE_KEY_DELIMITER, 1)
            else:
                package_id, policy_id = key, ""
            detail["access_package_id"] = package_id
            if policy_id:
                detail["policy_id"] = policy_id
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        for field in ("UserPrincipalName", "UserDisplayName", "GroupName"):
            value = _pick_identity_label(primary_row, fallback_row, (field,))
            if value:
                detail[field] = value
        return
    if family == "Entra_Users_Activity":
        detail["identity"] = _user_activity_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        user_id = _pick_identity_label(primary_row, fallback_row, ("UserId",))
        if not user_id and key and "@" not in key:
            user_id = key
        if user_id:
            detail["user_id"] = user_id
        upn = _pick_identity_label(primary_row, fallback_row, ("UPN",))
        if upn:
            detail["UPN"] = upn
        return
    if family == AUTH_METHODS_HYBRID_FAMILY:
        detail["identity"] = _display_name_upn_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        microsoft_report_id = _pick_identity_label(
            primary_row,
            fallback_row,
            ("MicrosoftReportId",),
        )
        if not microsoft_report_id and key and "@" not in key:
            microsoft_report_id = key
        if microsoft_report_id:
            detail["microsoft_report_id"] = microsoft_report_id
        upn = _pick_identity_label(primary_row, fallback_row, ("UPN",))
        if upn:
            detail["UPN"] = upn
        return
    if family == USER_PROPERTIES_FAMILY:
        detail["identity"] = _display_name_upn_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        entra_id = _pick_identity_label(primary_row, fallback_row, ("Id",))
        if not entra_id and key and "@" not in key:
            entra_id = key
        if entra_id:
            detail["user_id"] = entra_id
        upn = _pick_identity_label(primary_row, fallback_row, ("UPN",))
        if upn:
            detail["UPN"] = upn
        return
    if is_android_devices_family(family):
        detail["identity"] = _android_device_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        device_name = _pick_identity_label(primary_row, fallback_row, ("DeviceName",))
        if device_name:
            detail["device_name"] = device_name
        serial_number = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
        if serial_number:
            detail["serial_number"] = serial_number
        entra_device_id = _pick_identity_label(primary_row, fallback_row, ("EntraDeviceId",))
        if entra_device_id:
            detail["entra_device_id"] = entra_device_id
        intune_device_id = _pick_identity_label(primary_row, fallback_row, ("IntuneDeviceId",))
        if intune_device_id:
            detail["intune_device_id"] = intune_device_id
        user_principal_name = _pick_identity_label(
            primary_row,
            fallback_row,
            ("UserPrincipalName",),
        )
        if user_principal_name:
            detail["UserPrincipalName"] = user_principal_name
        return
    if is_ios_devices_family(family):
        detail["identity"] = _ios_device_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        device_name = _pick_identity_label(primary_row, fallback_row, ("DeviceName",))
        if device_name:
            detail["device_name"] = device_name
        serial_number = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
        if serial_number:
            detail["serial_number"] = serial_number
        entra_device_id = _pick_identity_label(primary_row, fallback_row, ("EntraDeviceId",))
        if entra_device_id:
            detail["entra_device_id"] = entra_device_id
        intune_device_id = _pick_identity_label(primary_row, fallback_row, ("IntuneDeviceId",))
        if intune_device_id:
            detail["intune_device_id"] = intune_device_id
        udid = _pick_identity_label(primary_row, fallback_row, ("UDID",))
        if udid:
            detail["udid"] = udid
        user_principal_name = _pick_identity_label(
            primary_row,
            fallback_row,
            ("UserPrincipalName",),
        )
        if user_principal_name:
            detail["UserPrincipalName"] = user_principal_name
        return
    if is_managed_devices_family(family):
        detail["identity"] = _managed_device_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        device_name = _pick_identity_label(primary_row, fallback_row, ("DeviceName",))
        if device_name:
            detail["device_name"] = device_name
        serial_number = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
        if serial_number:
            detail["serial_number"] = serial_number
        managed_device_id = _pick_identity_label(primary_row, fallback_row, ("ManagedDeviceId",))
        if managed_device_id:
            detail["managed_device_id"] = managed_device_id
        azure_ad_device_id = _pick_identity_label(primary_row, fallback_row, ("AzureADDeviceId",))
        if azure_ad_device_id:
            detail["azure_ad_device_id"] = azure_ad_device_id
        user_display_name = _pick_identity_label(primary_row, fallback_row, ("UserDisplayName",))
        if user_display_name:
            detail["user_display_name"] = user_display_name
        user_principal_name = _pick_identity_label(
            primary_row,
            fallback_row,
            ("UserPrincipalName",),
        )
        if user_principal_name:
            detail["UserPrincipalName"] = user_principal_name
        user_id = _pick_identity_label(primary_row, fallback_row, ("UserId",))
        if user_id:
            detail["user_id"] = user_id
        return
    if is_autopilot_devices_family(family):
        detail["identity"] = _autopilot_device_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        display_name = _pick_identity_label(primary_row, fallback_row, ("DisplayName",))
        if display_name:
            detail["display_name"] = display_name
        serial_number = _pick_identity_label(primary_row, fallback_row, ("SerialNumber",))
        if serial_number:
            detail["serial_number"] = serial_number
        autopilot_object_id = _pick_identity_label(
            primary_row,
            fallback_row,
            ("AutopilotObjectId",),
        )
        if autopilot_object_id:
            detail["autopilot_object_id"] = autopilot_object_id
        azure_ad_device_id = _pick_identity_label(
            primary_row,
            fallback_row,
            ("AzureADDeviceId",),
        )
        if azure_ad_device_id:
            detail["azure_ad_device_id"] = azure_ad_device_id
        managed_device_id = _pick_identity_label(
            primary_row,
            fallback_row,
            ("ManagedDeviceId",),
        )
        if managed_device_id:
            detail["managed_device_id"] = managed_device_id
        user_principal_name = _pick_identity_label(
            primary_row,
            fallback_row,
            ("UserPrincipalName",),
        )
        if user_principal_name:
            detail["UserPrincipalName"] = user_principal_name
        return
    if is_role_assignments_family(family):
        detail["identity"] = _role_assignment_identity_label(
            key,
            change,
            before_map,
            after_map,
        )
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        for field, detail_key in (
            ("DisplayName", "display_name"),
            ("UserPrincipalName", "UPN"),
            ("UserId", "user_id"),
            ("RoleName", "role_name"),
            ("RoleDefinitionId", "role_definition_id"),
            ("RoleState", "role_state"),
            ("AssignmentSource", "assignment_source"),
            ("SourceGroup", "source_group"),
            ("AssignmentScheduleId", "assignment_schedule_id"),
            ("SourcePrincipalId", "source_principal_id"),
            ("SourceGroupId", "source_group_id"),
            ("DirectoryScopeId", "directory_scope_id"),
            ("AppScopeId", "app_scope_id"),
        ):
            value = _pick_identity_label(primary_row, fallback_row, (field,))
            if value:
                detail[detail_key] = value
        return
    identity_columns = FAMILY_IDENTITY_DISPLAY.get(canonical_comparison_family(family) or family or "", ())
    if identity_columns:
        before_row = before_map.get(key, {})
        after_row = after_map.get(key, {})
        if change == "Added":
            primary_row, fallback_row = after_row, after_row
        elif change == "Removed":
            primary_row, fallback_row = before_row, before_row
        else:
            primary_row, fallback_row = after_row, before_row
        detail["identity"] = (
            _pick_identity_label(primary_row, fallback_row, identity_columns) or key
        )
    elif display_column:
        detail["identity"] = _identity_label(
            key,
            change,
            before_map,
            after_map,
            display_column,
        )


def _compare_snapshots(
    baseline: ReportSnapshot,
    latest: ReportSnapshot,
    key_column: str,
    include_details: bool,
    family: str | None = None,
) -> ComparisonSummary:
    common = common_headers(baseline, latest)
    if is_role_assignments_family(family):
        key_columns = role_assignment_key_columns_for_comparison(
            baseline.headers,
            latest.headers,
        )
        use_composite = True
        composite_columns = key_columns
        if key_column not in {
            ROLE_ASSIGNMENT_STABLE_KEY_LABEL,
            ROLE_ASSIGNMENT_LEGACY_KEY_LABEL,
        }:
            raise ValueError("Choose a role-assignment key shared by both reports.")
    else:
        composite_columns = composite_key_columns(family, common)
        use_composite = bool(
            composite_columns
            and uses_composite_key(family, common, key_column)
        )
        if not use_composite and (not key_column or key_column not in common):
            raise ValueError("Choose a key column shared by both reports.")
        key_columns = comparison_key_columns(family, common, key_column)

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
    if not is_role_assignments_family(family):
        key_columns = comparison_key_columns(family, common, key_column)

    def keyed(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        result = {}
        for row in rows:
            key = _comparison_row_key(
                row,
                family=family,
                key_column=key_column,
                key_columns=key_columns,
                use_composite=use_composite,
            )
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
    skip_columns = set(key_columns)
    if is_android_devices_family(family):
        skip_columns.update(("EntraDeviceId", "IntuneDeviceId", "SerialNumber"))
    if is_ios_devices_family(family):
        skip_columns.update(("EntraDeviceId", "IntuneDeviceId", "SerialNumber", "UDID"))
    if is_managed_devices_family(family):
        skip_columns.update(("ManagedDeviceId", "AzureADDeviceId", "SerialNumber"))
    if is_autopilot_devices_family(family):
        skip_columns.update(("AutopilotObjectId", "SerialNumber"))
    excluded_columns = _comparison_excluded_columns(family)
    added_after = (
        "New assignment" if is_role_assignments_family(family) else "New row"
    )
    removed_before = (
        "Existing assignment" if is_role_assignments_family(family) else "Existing row"
    )

    if include_details:
        for key in added_keys:
            detail = {
                "change": "Added",
                "key": key,
                "column": "",
                "before": "",
                "after": added_after,
            }
            _attach_detail_identity(
                detail,
                key,
                "Added",
                before_map,
                after_map,
                family,
                composite_columns or (),
                display_column,
            )
            if is_role_assignments_family(family):
                detail["after"] = _role_assignment_path_label(after_map.get(key, {}))
            details.append(detail)
        for key in removed_keys:
            detail = {
                "change": "Removed",
                "key": key,
                "column": "",
                "before": removed_before,
                "after": "",
            }
            _attach_detail_identity(
                detail,
                key,
                "Removed",
                before_map,
                after_map,
                family,
                composite_columns or (),
                display_column,
            )
            if is_role_assignments_family(family):
                detail["before"] = _role_assignment_path_label(before_map.get(key, {}))
            details.append(detail)

    changed_rows = 0
    for key in shared_keys:
        row_changed = False
        for column in common:
            if column in skip_columns or column in excluded_columns:
                continue
            before_row = before_map[key]
            after_row = after_map[key]
            if _should_skip_column_comparison(column, family, before_row, after_row):
                continue
            before_value = str(before_row.get(column, "") or "").strip()
            after_value = str(after_row.get(column, "") or "").strip()
            if not _column_values_equal(
                before_value,
                after_value,
                column=column,
                family=family,
            ):
                row_changed = True
                if include_details:
                    detail = {
                        "change": "Changed",
                        "key": key,
                        "column": column,
                        "before": before_value,
                        "after": after_value,
                    }
                    _attach_detail_identity(
                        detail,
                        key,
                        "Changed",
                        before_map,
                        after_map,
                        family,
                        composite_columns or (),
                        display_column,
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
    from diffasaurus.core.configuration_policies.integration import guard_generic_csv_comparison

    guard_generic_csv_comparison(family or baseline.family)
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
    family: str | None = None,
) -> ComparisonSummary:
    from diffasaurus.core.configuration_policies.integration import guard_generic_csv_comparison

    guard_generic_csv_comparison(family or baseline.family)
    return _compare_snapshots(
        baseline,
        latest,
        key_column,
        include_details=False,
        family=family,
    )


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
    report_dir: Path | str | None = None,
) -> FamilyChangeStatus:
    from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
    from diffasaurus.core.configuration_policies.integration import (
        configuration_policy_family_change_status,
    )

    if family == CONFIGURATION_POLICY_FAMILY and report_dir is not None:
        return configuration_policy_family_change_status(
            report_dir,
            snapshots,
            period,
            reference,
            include_details=include_details,
        )

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
    key_column = suggested_key(headers, family)
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
            summary = compare_snapshot_counts(baseline, latest, key_column, family)
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
    status_rank = {"changed": 0, "partial": 1, "unchanged": 2, "no_data": 3}
    return status_rank.get(item.status, 4), item.family.lower()


def aggregate_recent_changes(
    families: dict[str, list[ReportSnapshot]],
    period: timedelta,
    reference: datetime | None = None,
    period_label: str = "",
    family_order: tuple[str, ...] | None = None,
    include_details: bool = False,
    report_dir: Path | str | None = None,
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
                report_dir=report_dir,
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
        key = suggested_key(headers, baseline.family)
        if not key:
            continue
        try:
            summary = compare_snapshot_counts(baseline, latest, key, baseline.family)
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
