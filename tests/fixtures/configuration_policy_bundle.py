"""Synthetic Configuration Policy Snapshot Bundle fixtures for Phase 0 tests."""

from __future__ import annotations

import json
from pathlib import Path

from tools.configuration_policy_inventory import EXPORT_STATUS_COMPLETE, INVENTORY_COLUMNS, write_inventory_csv
from tools.inspect_configuration_policy_bundle import collect_recursive_definition_ids

SNAPSHOT_SCHEMA_VERSION = 1
POLICY_EXPORT_SCHEMA_VERSION = 4
TIMESTAMP = "20990101-120000"
SNAPSHOT_ID = f"Intune_ConfigurationPolicies_{TIMESTAMP}"
CAPTURED_AT_UTC = "2099-01-01T12:00:00.0000000Z"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _inventory_row(**overrides: object) -> dict[str, str]:
    row = {
        "SnapshotId": SNAPSHOT_ID,
        "CapturedAtUtc": CAPTURED_AT_UTC,
        "Platform": "Windows",
        "PolicyType": "Settings catalog",
        "Source": "Modern",
        "PolicyName": "Synthetic Policy",
        "Description": "Synthetic description",
        "PolicyId": "policy-modern-001",
        "ODataType": "#microsoft.graph.deviceManagementConfigurationPolicy",
        "PlatformsRaw": "windows10",
        "Technologies": "mdm",
        "TemplateFamily": "settingsCatalog",
        "TemplateDisplayName": "Settings catalog",
        "TemplateDisplayVersion": "1",
        "SettingCount": "1",
        "RetrievedSettingCount": "1",
        "AssignmentCount": "1",
        "AssignmentTargets": "allDevices",
        "IsAssigned": "True",
        "RoleScopeTagIds": "0",
        "CreatedDateTime": CAPTURED_AT_UTC,
        "LastModifiedDateTime": CAPTURED_AT_UTC,
        "Version": "",
        "JsonRelativePath": "Windows/Modern/Synthetic Policy__policy-modern-001.json",
        "RetrievalStatus": "success",
        "SettingsRetrievalStatus": "success",
        "AssignmentsRetrievalStatus": "success",
        "DefinitionsRetrievalStatus": "success",
    }
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def build_synthetic_bundle(root: Path) -> Path:
    bundle_root = root / SNAPSHOT_ID
    bundle_root.mkdir(parents=True, exist_ok=True)

    modern_policy = {
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportSource": "configurationPolicies",
        "platform": "Windows",
        "policyType": "Settings catalog",
        "retrieval": {
            "policyDetail": {"status": "success", "count": 1, "error": None},
            "settings": {"status": "success", "count": 2, "error": None},
            "assignments": {"status": "success", "count": 1, "error": None},
            "settingDefinitions": {
                "status": "success",
                "count": 3,
                "requestedCount": 3,
                "foundCount": 3,
                "missingCount": 0,
                "error": None,
            },
        },
        "policy": {
            "@odata.type": "#microsoft.graph.deviceManagementConfigurationPolicy",
            "id": "policy-modern-001",
            "name": "Synthetic Policy",
            "description": "Synthetic description",
            "platforms": "windows10",
            "technologies": "mdm",
            "settingCount": 2,
            "isAssigned": True,
            "roleScopeTagIds": ["0"],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
            "templateReference": {
                "templateFamily": "settingsCatalog",
                "templateDisplayName": "Settings catalog",
                "templateDisplayVersion": "1",
            },
        },
        "settings": [
            {
                "id": "setting-simple-001",
                "settingInstance": {
                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                    "settingDefinitionId": "def-simple-001",
                    "simpleSettingValue": {"value": "enabled"},
                },
            },
            {
                "id": "setting-group-001",
                "settingInstance": {
                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
                    "settingDefinitionId": "def-group-001",
                    "groupSettingCollectionValue": [
                        {
                            "settingInstance": {
                                "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
                                "settingDefinitionId": "def-choice-001",
                                "choiceSettingValue": {
                                    "value": "option-a",
                                    "children": [],
                                },
                            }
                        }
                    ],
                },
            },
        ],
        "settingDefinitions": {
            "def-simple-001": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition",
                "id": "def-simple-001",
                "displayName": "Synthetic simple setting",
                "description": "Synthetic definition",
            },
            "def-choice-001": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingDefinition",
                "id": "def-choice-001",
                "displayName": "Synthetic choice setting",
                "description": "Synthetic choice definition",
                "options": [
                    {"itemId": "option-a", "displayName": "Option A"},
                    {"itemId": "option-b", "displayName": "Option B"},
                ],
                "defaultOptionId": "option-a",
            },
            "def-group-001": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionDefinition",
                "id": "def-group-001",
                "displayName": "Synthetic group setting",
                "description": "Synthetic group definition",
            },
        },
        "assignments": [
            {
                "target": {
                    "@odata.type": "#microsoft.graph.allDevicesAssignmentTarget",
                }
            }
        ],
    }

    classic_policy = {
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportSource": "deviceConfigurations",
        "platform": "iOS/iPadOS",
        "policyType": "Device restrictions",
        "retrieval": {
            "policyDetail": {"status": "success", "count": 1, "error": None},
            "settings": {"status": "not_applicable", "count": 0, "error": None},
            "assignments": {"status": "success", "count": 1, "error": None},
            "settingDefinitions": {"status": "not_applicable", "count": 0, "error": None},
        },
        "policy": {
            "@odata.type": "#microsoft.graph.iosGeneralDeviceConfiguration",
            "id": "policy-classic-001",
            "displayName": "Synthetic Classic",
            "description": "Classic synthetic",
            "accountBlockModification": True,
            "appStoreBlocked": False,
            "version": 1,
            "roleScopeTagIds": ["0"],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
        },
        "assignments": [
            {
                "target": {
                    "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                    "groupId": "group-synthetic-001",
                    "deviceAndAppManagementAssignmentFilterId": "filter-synthetic-001",
                    "deviceAndAppManagementAssignmentFilterType": "include",
                }
            }
        ],
    }

    admx_policy = {
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportSource": "groupPolicyConfigurations",
        "platform": "Windows",
        "policyType": "Administrative Templates / ADMX",
        "retrieval": {
            "policyDetail": {"status": "success", "count": 1, "error": None},
            "definitionValues": {"status": "success", "count": 1, "error": None},
            "presentationValues": {"status": "partial", "count": 1, "error": "1 presentation value requests failed"},
            "assignments": {"status": "success", "count": 0, "error": None},
            "settings": {"status": "not_applicable", "count": 0, "error": None},
            "settingDefinitions": {"status": "not_applicable", "count": 0, "error": None},
        },
        "policy": {
            "@odata.type": "#microsoft.graph.groupPolicyConfiguration",
            "id": "policy-admx-001",
            "displayName": "Synthetic ADMX",
            "description": "ADMX synthetic",
            "roleScopeTagIds": ["0"],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
        },
        "definitionValues": [
            {
                "id": "def-value-001",
                "enabled": True,
                "definition": {
                    "@odata.type": "#microsoft.graph.groupPolicyDefinition",
                    "id": "admx-def-001",
                    "displayName": "Synthetic ADMX definition",
                },
                "presentationValues": [
                    {
                        "@odata.type": "#microsoft.graph.groupPolicyPresentationValueText",
                        "value": "synthetic",
                    }
                ],
                "presentationRetrieval": {"status": "success", "count": 1, "error": None},
            }
        ],
        "assignments": [],
    }

    error_policy = {
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportSource": "configurationPolicies",
        "platform": "Android",
        "policyType": "Settings catalog",
        "retrieval": {
            "policyDetail": {"status": "success", "count": 1, "error": None},
            "settings": {"status": "error", "count": 0, "error": "Synthetic settings retrieval failure"},
            "assignments": {"status": "success", "count": 0, "error": None},
            "settingDefinitions": {"status": "error", "count": 0, "error": "Settings retrieval failed"},
        },
        "policy": {
            "@odata.type": "#microsoft.graph.deviceManagementConfigurationPolicy",
            "id": "policy-modern-error-001",
            "name": "Synthetic Error Policy",
            "platforms": "android",
            "technologies": "android",
            "settingCount": 0,
            "isAssigned": False,
            "roleScopeTagIds": [],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
            "templateReference": {},
        },
        "settings": [],
        "settingDefinitions": {},
        "assignments": [],
    }

    policy_files = [
        (
            "Windows/Modern/Synthetic Policy__policy-modern-001.json",
            modern_policy,
            _inventory_row(),
        ),
        (
            "iOS-iPadOS/Classic/Synthetic Classic__policy-classic-001.json",
            classic_policy,
            _inventory_row(
                Platform="iOS/iPadOS",
                PolicyType="Device restrictions",
                Source="Classic",
                PolicyName="Synthetic Classic",
                PolicyId="policy-classic-001",
                ODataType="#microsoft.graph.iosGeneralDeviceConfiguration",
                PlatformsRaw="",
                Technologies="",
                TemplateFamily="",
                TemplateDisplayName="",
                TemplateDisplayVersion="",
                SettingCount="",
                RetrievedSettingCount="",
                JsonRelativePath="iOS-iPadOS/Classic/Synthetic Classic__policy-classic-001.json",
                DefinitionsRetrievalStatus="not_applicable",
                SettingsRetrievalStatus="not_applicable",
            ),
        ),
        (
            "Windows/AdministrativeTemplates/Synthetic ADMX__policy-admx-001.json",
            admx_policy,
            _inventory_row(
                Platform="Windows",
                PolicyType="Administrative Templates / ADMX",
                Source="AdministrativeTemplate",
                PolicyName="Synthetic ADMX",
                PolicyId="policy-admx-001",
                ODataType="#microsoft.graph.groupPolicyConfiguration",
                PlatformsRaw="",
                Technologies="",
                TemplateFamily="",
                TemplateDisplayName="",
                TemplateDisplayVersion="",
                SettingCount="1",
                RetrievedSettingCount="1",
                AssignmentCount="0",
                AssignmentTargets="",
                IsAssigned="False",
                JsonRelativePath="Windows/AdministrativeTemplates/Synthetic ADMX__policy-admx-001.json",
                RetrievalStatus="partial",
                SettingsRetrievalStatus="not_applicable",
                DefinitionsRetrievalStatus="partial",
            ),
        ),
        (
            "Android/Modern/Synthetic Error Policy__policy-modern-error-001.json",
            error_policy,
            _inventory_row(
                Platform="Android",
                PolicyName="Synthetic Error Policy",
                PolicyId="policy-modern-error-001",
                PlatformsRaw="android",
                Technologies="android",
                SettingCount="0",
                RetrievedSettingCount="0",
                AssignmentCount="0",
                AssignmentTargets="",
                IsAssigned="False",
                JsonRelativePath="Android/Modern/Synthetic Error Policy__policy-modern-error-001.json",
                RetrievalStatus="partial",
                SettingsRetrievalStatus="error",
                DefinitionsRetrievalStatus="error",
            ),
        ),
    ]

    inventory_rows = []
    for relative_path, payload, row in policy_files:
        _write_json(bundle_root / relative_path, payload)
        inventory_rows.append(row)

    inventory_rows.sort(key=lambda item: (item["Platform"], item["PolicyType"], item["PolicyName"]))

    inventory_path = bundle_root / "inventory.csv"
    write_inventory_csv(inventory_path, inventory_rows)

    anchor_path = root / f"{SNAPSHOT_ID}.csv"
    write_inventory_csv(anchor_path, inventory_rows)

    assignment_filters = {
        "snapshotSchemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "retrieval": {"status": "success", "count": 1, "error": None},
        "assignmentFilters": [
            {
                "id": "filter-synthetic-001",
                "displayName": "Synthetic filter",
                "description": "Synthetic",
                "platform": "windows10AndLater",
                "rule": "device.manufacturer -eq 'Synthetic'",
                "roleScopeTags": ["0"],
                "assignmentFilterManagementType": "devices",
            }
        ],
    }
    _write_json(bundle_root / "assignment_filters.json", assignment_filters)

    diagnostics = {
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "retrievalSummary": {
            "policiesComplete": 2,
            "policiesPartial": 2,
            "policiesError": 0,
            "assignmentRetrievalErrors": 0,
            "settingsRetrievalErrors": 1,
            "definitionRetrievalErrors": 1,
            "presentationRetrievalErrors": 0,
        },
        "graphRequestCount": 10,
        "batchHttpRequestCount": 2,
        "batchItemCount": 4,
        "batchRequestCount": 4,
        "settingDefinitionRequests": 3,
        "settingDefinitionsFound": 3,
        "settingDefinitionsMissing": 0,
        "definitionRetrievalErrors": 1,
        "presentationValueRequests": 1,
        "exportDurationSeconds": 1.23,
        "entries": [],
    }
    _write_json(bundle_root / "retrieval_diagnostics.json", diagnostics)

    manifest = {
        "snapshotSchemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "timestamp": TIMESTAMP,
        "requiredGraphScope": "DeviceManagementConfiguration.Read.All",
        "bundleName": SNAPSHOT_ID,
        "exportStatus": EXPORT_STATUS_COMPLETE,
        "sourceCoverage": {
            "modern": {"status": "success", "count": 2, "error": None},
            "classic": {"status": "success", "count": 1, "error": None},
            "administrativeTemplates": {"status": "success", "count": 1, "error": None},
            "assignmentFilters": {"status": "success", "count": 1, "error": None},
        },
        "policyCount": len(inventory_rows),
        "platformCounts": {
            "Windows": 2,
            "iOS/iPadOS": 1,
            "Android": 1,
        },
        "sourceCounts": {
            "Modern": 2,
            "Classic": 1,
            "AdministrativeTemplate": 1,
        },
        "policyTypeCounts": {
            "Windows|Settings catalog": 1,
            "Windows|Administrative Templates / ADMX": 1,
            "iOS/iPadOS|Device restrictions": 1,
            "Android|Settings catalog": 1,
        },
        "inventoryRelativePath": "inventory.csv",
        "anchorRelativePath": f"{SNAPSHOT_ID}.csv",
        "assignmentFiltersRelativePath": "assignment_filters.json",
        "diagnosticsRelativePath": "retrieval_diagnostics.json",
        "retrievalSummary": diagnostics["retrievalSummary"],
        "exportDurationSeconds": 1.23,
        "graphRequestCount": 10,
        "batchHttpRequestCount": 2,
        "batchItemCount": 4,
        "batchRequestCount": 4,
        "settingDefinitionRequests": 3,
        "settingDefinitionsFound": 3,
        "settingDefinitionsMissing": 0,
        "definitionRetrievalErrors": 1,
        "presentationValueRequests": 1,
    }
    _write_json(bundle_root / "snapshot_manifest.json", manifest)

    for relative in (
        "Windows/Modern",
        "Windows/Classic",
        "Windows/AdministrativeTemplates",
        "macOS/Modern",
        "macOS/Classic",
        "iOS-iPadOS/Modern",
        "iOS-iPadOS/Classic",
        "Android/Modern",
        "Android/Classic",
        "Other/Modern",
        "Other/Classic",
    ):
        (bundle_root / relative).mkdir(parents=True, exist_ok=True)

    return bundle_root


def build_depth3_modern_setting_tree() -> dict[str, object]:
    """Group(depth0)->Group(depth1)->Group(depth2)->5 leaves at depth3."""
    leaf_specs = [
        ("#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance", "def-choice-1"),
        ("#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance", "def-simple-1"),
        ("#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance", "def-simple-2"),
        ("#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance", "def-choice-2"),
        ("#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance", "def-choice-3"),
    ]
    leaf_nodes = [
        {
            "settingInstance": {
                "@odata.type": odata_type,
                "settingDefinitionId": definition_id,
            }
        }
        for odata_type, definition_id in leaf_specs
    ]
    depth2 = {
        "settingInstance": {
            "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
            "settingDefinitionId": "def-group-2",
            "groupSettingCollectionValue": [{"children": leaf_nodes}],
        }
    }
    depth1 = {
        "settingInstance": {
            "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
            "settingDefinitionId": "def-group-1",
            "groupSettingCollectionValue": [{"children": [depth2]}],
        }
    }
    return {
        "id": "setting-depth3-001",
        "settingInstance": {
            "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
            "settingDefinitionId": "def-group-0",
            "groupSettingCollectionValue": [{"children": [depth1]}],
        },
    }


def build_modern_policy_fixture(
    *,
    settings: list[dict[str, object]],
    setting_definitions: dict[str, dict[str, object]] | None = None,
    setting_definitions_retrieval: dict[str, object] | None = None,
    policy_id: str = "policy-nested-001",
    platform: str = "Windows",
    policy_type: str = "Settings catalog",
    policy_name: str = "Nested structure policy",
    platforms_raw: str = "windows10",
    technologies: str = "mdm",
    include_template_reference: bool = True,
) -> dict[str, object]:
    definitions = setting_definitions or {}
    definitions_retrieval = setting_definitions_retrieval or {
        "status": "success",
        "count": len(definitions),
        "requestedCount": len(definitions),
        "foundCount": len(definitions),
        "missingCount": 0,
        "error": None,
    }
    policy_body: dict[str, object] = {
        "@odata.type": "#microsoft.graph.deviceManagementConfigurationPolicy",
        "id": policy_id,
        "name": policy_name,
        "platforms": platforms_raw,
        "technologies": technologies,
        "settingCount": len(settings),
        "isAssigned": False,
        "roleScopeTagIds": ["0"],
        "createdDateTime": CAPTURED_AT_UTC,
        "lastModifiedDateTime": CAPTURED_AT_UTC,
    }
    if include_template_reference:
        policy_body["templateReference"] = {}
    return {
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportSource": "configurationPolicies",
        "platform": platform,
        "policyType": policy_type,
        "retrieval": {
            "policyDetail": {"status": "success", "count": 1, "error": None},
            "settings": {"status": "success", "count": len(settings), "error": None},
            "assignments": {"status": "success", "count": 0, "error": None},
            "settingDefinitions": definitions_retrieval,
        },
        "policy": policy_body,
        "settings": settings,
        "settingDefinitions": setting_definitions or {},
        "assignments": [],
    }


def build_simple_modern_setting(
    definition_id: str,
    *,
    setting_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": setting_id or f"setting-{definition_id}",
        "settingInstance": {
            "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
            "settingDefinitionId": definition_id,
        },
    }


def build_definition_object(
    definition_id: str,
    *,
    odata_type: str = "#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition",
    display_name: str | None = None,
) -> dict[str, object]:
    return {
        "@odata.type": odata_type,
        "id": definition_id,
        "displayName": display_name or f"Definition {definition_id}",
        "description": "Synthetic definition",
    }


def build_cache_propagation_bundle(root: Path) -> Path:
    """Two-policy bundle where def-B is reused and must appear in both policy JSON files."""
    bundle_root = root / SNAPSHOT_ID
    bundle_root.mkdir(parents=True, exist_ok=True)

    policy_a = build_modern_policy_fixture(
        settings=[
            build_simple_modern_setting("def-A"),
            build_simple_modern_setting("def-B"),
        ],
        setting_definitions={
            "def-A": build_definition_object("def-A"),
            "def-B": build_definition_object("def-B"),
        },
        setting_definitions_retrieval={
            "status": "success",
            "count": 2,
            "requestedCount": 2,
            "foundCount": 2,
            "missingCount": 0,
            "error": None,
        },
        policy_id="policy-a",
    )
    policy_a["policy"]["name"] = "Policy A"

    policy_b = build_modern_policy_fixture(
        settings=[
            build_simple_modern_setting("def-B", setting_id="setting-b-shared"),
            build_simple_modern_setting("def-C", setting_id="setting-c"),
        ],
        setting_definitions={
            "def-B": build_definition_object("def-B"),
            "def-C": build_definition_object("def-C"),
        },
        setting_definitions_retrieval={
            "status": "success",
            "count": 2,
            "requestedCount": 2,
            "foundCount": 2,
            "missingCount": 0,
            "error": None,
        },
        policy_id="policy-b",
    )
    policy_b["policy"]["name"] = "Policy B"

    rel_a = "Windows/Modern/Policy A__policy-a.json"
    rel_b = "Windows/Modern/Policy B__policy-b.json"
    _write_json(bundle_root / rel_a, policy_a)
    _write_json(bundle_root / rel_b, policy_b)

    inventory_rows = [
        _inventory_row(
            PolicyName="Policy A",
            PolicyId="policy-a",
            JsonRelativePath=rel_a,
            SettingCount="2",
            RetrievedSettingCount="2",
        ),
        _inventory_row(
            PolicyName="Policy B",
            PolicyId="policy-b",
            JsonRelativePath=rel_b,
            SettingCount="2",
            RetrievedSettingCount="2",
        ),
    ]
    write_inventory_csv(bundle_root / "inventory.csv", inventory_rows)
    write_inventory_csv(root / f"{SNAPSHOT_ID}.csv", inventory_rows)

    manifest = {
        "snapshotSchemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportStatus": EXPORT_STATUS_COMPLETE,
        "inventoryRelativePath": "inventory.csv",
        "policyCount": 2,
        "settingDefinitionRequests": 3,
        "settingDefinitionsFound": 3,
        "settingDefinitionsMissing": 0,
        "definitionRetrievalErrors": 0,
    }
    _write_json(bundle_root / "snapshot_manifest.json", manifest)
    return bundle_root


def build_orchestration_regression_bundle(root: Path) -> Path:
    """Synthetic 3 modern + 2 classic export shape from the real tenant regression."""
    bundle_root = root / SNAPSHOT_ID
    bundle_root.mkdir(parents=True, exist_ok=True)

    nested_setting = build_depth3_modern_setting_tree()
    nested_definition_ids = sorted(collect_recursive_definition_ids([nested_setting]))
    nested_definitions = {
        definition_id: build_definition_object(definition_id)
        for definition_id in nested_definition_ids
    }

    modern_policies = [
        (
            "macOS/Modern/Settings catalog Modern__policy-modern-macos-001.json",
            build_modern_policy_fixture(
                settings=[nested_setting],
                setting_definitions=nested_definitions,
                setting_definitions_retrieval={
                    "status": "success",
                    "count": len(nested_definitions),
                    "requestedCount": len(nested_definitions),
                    "foundCount": len(nested_definitions),
                    "missingCount": 0,
                    "error": None,
                },
                policy_id="policy-modern-macos-001",
                platform="macOS",
                policy_type="Settings catalog / Modern",
                policy_name="Settings catalog Modern",
                platforms_raw="macOS",
                technologies="mdm,appleRemoteManagement",
                include_template_reference=False,
            ),
            _inventory_row(
                Platform="macOS",
                PolicyType="Settings catalog / Modern",
                PolicyName="Settings catalog Modern",
                PolicyId="policy-modern-macos-001",
                PlatformsRaw="macOS",
                Technologies="mdm,appleRemoteManagement",
                TemplateFamily="",
                TemplateDisplayName="",
                TemplateDisplayVersion="",
                SettingCount="1",
                RetrievedSettingCount="1",
                JsonRelativePath="macOS/Modern/Settings catalog Modern__policy-modern-macos-001.json",
            ),
        ),
        (
            "Windows/Modern/Modern Policy Two__policy-modern-002.json",
            build_modern_policy_fixture(
                settings=[build_simple_modern_setting("def-modern-002")],
                setting_definitions={"def-modern-002": build_definition_object("def-modern-002")},
                policy_id="policy-modern-002",
                policy_name="Modern Policy Two",
            ),
            _inventory_row(
                PolicyName="Modern Policy Two",
                PolicyId="policy-modern-002",
                JsonRelativePath="Windows/Modern/Modern Policy Two__policy-modern-002.json",
            ),
        ),
        (
            "Windows/Modern/Modern Policy Three__policy-modern-003.json",
            build_modern_policy_fixture(
                settings=[build_simple_modern_setting("def-modern-003")],
                setting_definitions={"def-modern-003": build_definition_object("def-modern-003")},
                policy_id="policy-modern-003",
                policy_name="Modern Policy Three",
            ),
            _inventory_row(
                PolicyName="Modern Policy Three",
                PolicyId="policy-modern-003",
                JsonRelativePath="Windows/Modern/Modern Policy Three__policy-modern-003.json",
            ),
        ),
    ]

    classic_ios = {
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportSource": "deviceConfigurations",
        "platform": "iOS/iPadOS",
        "policyType": "Device restrictions",
        "retrieval": {
            "policyDetail": {"status": "success", "count": 1, "error": None},
            "settings": {"status": "not_applicable", "count": 0, "error": None},
            "assignments": {"status": "success", "count": 0, "error": None},
            "settingDefinitions": {"status": "not_applicable", "count": 0, "error": None},
        },
        "policy": {
            "@odata.type": "#microsoft.graph.iosGeneralDeviceConfiguration",
            "id": "policy-classic-ios-001",
            "displayName": "iOS Device restrictions",
            "description": "",
            "version": 1,
            "roleScopeTagIds": ["0"],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
        },
        "assignments": [],
    }
    classic_windows = {
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportSource": "deviceConfigurations",
        "platform": "Windows",
        "policyType": "Device restrictions",
        "retrieval": {
            "policyDetail": {"status": "success", "count": 1, "error": None},
            "settings": {"status": "not_applicable", "count": 0, "error": None},
            "assignments": {"status": "success", "count": 0, "error": None},
            "settingDefinitions": {"status": "not_applicable", "count": 0, "error": None},
        },
        "policy": {
            "@odata.type": "#microsoft.graph.windows10GeneralConfiguration",
            "id": "policy-classic-windows-001",
            "displayName": "Windows Device restrictions",
            "description": "",
            "version": 1,
            "roleScopeTagIds": ["0"],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
        },
        "assignments": [],
    }

    classic_policies = [
        (
            "iOS-iPadOS/Classic/iOS Device restrictions__policy-classic-ios-001.json",
            classic_ios,
            _inventory_row(
                Platform="iOS/iPadOS",
                PolicyType="Device restrictions",
                Source="Classic",
                PolicyName="iOS Device restrictions",
                PolicyId="policy-classic-ios-001",
                ODataType="#microsoft.graph.iosGeneralDeviceConfiguration",
                PlatformsRaw="",
                Technologies="",
                TemplateFamily="",
                TemplateDisplayName="",
                TemplateDisplayVersion="",
                SettingCount="",
                RetrievedSettingCount="",
                AssignmentCount="0",
                AssignmentTargets="",
                IsAssigned="False",
                JsonRelativePath="iOS-iPadOS/Classic/iOS Device restrictions__policy-classic-ios-001.json",
                DefinitionsRetrievalStatus="not_applicable",
                SettingsRetrievalStatus="not_applicable",
            ),
        ),
        (
            "Windows/Classic/Windows Device restrictions__policy-classic-windows-001.json",
            classic_windows,
            _inventory_row(
                Platform="Windows",
                PolicyType="Device restrictions",
                Source="Classic",
                PolicyName="Windows Device restrictions",
                PolicyId="policy-classic-windows-001",
                ODataType="#microsoft.graph.windows10GeneralConfiguration",
                PlatformsRaw="",
                Technologies="",
                TemplateFamily="",
                TemplateDisplayName="",
                TemplateDisplayVersion="",
                SettingCount="",
                RetrievedSettingCount="",
                AssignmentCount="0",
                AssignmentTargets="",
                IsAssigned="False",
                JsonRelativePath="Windows/Classic/Windows Device restrictions__policy-classic-windows-001.json",
                DefinitionsRetrievalStatus="not_applicable",
                SettingsRetrievalStatus="not_applicable",
            ),
        ),
    ]

    inventory_rows: list[dict[str, str]] = []
    for relative_path, payload, row in modern_policies + classic_policies:
        _write_json(bundle_root / relative_path, payload)
        inventory_rows.append(row)

    inventory_rows.sort(key=lambda item: (item["Platform"], item["PolicyType"], item["PolicyName"]))
    write_inventory_csv(bundle_root / "inventory.csv", inventory_rows)
    write_inventory_csv(root / f"{SNAPSHOT_ID}.csv", inventory_rows)

    manifest = {
        "snapshotSchemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
        "snapshotId": SNAPSHOT_ID,
        "capturedAtUtc": CAPTURED_AT_UTC,
        "exportStatus": EXPORT_STATUS_COMPLETE,
        "inventoryRelativePath": "inventory.csv",
        "policyCount": 5,
        "sourceCoverage": {
            "modern": {
                "status": "success",
                "count": 3,
                "exportedCount": 3,
                "processingErrors": 0,
                "error": None,
            },
            "classic": {
                "status": "success",
                "count": 2,
                "exportedCount": 2,
                "processingErrors": 0,
                "error": None,
            },
            "administrativeTemplates": {
                "status": "skipped_by_option",
                "count": 0,
                "exportedCount": 0,
                "processingErrors": 0,
                "error": None,
            },
            "assignmentFilters": {"status": "success", "count": 0, "error": None},
        },
        "batchHttpRequestCount": 1,
        "batchItemCount": len(nested_definition_ids),
        "settingDefinitionRequests": len(nested_definition_ids),
        "settingDefinitionsFound": len(nested_definition_ids),
        "settingDefinitionsMissing": 0,
    }
    _write_json(bundle_root / "snapshot_manifest.json", manifest)
    return bundle_root


def build_inventory_only_bundle(root: Path, inventory_rows: list[dict[str, str]]) -> Path:
    """Build a minimal bundle with inventory/anchor only (for CSV contract tests)."""
    bundle_root = root / SNAPSHOT_ID
    bundle_root.mkdir(parents=True, exist_ok=True)
    write_inventory_csv(bundle_root / "inventory.csv", inventory_rows)
    write_inventory_csv(root / f"{SNAPSHOT_ID}.csv", inventory_rows)
    return bundle_root


def build_incomplete_bundle(root: Path) -> Path:
    """Simulate a failed export that wrote early artifacts but not a complete manifest."""
    bundle_root = build_inventory_only_bundle(root, [])
    _write_json(
        bundle_root / "assignment_filters.json",
        {
            "snapshotId": SNAPSHOT_ID,
            "capturedAtUtc": CAPTURED_AT_UTC,
            "retrieval": {"status": "success", "count": 0, "error": None},
            "assignmentFilters": [],
        },
    )
    _write_json(
        bundle_root / "snapshot_manifest.json",
        {
            "snapshotSchemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
            "snapshotId": SNAPSHOT_ID,
            "capturedAtUtc": CAPTURED_AT_UTC,
            "inventoryRelativePath": "inventory.csv",
            "policyCount": 0,
        },
    )
    return bundle_root
