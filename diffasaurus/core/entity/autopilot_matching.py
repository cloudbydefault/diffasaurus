from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from diffasaurus.core.entity.pit_enrichment import (
    AUTOPILOT_FAMILY,
    AutopilotKeyMatch,
    AutopilotMatchStatus,
    EnrichedManagedDevice,
    RelatedAutopilotState,
    RelatedManagedDevice,
    UserManagedDevicesEnrichment,
)
from diffasaurus.core.entity.pit_presentation import (
    ProvenanceObservation,
    SourceProvenance,
    single_provenance,
)
from diffasaurus.core.entity.types import FamilyCoverage, SourcedProperty

_NULL_LIKE = frozenset({"", "null", "none", "n/a", "na", "unknown", "unassigned"})
_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

AutopilotKeyKind = Literal["azure_ad_device_id", "managed_device_id", "serial_number"]
KeyResolutionStatus = Literal["unique", "ambiguous", "absent", "invalid"]

KEY_KIND_AAD = "azure_ad_device_id"
KEY_KIND_MANAGED = "managed_device_id"
KEY_KIND_SERIAL = "serial_number"

MATCH_KEY_COLUMNS: tuple[tuple[str, str], ...] = (
    (KEY_KIND_AAD, "AzureADDeviceId"),
    (KEY_KIND_MANAGED, "ManagedDeviceId"),
    (KEY_KIND_SERIAL, "SerialNumber"),
)


def normalize_guid(value: str) -> str:
    """Trim and casefold GUID-like identifiers. Returns empty when invalid."""
    text = value.strip()
    if not text:
        return ""
    lowered = text.casefold()
    if lowered in _NULL_LIKE:
        return ""
    if not _GUID_RE.match(lowered):
        return ""
    return lowered


def normalize_serial(value: str) -> str:
    """Trim, collapse internal whitespace, casefold serial numbers."""
    text = value.strip()
    if not text:
        return ""
    lowered = text.casefold()
    if lowered in _NULL_LIKE:
        return ""
    collapsed = re.sub(r"\s+", " ", lowered)
    return collapsed


def property_value(properties: tuple[SourcedProperty, ...], name: str) -> str:
    for prop in properties:
        if prop.name == name:
            return prop.value
    return ""


def operating_system_value(properties: tuple[SourcedProperty, ...]) -> str:
    return property_value(properties, "OperatingSystem")


def is_windows_device(properties: tuple[SourcedProperty, ...]) -> bool:
    os_value = operating_system_value(properties).strip().casefold()
    return os_value.startswith("windows")


def is_non_windows_autopilot_applicable(properties: tuple[SourcedProperty, ...]) -> bool:
    """macOS and iOS managed devices never participate in Autopilot matching."""
    os_value = operating_system_value(properties).strip().casefold()
    if not os_value:
        return False
    return (
        os_value.startswith("macos")
        or os_value.startswith("mac os")
        or os_value == "mac"
        or os_value.startswith("ios")
        or os_value.startswith("ipados")
    )


def windows_autopilot_inapplicable_category(
    properties: tuple[SourcedProperty, ...],
) -> str | None:
    """
    Windows devices excluded from Autopilot matching by confirmed managed-device Model values.

    Intune exports explicit categories that do not appear in Autopilot device identity reports.
    """
    model = property_value(properties, "Model").strip().casefold()
    if not model:
        return None
    if "cloud pc" in model:
        return "Cloud PC device category (Model indicates Windows 365/Cloud PC)"
    if model == "virtual machine":
        return "Virtual machine device category"
    return None


@dataclass(frozen=True)
class AutopilotSnapshotRow:
    row_index: int
    properties: tuple[SourcedProperty, ...]


@dataclass(frozen=True)
class AutopilotSnapshotIndex:
    rows: tuple[AutopilotSnapshotRow, ...]
    by_aad: dict[str, tuple[int, ...]]
    by_managed_id: dict[str, tuple[int, ...]]
    by_serial: dict[str, tuple[int, ...]]


def build_autopilot_snapshot_index(
    rows: list[tuple[SourcedProperty, ...]],
) -> AutopilotSnapshotIndex:
    indexed_rows: list[AutopilotSnapshotRow] = []
    by_aad: dict[str, list[int]] = {}
    by_managed: dict[str, list[int]] = {}
    by_serial: dict[str, list[int]] = {}

    for row_index, properties in enumerate(rows):
        indexed_rows.append(AutopilotSnapshotRow(row_index=row_index, properties=properties))
        aad = normalize_guid(property_value(properties, "AzureADDeviceId"))
        if aad:
            by_aad.setdefault(aad, []).append(row_index)
        managed = normalize_guid(property_value(properties, "ManagedDeviceId"))
        if managed:
            by_managed.setdefault(managed, []).append(row_index)
        serial = normalize_serial(property_value(properties, "SerialNumber"))
        if serial:
            by_serial.setdefault(serial, []).append(row_index)

    return AutopilotSnapshotIndex(
        rows=tuple(indexed_rows),
        by_aad={key: tuple(values) for key, values in by_aad.items()},
        by_managed_id={key: tuple(values) for key, values in by_managed.items()},
        by_serial={key: tuple(values) for key, values in by_serial.items()},
    )


def _normalize_for_kind(kind: AutopilotKeyKind, raw_value: str) -> str:
    if kind in (KEY_KIND_AAD, KEY_KIND_MANAGED):
        return normalize_guid(raw_value)
    return normalize_serial(raw_value)


def _index_for_kind(index: AutopilotSnapshotIndex, kind: AutopilotKeyKind) -> dict[str, tuple[int, ...]]:
    if kind == KEY_KIND_AAD:
        return index.by_aad
    if kind == KEY_KIND_MANAGED:
        return index.by_managed_id
    return index.by_serial


def resolve_key_match(
    kind: AutopilotKeyKind,
    raw_value: str,
    index: AutopilotSnapshotIndex,
) -> AutopilotKeyMatch:
    normalized = _normalize_for_kind(kind, raw_value)
    if not raw_value.strip():
        return AutopilotKeyMatch(
            key_kind=kind,
            raw_value=raw_value,
            normalized_value="",
            resolution_status="absent",
            matched_row_index=None,
            candidate_row_indices=frozenset(),
        )
    if not normalized:
        return AutopilotKeyMatch(
            key_kind=kind,
            raw_value=raw_value,
            normalized_value="",
            resolution_status="invalid",
            matched_row_index=None,
            candidate_row_indices=frozenset(),
        )
    candidates = _index_for_kind(index, kind).get(normalized, ())
    if not candidates:
        return AutopilotKeyMatch(
            key_kind=kind,
            raw_value=raw_value,
            normalized_value=normalized,
            resolution_status="absent",
            matched_row_index=None,
            candidate_row_indices=frozenset(),
        )
    if len(candidates) == 1:
        return AutopilotKeyMatch(
            key_kind=kind,
            raw_value=raw_value,
            normalized_value=normalized,
            resolution_status="unique",
            matched_row_index=candidates[0],
            candidate_row_indices=frozenset(candidates),
        )
    return AutopilotKeyMatch(
        key_kind=kind,
        raw_value=raw_value,
        normalized_value=normalized,
        resolution_status="ambiguous",
        matched_row_index=None,
        candidate_row_indices=frozenset(candidates),
    )


def build_key_matches(
    device: RelatedManagedDevice,
    index: AutopilotSnapshotIndex,
) -> tuple[AutopilotKeyMatch, ...]:
    matches: list[AutopilotKeyMatch] = []
    for kind, column in MATCH_KEY_COLUMNS:
        raw = property_value(device.properties, column)
        matches.append(resolve_key_match(kind, raw, index))
    return tuple(matches)


def _key_match(
    matches: tuple[AutopilotKeyMatch, ...],
    kind: AutopilotKeyKind,
) -> AutopilotKeyMatch | None:
    for item in matches:
        if item.key_kind == kind:
            return item
    return None


def resolve_cross_key_match(
    key_matches: tuple[AutopilotKeyMatch, ...],
) -> tuple[AutopilotMatchStatus, int | None, str]:
    """
    Deterministic cross-key Autopilot resolution.

    1. Unique keys pointing to different rows -> ambiguous.
    2. Unique immutable ID with ambiguous serial containing that row -> matched + diagnostic.
    3. Unique immutable ID with ambiguous serial excluding that row -> ambiguous.
    4. Only ambiguous keys -> ambiguous (duplicate serial/AAD).
    5. Single unique key -> matched.
    6. All keys absent/invalid -> no_match_with_coverage.
    """
    unique = [item for item in key_matches if item.resolution_status == "unique"]
    ambiguous = [item for item in key_matches if item.resolution_status == "ambiguous"]

    unique_rows = {item.matched_row_index for item in unique}
    if len(unique_rows) > 1:
        return (
            "ambiguous",
            None,
            "Cross-key disagreement: unique keys identify different Autopilot rows",
        )

    aad = _key_match(key_matches, KEY_KIND_AAD)
    managed = _key_match(key_matches, KEY_KIND_MANAGED)
    serial = _key_match(key_matches, KEY_KIND_SERIAL)

    if unique:
        chosen_row = next(iter(unique_rows))
        if ambiguous:
            serial_amb = _key_match(key_matches, KEY_KIND_SERIAL)
            if serial_amb and serial_amb.resolution_status == "ambiguous":
                if chosen_row in serial_amb.candidate_row_indices:
                    return (
                        "matched",
                        chosen_row,
                        "SerialNumber is non-unique; immutable key match selected",
                    )
                return (
                    "ambiguous",
                    None,
                    "SerialNumber candidates do not include immutable-key match",
                )
            if any(item.resolution_status == "ambiguous" for item in ambiguous):
                return (
                    "ambiguous",
                    None,
                    "Ambiguous key candidates conflict with unique immutable match",
                )
        return ("matched", chosen_row, "Unique key match")

    if ambiguous:
        if serial and serial.resolution_status == "ambiguous" and len(ambiguous) == 1:
            return ("ambiguous", None, "Duplicate SerialNumber candidates")
        if aad and aad.resolution_status == "ambiguous":
            return ("ambiguous", None, "Duplicate AzureADDeviceId candidates")
        if managed and managed.resolution_status == "ambiguous":
            return ("ambiguous", None, "Duplicate ManagedDeviceId candidates")
        return ("ambiguous", None, "Ambiguous Autopilot key candidates")

    return (
        "no_match_with_coverage",
        None,
        "No Autopilot row matches available keys",
    )


def match_device_to_autopilot(
    device: RelatedManagedDevice,
    index: AutopilotSnapshotIndex,
    *,
    autopilot_snapshot_exists: bool,
    target: datetime,
    snapshot_at: datetime,
    autopilot_provenance: SourceProvenance | None,
) -> RelatedAutopilotState:
    if is_non_windows_autopilot_applicable(device.properties):
        return RelatedAutopilotState(
            status="not_applicable",
            properties=(),
            provenance=None,
            key_matches=(),
            matched_row_index=None,
            conflict_diagnostic="Non-Windows device",
        )

    if not is_windows_device(device.properties):
        # Unknown OS — do not treat as missing Autopilot inventory.
        return RelatedAutopilotState(
            status="not_applicable",
            properties=(),
            provenance=None,
            key_matches=(),
            matched_row_index=None,
            conflict_diagnostic="Operating system not Windows",
        )

    category_exclusion = windows_autopilot_inapplicable_category(device.properties)
    if category_exclusion:
        return RelatedAutopilotState(
            status="not_applicable",
            properties=(),
            provenance=None,
            key_matches=(),
            matched_row_index=None,
            conflict_diagnostic=category_exclusion,
        )

    if not autopilot_snapshot_exists:
        return RelatedAutopilotState(
            status="no_coverage",
            properties=(),
            provenance=None,
            key_matches=(),
            matched_row_index=None,
            conflict_diagnostic="No Autopilot snapshot at or before target",
        )

    key_matches = build_key_matches(device, index)
    status, row_index, diagnostic = resolve_cross_key_match(key_matches)

    if status == "matched" and row_index is not None:
        row = index.rows[row_index]
        return RelatedAutopilotState(
            status="matched",
            properties=row.properties,
            provenance=autopilot_provenance,
            key_matches=key_matches,
            matched_row_index=row_index,
            conflict_diagnostic=diagnostic,
        )

    if status == "ambiguous":
        return RelatedAutopilotState(
            status="ambiguous",
            properties=(),
            provenance=autopilot_provenance,
            key_matches=key_matches,
            matched_row_index=None,
            conflict_diagnostic=diagnostic,
        )

    return RelatedAutopilotState(
        status="no_match_with_coverage",
        properties=(),
        provenance=autopilot_provenance,
        key_matches=key_matches,
        matched_row_index=None,
        conflict_diagnostic=diagnostic,
    )


def enrich_managed_devices_with_autopilot(
    managed_devices: UserManagedDevicesEnrichment,
    index: AutopilotSnapshotIndex | None,
    *,
    target: datetime,
    autopilot_snapshot_at: datetime | None,
    autopilot_source_relative_path: str = "",
) -> UserManagedDevicesEnrichment:
    autopilot_provenance: SourceProvenance | None = None
    autopilot_family_coverage: FamilyCoverage | None = None

    if autopilot_snapshot_at is None:
        autopilot_family_coverage = FamilyCoverage(
            family=AUTOPILOT_FAMILY,
            status="no_snapshot",
            requested_at=target,
            snapshot_at=None,
            gap=None,
            entity_present=False,
            source_relative_path="",
            source_report_family="",
        )
    else:
        gap = target - autopilot_snapshot_at
        autopilot_family_coverage = FamilyCoverage(
            family=AUTOPILOT_FAMILY,
            status="snapshot_used",
            requested_at=target,
            snapshot_at=autopilot_snapshot_at,
            gap=gap,
            entity_present=index is not None and len(index.rows) > 0,
            source_relative_path=autopilot_source_relative_path,
            source_report_family=autopilot_source_relative_path.split("_20")[0]
            if autopilot_source_relative_path
            else AUTOPILOT_FAMILY,
        )
        autopilot_provenance = single_provenance(
            ProvenanceObservation(
                family=AUTOPILOT_FAMILY,
                observed_at=autopilot_snapshot_at,
                snapshot_at=autopilot_snapshot_at,
                requested_at=target,
                gap=gap,
            )
        )

    enriched: list[EnrichedManagedDevice] = []
    snapshot_exists = index is not None and autopilot_snapshot_at is not None
    provenance_for_match = autopilot_provenance if snapshot_exists else None

    for device in managed_devices.devices:
        autopilot_state = match_device_to_autopilot(
            device,
            index if index is not None else AutopilotSnapshotIndex((), {}, {}, {}),
            autopilot_snapshot_exists=snapshot_exists,
            target=target,
            snapshot_at=autopilot_snapshot_at or target,
            autopilot_provenance=provenance_for_match,
        )
        enriched.append(EnrichedManagedDevice(device=device, autopilot=autopilot_state))

    return UserManagedDevicesEnrichment(
        devices=managed_devices.devices,
        coverage=managed_devices.coverage,
        family_coverage=managed_devices.family_coverage,
        unresolved_observations=managed_devices.unresolved_observations,
        snapshot_at=managed_devices.snapshot_at,
        snapshot_file_id=managed_devices.snapshot_file_id,
        source_relative_path=managed_devices.source_relative_path,
        enriched_devices=tuple(enriched),
        autopilot_family_coverage=autopilot_family_coverage,
    )
