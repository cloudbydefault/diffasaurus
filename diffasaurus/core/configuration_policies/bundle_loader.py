"""Load Phase 0 Configuration Policy Snapshot Bundles."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LoadedPolicyDocument:
    inventory_row: dict[str, str]
    document: dict[str, Any]
    json_relative_path: str


@dataclass(frozen=True)
class LoadedConfigurationPolicyBundle:
    bundle_path: Path
    manifest: dict[str, Any]
    inventory_rows: list[dict[str, str]]
    assignment_filters: dict[str, Any] | None
    policies: list[LoadedPolicyDocument]


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_inventory(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_configuration_policy_bundle(bundle_path: Path) -> LoadedConfigurationPolicyBundle:
    resolved = bundle_path.resolve()
    if resolved.is_file():
        resolved = resolved.parent

    manifest_path = resolved / "snapshot_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing snapshot_manifest.json in {resolved}")

    manifest = _load_json(manifest_path)
    inventory_rel = str(manifest.get("inventoryRelativePath", "inventory.csv"))
    inventory_rows = _read_inventory(resolved / inventory_rel)

    filters_rel = str(manifest.get("assignmentFiltersRelativePath", "assignment_filters.json"))
    filters_path = resolved / filters_rel
    assignment_filters = _load_json(filters_path) if filters_path.exists() else None

    policies: list[LoadedPolicyDocument] = []
    for row in inventory_rows:
        json_rel = str(row.get("JsonRelativePath", "")).strip()
        if not json_rel:
            continue
        policy_path = resolved / json_rel
        if not policy_path.is_file():
            continue
        policies.append(
            LoadedPolicyDocument(
                inventory_row=row,
                document=_load_json(policy_path),
                json_relative_path=json_rel,
            )
        )

    return LoadedConfigurationPolicyBundle(
        bundle_path=resolved,
        manifest=manifest,
        inventory_rows=inventory_rows,
        assignment_filters=assignment_filters,
        policies=policies,
    )
