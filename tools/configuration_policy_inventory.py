"""Inventory CSV contract helpers for Intune Configuration Policy bundles (Phase 0)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

INVENTORY_COLUMNS: tuple[str, ...] = (
    "SnapshotId",
    "CapturedAtUtc",
    "Platform",
    "PolicyType",
    "Source",
    "PolicyName",
    "Description",
    "PolicyId",
    "ODataType",
    "PlatformsRaw",
    "Technologies",
    "TemplateFamily",
    "TemplateDisplayName",
    "TemplateDisplayVersion",
    "SettingCount",
    "RetrievedSettingCount",
    "AssignmentCount",
    "AssignmentTargets",
    "IsAssigned",
    "RoleScopeTagIds",
    "CreatedDateTime",
    "LastModifiedDateTime",
    "Version",
    "JsonRelativePath",
    "RetrievalStatus",
    "SettingsRetrievalStatus",
    "AssignmentsRetrievalStatus",
    "DefinitionsRetrievalStatus",
)

EXPORT_STATUS_COMPLETE = "complete"
EXPORT_STATUS_INCOMPLETE = "incomplete"
EXPORT_STATUS_INTEGRITY_ERROR = "integrity_error"

REQUIRED_INVENTORY_COLUMNS: tuple[str, ...] = (
    "SnapshotId",
    "CapturedAtUtc",
    "PolicyId",
    "Source",
    "Platform",
    "PolicyType",
    "JsonRelativePath",
    "RetrievalStatus",
)


def validate_inventory_schema(fieldnames: Iterable[str] | None) -> list[str]:
    names = list(fieldnames or [])
    if not names:
        return ["inventory_missing_header"]
    present = set(names)
    return [
        f"inventory_missing_required_column:{column}"
        for column in REQUIRED_INVENTORY_COLUMNS
        if column not in present
    ]


def read_inventory_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def normalize_inventory_row(row: dict[str, Any] | None) -> dict[str, str]:
    source = row or {}
    return {
        column: "" if source.get(column) is None else str(source[column])
        for column in INVENTORY_COLUMNS
    }


def write_inventory_csv(path: Path, rows: Iterable[dict[str, Any] | None] | None) -> None:
    """Write deterministic inventory/anchor CSV with header-only support for zero rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [normalize_inventory_row(row) for row in (rows or []) if row is not None]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(INVENTORY_COLUMNS))
        writer.writeheader()
        writer.writerows(normalized)


def validate_source_export_accounting(
    *,
    listed: int,
    exported: int,
    processing_errors: int,
    source_name: str = "source",
) -> list[str]:
    """Return structural integrity errors when listed policies are unaccounted for."""
    if listed <= 0:
        return []
    if exported + processing_errors != listed:
        return [f"{source_name}_source_accounting_mismatch"]
    return []


def resolve_export_status_from_coverage(
    source_coverage: dict[str, dict[str, object]],
) -> str:
    """Mirror exporter finalization rules for source accounting and processing errors."""
    integrity_errors: list[str] = []
    processing_errors = 0
    list_errors = 0

    for source_name, coverage in source_coverage.items():
        if source_name == "assignmentFilters":
            continue
        if not isinstance(coverage, dict):
            continue
        status = str(coverage.get("status", ""))
        if status == "skipped_by_option":
            continue
        listed = int(coverage.get("count", 0) or 0)
        exported = int(coverage.get("exportedCount", 0) or 0)
        errors = int(coverage.get("processingErrors", 0) or 0)
        integrity_errors.extend(
            validate_source_export_accounting(
                listed=listed,
                exported=exported,
                processing_errors=errors,
                source_name=source_name,
            )
        )
        processing_errors += errors
        if status == "error":
            list_errors += 1

    if integrity_errors:
        return EXPORT_STATUS_INTEGRITY_ERROR
    if processing_errors > 0 or list_errors > 0:
        return EXPORT_STATUS_INCOMPLETE
    return EXPORT_STATUS_COMPLETE


def bundle_is_complete(bundle_root: Path) -> bool:
    manifest_path = bundle_root / "snapshot_manifest.json"
    if not manifest_path.exists():
        return False
    import json

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("exportStatus") != EXPORT_STATUS_COMPLETE:
        return False

    inventory_rel = str(manifest.get("inventoryRelativePath", "inventory.csv"))
    anchor_name = str(manifest.get("anchorRelativePath", ""))
    inventory_path = bundle_root / inventory_rel
    if not inventory_path.exists():
        return False
    if anchor_name:
        anchor_path = bundle_root.parent / anchor_name
        if not anchor_path.exists():
            return False
    return True
