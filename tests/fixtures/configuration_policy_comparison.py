"""Synthetic fixtures for Configuration Policy comparison tests (Phase 2)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from tests.fixtures.configuration_policy_bundle import (
    CAPTURED_AT_UTC,
    POLICY_EXPORT_SCHEMA_VERSION,
    SNAPSHOT_ID,
    SNAPSHOT_SCHEMA_VERSION,
    _write_json,
    build_definition_object,
    build_modern_policy_fixture,
    build_simple_modern_setting,
)
from tools.configuration_policy_inventory import EXPORT_STATUS_COMPLETE, write_inventory_csv

DEFAULT_SOURCE_COVERAGE: dict[str, dict[str, Any]] = {
    "modern": {"status": "success", "count": 1, "exportedCount": 1, "processingErrors": 0},
    "classic": {"status": "success", "count": 0, "exportedCount": 0, "processingErrors": 0},
    "administrativeTemplates": {
        "status": "skipped_by_option",
        "count": 0,
        "exportedCount": 0,
        "processingErrors": 0,
    },
    "assignmentFilters": {"status": "success", "count": 0, "error": None},
}


def build_comparison_bundle(
    root: Path,
    *,
    snapshot_id: str,
    captured_at_utc: str,
    policies: list[tuple[str, dict[str, Any], dict[str, str]]],
    export_status: str = EXPORT_STATUS_COMPLETE,
    source_coverage: dict[str, dict[str, Any]] | None = None,
    assignment_filters: dict[str, Any] | None = None,
) -> Path:
    bundle_root = root / snapshot_id
    bundle_root.mkdir(parents=True, exist_ok=True)

    inventory_rows: list[dict[str, str]] = []
    for relative_path, payload, row in policies:
        policy_payload = copy.deepcopy(payload)
        policy_payload["snapshotId"] = snapshot_id
        policy_payload["capturedAtUtc"] = captured_at_utc
        row = dict(row)
        row["SnapshotId"] = snapshot_id
        row["CapturedAtUtc"] = captured_at_utc
        _write_json(bundle_root / relative_path, policy_payload)
        inventory_rows.append(row)

    write_inventory_csv(bundle_root / "inventory.csv", inventory_rows)

    filters_payload = assignment_filters or {
        "snapshotId": snapshot_id,
        "capturedAtUtc": captured_at_utc,
        "retrieval": {"status": "success", "count": 0, "error": None},
        "assignmentFilters": [],
    }
    _write_json(bundle_root / "assignment_filters.json", filters_payload)

    coverage = copy.deepcopy(source_coverage or DEFAULT_SOURCE_COVERAGE)
    _write_json(
        bundle_root / "snapshot_manifest.json",
        {
            "snapshotSchemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
            "snapshotId": snapshot_id,
            "capturedAtUtc": captured_at_utc,
            "exportStatus": export_status,
            "inventoryRelativePath": "inventory.csv",
            "assignmentFiltersRelativePath": "assignment_filters.json",
            "policyCount": len(inventory_rows),
            "sourceCoverage": coverage,
        },
    )
    return bundle_root


def build_modern_inventory_row(
    *,
    policy_id: str,
    policy_name: str,
    json_relative_path: str,
) -> dict[str, str]:
    return {
        "SnapshotId": SNAPSHOT_ID,
        "CapturedAtUtc": CAPTURED_AT_UTC,
        "Platform": "Windows",
        "PolicyType": "Settings catalog",
        "Source": "Modern",
        "PolicyName": policy_name,
        "Description": "",
        "PolicyId": policy_id,
        "ODataType": "#microsoft.graph.deviceManagementConfigurationPolicy",
        "PlatformsRaw": "windows10",
        "Technologies": "mdm",
        "TemplateFamily": "",
        "TemplateDisplayName": "",
        "TemplateDisplayVersion": "",
        "SettingCount": "1",
        "RetrievedSettingCount": "1",
        "AssignmentCount": "0",
        "AssignmentTargets": "",
        "IsAssigned": "False",
        "RoleScopeTagIds": "0",
        "CreatedDateTime": CAPTURED_AT_UTC,
        "LastModifiedDateTime": CAPTURED_AT_UTC,
        "Version": "",
        "JsonRelativePath": json_relative_path,
        "RetrievalStatus": "success",
        "SettingsRetrievalStatus": "success",
        "AssignmentsRetrievalStatus": "success",
        "DefinitionsRetrievalStatus": "success",
    }


def build_basic_modern_policy_document(
    *,
    policy_id: str = "policy-modern-001",
    policy_name: str = "Synthetic Policy",
    simple_value: str = "enabled",
    choice_value: str = "option-a",
    include_assignment: bool = False,
) -> dict[str, Any]:
    policy = build_modern_policy_fixture(
        settings=[
            {
                "id": "setting-simple",
                "settingInstance": {
                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                    "settingDefinitionId": "def-simple-001",
                    "simpleSettingValue": {"value": simple_value},
                },
            },
            {
                "id": "setting-choice",
                "settingInstance": {
                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
                    "settingDefinitionId": "def-choice-001",
                    "choiceSettingValue": {"value": choice_value, "children": []},
                },
            },
        ],
        setting_definitions={
            "def-simple-001": build_definition_object("def-simple-001", display_name="Simple Label A"),
            "def-choice-001": {
                **build_definition_object(
                    "def-choice-001",
                    odata_type="#microsoft.graph.deviceManagementConfigurationChoiceSettingDefinition",
                    display_name="Choice Label A",
                ),
                "options": [{"itemId": "option-a", "displayName": "Option A"}],
            },
        },
        policy_id=policy_id,
        policy_name=policy_name,
    )
    policy["policy"]["description"] = "Synthetic description"
    if include_assignment:
        policy["assignments"] = [
            {"target": {"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"}},
            {
                "target": {
                    "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                    "groupId": "group-001",
                    "displayName": "Group Label",
                }
            },
        ]
        policy["retrieval"]["assignments"] = {"status": "success", "count": 2, "error": None}
    return policy
