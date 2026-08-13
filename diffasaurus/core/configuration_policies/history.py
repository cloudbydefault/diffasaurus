"""Historical Configuration Policy snapshot discovery (Phase 2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from diffasaurus.core.configuration_policies.comparison_models import (
    DiscoveryDiagnostic,
    DiscoveryResult,
    SnapshotDescriptor,
)

SUPPORTED_SNAPSHOT_SCHEMA_VERSION = 1
SUPPORTED_POLICY_EXPORT_SCHEMA_VERSION = 4
_REQUIRED_MANIFEST_KEYS = ("snapshotId", "capturedAtUtc", "inventoryRelativePath")


def _parse_captured_at_utc(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _snapshot_sort_key(descriptor: SnapshotDescriptor) -> tuple[str, str]:
    return (descriptor.captured_at_utc, descriptor.snapshot_id)


def _validate_manifest_bundle(bundle_path: Path, manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in _REQUIRED_MANIFEST_KEYS:
        if key not in manifest or manifest.get(key) in (None, ""):
            issues.append(f"missing_manifest_field:{key}")

    snapshot_schema = manifest.get("snapshotSchemaVersion")
    if snapshot_schema not in (None, SUPPORTED_SNAPSHOT_SCHEMA_VERSION):
        issues.append("unsupported_snapshot_schema_version")

    export_schema = manifest.get("policyExportSchemaVersion")
    if export_schema not in (None, SUPPORTED_POLICY_EXPORT_SCHEMA_VERSION):
        issues.append("unsupported_policy_export_schema_version")

    captured_at = str(manifest.get("capturedAtUtc", ""))
    if _parse_captured_at_utc(captured_at) is None:
        issues.append("invalid_captured_at_utc")

    inventory_rel = str(manifest.get("inventoryRelativePath", "inventory.csv"))
    inventory_path = bundle_path / inventory_rel
    if not inventory_path.is_file():
        issues.append("missing_inventory_csv")

    return issues


def _descriptor_from_bundle(bundle_path: Path) -> tuple[SnapshotDescriptor | None, list[str]]:
    manifest_path = bundle_path / "snapshot_manifest.json"
    if not manifest_path.is_file():
        return None, ["missing_snapshot_manifest"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ["manifest_unreadable"]

    if not isinstance(manifest, dict):
        return None, ["manifest_invalid"]

    issues = _validate_manifest_bundle(bundle_path, manifest)
    if issues:
        return None, issues

    inventory_rel = str(manifest.get("inventoryRelativePath", "inventory.csv"))
    policy_count = int(manifest.get("policyCount", 0) or 0)
    if policy_count <= 0:
        try:
            with (bundle_path / inventory_rel).open(encoding="utf-8-sig", newline="") as handle:
                policy_count = max(sum(1 for _ in handle) - 1, 0)
        except OSError:
            policy_count = 0

    source_coverage = manifest.get("sourceCoverage")
    if not isinstance(source_coverage, dict):
        source_coverage = {}

    descriptor = SnapshotDescriptor(
        path=str(bundle_path.resolve()),
        snapshot_id=str(manifest.get("snapshotId", "")),
        captured_at_utc=str(manifest.get("capturedAtUtc", "")),
        snapshot_schema_version=int(manifest.get("snapshotSchemaVersion", 0) or 0),
        policy_export_schema_version=int(manifest.get("policyExportSchemaVersion", 0) or 0),
        export_status=str(manifest.get("exportStatus", "")),
        policy_count=policy_count,
        source_coverage=source_coverage,
    )
    return descriptor, []


def _candidate_bundle_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    if (root / "snapshot_manifest.json").is_file():
        candidates.append(root.resolve())

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "snapshot_manifest.json").is_file():
            candidates.append(child.resolve())

    return candidates


def discover_policy_snapshots(root: Path | str) -> DiscoveryResult:
    resolved_root = Path(root).resolve()
    if not resolved_root.exists():
        return DiscoveryResult(
            diagnostics=[
                DiscoveryDiagnostic(
                    path=str(resolved_root),
                    category="root_missing",
                    message="discovery_root_not_found",
                )
            ]
        )

    snapshots: list[SnapshotDescriptor] = []
    diagnostics: list[DiscoveryDiagnostic] = []
    seen_paths: set[str] = set()

    for candidate in _candidate_bundle_paths(resolved_root):
        candidate_key = str(candidate)
        if candidate_key in seen_paths:
            continue
        seen_paths.add(candidate_key)

        descriptor, issues = _descriptor_from_bundle(candidate)
        if descriptor is None:
            for issue in issues:
                diagnostics.append(
                    DiscoveryDiagnostic(
                        path=candidate_key,
                        category=issue,
                        message=issue,
                    )
                )
            continue
        snapshots.append(descriptor)

    snapshots.sort(key=_snapshot_sort_key)
    return DiscoveryResult(snapshots=snapshots, diagnostics=diagnostics)


def select_latest_snapshot(root: Path | str) -> SnapshotDescriptor | None:
    result = discover_policy_snapshots(root)
    if not result.snapshots:
        return None
    return result.snapshots[-1]


def select_previous_snapshot(
    root: Path | str,
    target: SnapshotDescriptor | str,
) -> SnapshotDescriptor | None:
    result = discover_policy_snapshots(root)
    if not result.snapshots:
        return None

    target_id = target.snapshot_id if isinstance(target, SnapshotDescriptor) else str(target)
    index = next(
        (idx for idx, item in enumerate(result.snapshots) if item.snapshot_id == target_id),
        None,
    )
    if index is None or index == 0:
        return None
    return result.snapshots[index - 1]


@dataclass(frozen=True)
class SnapshotPair:
    baseline: SnapshotDescriptor
    target: SnapshotDescriptor


def select_latest_pair(root: Path | str) -> SnapshotPair | None:
    result = discover_policy_snapshots(root)
    if len(result.snapshots) < 2:
        return None
    return SnapshotPair(
        baseline=result.snapshots[-2],
        target=result.snapshots[-1],
    )


def resolve_snapshot_descriptor(root: Path | str, snapshot_id_or_path: str) -> SnapshotDescriptor | None:
    candidate = Path(snapshot_id_or_path)
    if candidate.exists():
        descriptor, issues = _descriptor_from_bundle(candidate.resolve())
        return descriptor if not issues else None

    result = discover_policy_snapshots(root)
    for item in result.snapshots:
        if item.snapshot_id == snapshot_id_or_path:
            return item
    return None
