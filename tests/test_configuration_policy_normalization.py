"""Phase 1 tests for Configuration Policy semantic normalization."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from diffasaurus.core.configuration_policies.canonical import SEMANTIC_PAYLOAD_VERSION
from diffasaurus.core.configuration_policies.normalizer import (
    build_policy_key,
    normalize_bundle,
    normalize_policy_document,
    summarize_normalized_snapshot,
)
from tests.fixtures.configuration_policy_bundle import (
    CAPTURED_AT_UTC,
    POLICY_EXPORT_SCHEMA_VERSION,
    SNAPSHOT_ID,
    SNAPSHOT_SCHEMA_VERSION,
    build_definition_object,
    build_modern_policy_fixture,
    build_simple_modern_setting,
    build_synthetic_bundle,
    _write_json,
)
from tools.configuration_policy_inventory import (
    EXPORT_STATUS_COMPLETE,
    EXPORT_STATUS_INCOMPLETE,
    EXPORT_STATUS_INTEGRITY_ERROR,
    write_inventory_csv,
)

REAL_BUNDLE_PATH = Path("/tmp/diffasaurus-policy-phase0/Intune_ConfigurationPolicies_20260813-124328")


def _build_source_trust_bundle(root: Path, export_status: str) -> Path:
    bundle_root = root / SNAPSHOT_ID
    bundle_root.mkdir(parents=True, exist_ok=True)

    policy = build_modern_policy_fixture(
        settings=[build_simple_modern_setting("def-trust-001")],
        setting_definitions={"def-trust-001": build_definition_object("def-trust-001")},
        policy_id="policy-trust-001",
        policy_name="Trust Policy",
    )
    json_rel = "Windows/Modern/Trust Policy__policy-trust-001.json"
    _write_json(bundle_root / json_rel, policy)
    write_inventory_csv(
        bundle_root / "inventory.csv",
        [
            {
                "SnapshotId": SNAPSHOT_ID,
                "CapturedAtUtc": CAPTURED_AT_UTC,
                "Platform": "Windows",
                "PolicyType": "Settings catalog",
                "Source": "Modern",
                "PolicyName": "Trust Policy",
                "Description": "",
                "PolicyId": "policy-trust-001",
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
                "JsonRelativePath": json_rel,
                "RetrievalStatus": "success",
                "SettingsRetrievalStatus": "success",
                "AssignmentsRetrievalStatus": "success",
                "DefinitionsRetrievalStatus": "success",
            }
        ],
    )
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
            "exportStatus": export_status,
            "inventoryRelativePath": "inventory.csv",
            "assignmentFiltersRelativePath": "assignment_filters.json",
            "policyCount": 1,
        },
    )
    return bundle_root


def _modern_policy_hash(policy_doc: dict[str, object]) -> str:
    return normalize_policy_document(policy_doc).semantic_hash


def _modern_setting_hash(policy_doc: dict[str, object], definition_id: str) -> str:
    normalized = normalize_policy_document(policy_doc)
    for node in normalized.settings.get("nodes", []):
        if node.get("definitionId") == definition_id:
            return str(node.get("semanticHash", ""))
    raise AssertionError(f"setting not found: {definition_id}")


def _base_modern_policy(**overrides: object) -> dict[str, object]:
    policy = build_modern_policy_fixture(
        settings=[
            {
                "id": "setting-simple",
                "settingInstance": {
                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                    "settingDefinitionId": "def-simple-hash",
                    "simpleSettingValue": {"value": "enabled"},
                },
            }
        ],
        setting_definitions={
            "def-simple-hash": build_definition_object(
                "def-simple-hash",
                display_name="Simple setting label",
            ),
        },
        policy_id="policy-hash-001",
        policy_name="Original Name",
    )
    policy["policy"]["description"] = "Original description"
    policy.update(overrides)
    return policy


class ConfigurationPolicyNormalizationSchemaTests(unittest.TestCase):
    def test_normalized_snapshot_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_synthetic_bundle(Path(tmp))
            snapshot = normalize_bundle(bundle)
            payload = snapshot.to_dict()
            self.assertEqual(payload["normalizationSchemaVersion"], 1)
            self.assertEqual(payload["semanticPayloadVersion"] if "semanticPayloadVersion" in payload else 1, 1)
            self.assertIn("policies", payload)
            self.assertIn("assignmentFilters", payload)


class ConfigurationPolicyIdentityTests(unittest.TestCase):
    def test_policy_key_uses_export_source_and_policy_id(self):
        policy = _base_modern_policy()
        normalized = normalize_policy_document(policy)
        self.assertEqual(
            normalized.policy_key,
            build_policy_key("configurationPolicies", "policy-hash-001"),
        )

    def test_rename_preserves_policy_key(self):
        policy_a = _base_modern_policy()
        policy_b = copy.deepcopy(policy_a)
        policy_b["policy"]["name"] = "Renamed Policy"
        key_a = normalize_policy_document(policy_a).policy_key
        key_b = normalize_policy_document(policy_b).policy_key
        self.assertEqual(key_a, key_b)


class ConfigurationPolicyHashInvariantTests(unittest.TestCase):
    def test_a_policy_rename_changes_hash(self):
        original = _base_modern_policy()
        renamed = copy.deepcopy(original)
        renamed["policy"]["name"] = "Renamed Policy"
        self.assertNotEqual(_modern_policy_hash(original), _modern_policy_hash(renamed))

    def test_b_description_change_changes_hash(self):
        original = _base_modern_policy()
        changed = copy.deepcopy(original)
        changed["policy"]["description"] = "Changed description"
        self.assertNotEqual(_modern_policy_hash(original), _modern_policy_hash(changed))

    def test_c_last_modified_only_unchanged(self):
        original = _base_modern_policy()
        changed = copy.deepcopy(original)
        changed["policy"]["lastModifiedDateTime"] = "2099-02-01T00:00:00.0000000Z"
        self.assertEqual(_modern_policy_hash(original), _modern_policy_hash(changed))

    def test_d_created_datetime_only_unchanged(self):
        original = _base_modern_policy()
        changed = copy.deepcopy(original)
        changed["policy"]["createdDateTime"] = "2098-01-01T00:00:00.0000000Z"
        self.assertEqual(_modern_policy_hash(original), _modern_policy_hash(changed))

    def test_e_top_level_setting_order_unchanged(self):
        setting_a = {
            "id": "setting-a",
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                "settingDefinitionId": "def-a",
                "simpleSettingValue": {"value": "a"},
            },
        }
        setting_b = {
            "id": "setting-b",
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                "settingDefinitionId": "def-b",
                "simpleSettingValue": {"value": "b"},
            },
        }
        policy_one = build_modern_policy_fixture(
            settings=[setting_a, setting_b],
            setting_definitions={
                "def-a": build_definition_object("def-a"),
                "def-b": build_definition_object("def-b"),
            },
        )
        policy_two = copy.deepcopy(policy_one)
        policy_two["settings"] = [setting_b, setting_a]
        self.assertEqual(_modern_policy_hash(policy_one), _modern_policy_hash(policy_two))

    def test_f_sibling_child_order_unchanged(self):
        child_a = {
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                "settingDefinitionId": "def-child-a",
                "simpleSettingValue": {"value": "a"},
            }
        }
        child_b = {
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                "settingDefinitionId": "def-child-b",
                "simpleSettingValue": {"value": "b"},
            }
        }
        group_setting = {
            "id": "setting-group",
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
                "settingDefinitionId": "def-group",
                "groupSettingCollectionValue": [{"children": [child_a, child_b]}],
            },
        }
        policy_one = build_modern_policy_fixture(
            settings=[group_setting],
            setting_definitions={
                "def-group": build_definition_object(
                    "def-group",
                    odata_type="#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionDefinition",
                ),
                "def-child-a": build_definition_object("def-child-a"),
                "def-child-b": build_definition_object("def-child-b"),
            },
        )
        policy_two = copy.deepcopy(policy_one)
        policy_two["settings"][0]["settingInstance"]["groupSettingCollectionValue"][0]["children"] = [
            child_b,
            child_a,
        ]
        self.assertEqual(_modern_policy_hash(policy_one), _modern_policy_hash(policy_two))

    def test_g_assignment_order_unchanged(self):
        assignment_a = {
            "target": {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": "group-a",
            }
        }
        assignment_b = {
            "target": {
                "@odata.type": "#microsoft.graph.allDevicesAssignmentTarget",
            }
        }
        policy_one = _base_modern_policy()
        policy_one["assignments"] = [assignment_a, assignment_b]
        policy_two = copy.deepcopy(policy_one)
        policy_two["assignments"] = [assignment_b, assignment_a]
        self.assertEqual(_modern_policy_hash(policy_one), _modern_policy_hash(policy_two))

    def test_h_scope_tag_order_unchanged(self):
        policy_one = _base_modern_policy()
        policy_one["policy"]["roleScopeTagIds"] = ["tag-b", "tag-a"]
        policy_two = copy.deepcopy(policy_one)
        policy_two["policy"]["roleScopeTagIds"] = ["tag-a", "tag-b"]
        self.assertEqual(_modern_policy_hash(policy_one), _modern_policy_hash(policy_two))

    def test_i_simple_value_change_changes_hash(self):
        original = _base_modern_policy()
        changed = copy.deepcopy(original)
        changed["settings"][0]["settingInstance"]["simpleSettingValue"]["value"] = "disabled"
        self.assertNotEqual(_modern_policy_hash(original), _modern_policy_hash(changed))
        self.assertNotEqual(
            _modern_setting_hash(original, "def-simple-hash"),
            _modern_setting_hash(changed, "def-simple-hash"),
        )

    def test_j_choice_option_id_change_changes_hash(self):
        policy = build_modern_policy_fixture(
            settings=[
                {
                    "id": "setting-choice",
                    "settingInstance": {
                        "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
                        "settingDefinitionId": "def-choice",
                        "choiceSettingValue": {"value": "option-a", "children": []},
                    },
                }
            ],
            setting_definitions={
                "def-choice": {
                    **build_definition_object(
                        "def-choice",
                        odata_type="#microsoft.graph.deviceManagementConfigurationChoiceSettingDefinition",
                    ),
                    "options": [
                        {"itemId": "option-a", "displayName": "Option A"},
                        {"itemId": "option-b", "displayName": "Option B"},
                    ],
                }
            },
        )
        changed = copy.deepcopy(policy)
        changed["settings"][0]["settingInstance"]["choiceSettingValue"]["value"] = "option-b"
        self.assertNotEqual(_modern_policy_hash(policy), _modern_policy_hash(changed))

    def test_k_choice_option_display_label_only_unchanged(self):
        policy = build_modern_policy_fixture(
            settings=[
                {
                    "id": "setting-choice",
                    "settingInstance": {
                        "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
                        "settingDefinitionId": "def-choice",
                        "choiceSettingValue": {"value": "option-a", "children": []},
                    },
                }
            ],
            setting_definitions={
                "def-choice": {
                    **build_definition_object(
                        "def-choice",
                        odata_type="#microsoft.graph.deviceManagementConfigurationChoiceSettingDefinition",
                    ),
                    "options": [
                        {"itemId": "option-a", "displayName": "Option A"},
                    ],
                }
            },
        )
        changed = copy.deepcopy(policy)
        changed["settingDefinitions"]["def-choice"]["options"][0]["displayName"] = "Renamed Option A"
        self.assertEqual(_modern_policy_hash(policy), _modern_policy_hash(changed))

    def test_l_definition_presentation_only_unchanged(self):
        original = _base_modern_policy()
        changed = copy.deepcopy(original)
        changed["settingDefinitions"]["def-simple-hash"]["displayName"] = "New label"
        changed["settingDefinitions"]["def-simple-hash"]["description"] = "New description"
        changed["settingDefinitions"]["def-simple-hash"]["helpText"] = "New help"
        self.assertEqual(_modern_policy_hash(original), _modern_policy_hash(changed))

    def test_m_group_nested_child_change_changes_hash(self):
        group_setting = {
            "id": "setting-group",
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
                "settingDefinitionId": "def-group",
                "groupSettingCollectionValue": [
                    {
                        "children": [
                            {
                                "settingInstance": {
                                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                                    "settingDefinitionId": "def-child",
                                    "simpleSettingValue": {"value": "one"},
                                }
                            }
                        ]
                    }
                ],
            },
        }
        policy = build_modern_policy_fixture(
            settings=[group_setting],
            setting_definitions={
                "def-group": build_definition_object(
                    "def-group",
                    odata_type="#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionDefinition",
                ),
                "def-child": build_definition_object("def-child"),
            },
        )
        changed = copy.deepcopy(policy)
        changed["settings"][0]["settingInstance"]["groupSettingCollectionValue"][0]["children"][0][
            "settingInstance"
        ]["simpleSettingValue"]["value"] = "two"
        self.assertNotEqual(
            _modern_setting_hash(policy, "def-group"),
            _modern_setting_hash(changed, "def-group"),
        )
        self.assertNotEqual(_modern_policy_hash(policy), _modern_policy_hash(changed))

    def test_n_retrieval_counters_only_unchanged(self):
        original = _base_modern_policy()
        changed = copy.deepcopy(original)
        changed["retrieval"]["settings"]["count"] = 99
        changed["retrieval"]["settingDefinitions"]["requestedCount"] = 99
        changed["capturedAtUtc"] = "2099-12-31T00:00:00.0000000Z"
        self.assertEqual(_modern_policy_hash(original), _modern_policy_hash(changed))


class ConfigurationPolicyClassicNormalizationTests(unittest.TestCase):
    def _classic_policy(self, **policy_props: object) -> dict[str, object]:
        body = {
            "@odata.type": "#microsoft.graph.iosGeneralDeviceConfiguration",
            "id": "policy-classic-test",
            "displayName": "Classic Policy",
            "description": "Classic description",
            "version": 1,
            "roleScopeTagIds": ["tag-1"],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
            "accountBlockModification": True,
            "appStoreBlocked": False,
            "emptyList": [],
            "emptyObject": {},
            "nullValue": None,
            "zeroValue": 0,
            "emptyString": "",
            "tags": ["alpha", "beta"],
        }
        body.update(policy_props)
        return {
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
            "policy": body,
            "assignments": [],
        }

    def test_false_and_zero_retained(self):
        normalized = normalize_policy_document(self._classic_policy())
        properties = {
            item["propertyPath"]: item["rawValue"]
            for item in normalized.settings["properties"]
        }
        self.assertFalse(properties["appStoreBlocked"])
        self.assertEqual(properties["zeroValue"], 0)

    def test_null_empty_omitted(self):
        normalized = normalize_policy_document(self._classic_policy())
        paths = {item["propertyPath"] for item in normalized.settings["properties"]}
        self.assertNotIn("nullValue", paths)
        self.assertNotIn("emptyList", paths)
        self.assertNotIn("emptyObject", paths)

    def test_metadata_excluded_and_scope_tags_separate(self):
        normalized = normalize_policy_document(self._classic_policy())
        paths = {item["propertyPath"] for item in normalized.settings["properties"]}
        self.assertNotIn("displayName", paths)
        self.assertNotIn("roleScopeTagIds", paths)
        self.assertEqual(normalized.semantic_metadata["roleScopeTagIds"], ["tag-1"])

    def test_classic_explicitness_unknown(self):
        normalized = normalize_policy_document(self._classic_policy())
        self.assertEqual(normalized.classic_explicitness, "unknown")
        self.assertIn("classic_explicitness_unknown", normalized.coverage.normalization_warnings)

    def test_property_order_does_not_affect_hash(self):
        policy_one = self._classic_policy()
        policy_two = copy.deepcopy(policy_one)
        reordered = {
            "tags": ["alpha", "beta"],
            "zeroValue": 0,
            "emptyString": "",
            "accountBlockModification": True,
            "appStoreBlocked": False,
            "emptyList": [],
            "emptyObject": {},
            "nullValue": None,
            "@odata.type": "#microsoft.graph.iosGeneralDeviceConfiguration",
            "id": "policy-classic-test",
            "displayName": "Classic Policy",
            "description": "Classic description",
            "version": 1,
            "roleScopeTagIds": ["tag-1"],
            "createdDateTime": CAPTURED_AT_UTC,
            "lastModifiedDateTime": CAPTURED_AT_UTC,
        }
        policy_two["policy"] = reordered
        self.assertEqual(
            normalize_policy_document(policy_one).semantic_hash,
            normalize_policy_document(policy_two).semantic_hash,
        )

    def test_property_value_change_changes_hash(self):
        original = self._classic_policy()
        changed = copy.deepcopy(original)
        changed["policy"]["accountBlockModification"] = False
        self.assertNotEqual(
            normalize_policy_document(original).semantic_hash,
            normalize_policy_document(changed).semantic_hash,
        )


class ConfigurationPolicyAdmxNormalizationTests(unittest.TestCase):
    def _admx_policy(self, **overrides: object) -> dict[str, object]:
        policy = {
            "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
            "snapshotId": SNAPSHOT_ID,
            "capturedAtUtc": CAPTURED_AT_UTC,
            "exportSource": "groupPolicyConfigurations",
            "platform": "Windows",
            "policyType": "Administrative Templates / ADMX",
            "retrieval": {
                "policyDetail": {"status": "success", "count": 1, "error": None},
                "definitionValues": {"status": "success", "count": 1, "error": None},
                "presentationValues": {"status": "success", "count": 1, "error": None},
                "assignments": {"status": "success", "count": 0, "error": None},
            },
            "policy": {
                "@odata.type": "#microsoft.graph.groupPolicyConfiguration",
                "id": "policy-admx-test",
                "displayName": "ADMX Policy",
                "description": "",
            },
            "definitionValues": [
                {
                    "id": "def-value-001",
                    "enabled": True,
                    "definition": {
                        "@odata.type": "#microsoft.graph.groupPolicyDefinition",
                        "id": "admx-def-001",
                        "displayName": "ADMX definition label",
                    },
                    "presentationValues": [
                        {
                            "@odata.type": "#microsoft.graph.groupPolicyPresentationValueText",
                            "value": "configured-text",
                        }
                    ],
                    "presentationRetrieval": {"status": "success", "count": 1, "error": None},
                }
            ],
            "assignments": [],
        }
        policy.update(overrides)
        return policy

    def test_enabled_and_presentation_values_normalized(self):
        normalized = normalize_policy_document(self._admx_policy())
        setting = normalized.settings["settings"][0]
        self.assertTrue(setting["enabled"])
        self.assertEqual(setting["presentationValues"][0]["value"], "configured-text")

    def test_boolean_presentation_value(self):
        policy = self._admx_policy()
        policy["definitionValues"][0]["presentationValues"] = [
            {
                "@odata.type": "#microsoft.graph.groupPolicyPresentationValueBoolean",
                "value": False,
            }
        ]
        normalized = normalize_policy_document(policy)
        self.assertFalse(normalized.settings["settings"][0]["presentationValues"][0]["value"])

    def test_presentation_label_excluded_from_hash(self):
        original = self._admx_policy()
        changed = copy.deepcopy(original)
        changed["definitionValues"][0]["definition"]["displayName"] = "Renamed label"
        self.assertEqual(
            normalize_policy_document(original).semantic_hash,
            normalize_policy_document(changed).semantic_hash,
        )

    def test_presentation_value_change_changes_hash(self):
        original = self._admx_policy()
        changed = copy.deepcopy(original)
        changed["definitionValues"][0]["presentationValues"][0]["value"] = "changed-text"
        self.assertNotEqual(
            normalize_policy_document(original).semantic_hash,
            normalize_policy_document(changed).semantic_hash,
        )

    def test_partial_presentation_retrieval_blocks_hash(self):
        policy = self._admx_policy()
        policy["definitionValues"][0]["presentationValues"] = []
        policy["definitionValues"][0]["presentationRetrieval"] = {
            "status": "error",
            "count": 0,
            "error": "failed",
        }
        normalized = normalize_policy_document(policy)
        self.assertFalse(normalized.coverage.semantic_hash_eligible)
        self.assertIn("admx_presentation_values_unavailable", normalized.coverage.semantic_hash_blockers)

    def test_list_presentation_values_preserved(self):
        policy = self._admx_policy()
        policy["definitionValues"][0]["presentationValues"] = [
            {
                "@odata.type": "#microsoft.graph.groupPolicyPresentationValueList",
                "values": ["one", "two"],
            }
        ]
        normalized = normalize_policy_document(policy)
        payload = normalized.settings["settings"][0]["presentationValues"][0]
        self.assertEqual(payload["values"], ["one", "two"])

    def test_zero_presentation_values_with_success_is_valid(self):
        policy = self._admx_policy()
        policy["definitionValues"][0]["presentationValues"] = []
        policy["definitionValues"][0]["presentationRetrieval"] = {
            "status": "success",
            "count": 0,
            "error": None,
        }
        normalized = normalize_policy_document(policy)
        self.assertTrue(normalized.coverage.semantic_hash_eligible)


class ConfigurationPolicyAssignmentNormalizationTests(unittest.TestCase):
    def test_assignment_kinds(self):
        policy = _base_modern_policy()
        policy["assignments"] = [
            {"target": {"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"}},
            {"target": {"@odata.type": "#microsoft.graph.allLicensedUsersAssignmentTarget"}},
            {
                "target": {
                    "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                    "groupId": "group-include",
                    "displayName": "Include Group Label",
                    "deviceAndAppManagementAssignmentFilterId": "filter-1",
                    "deviceAndAppManagementAssignmentFilterType": "include",
                }
            },
            {
                "target": {
                    "@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget",
                    "groupId": "group-exclude",
                }
            },
        ]
        normalized = normalize_policy_document(policy)
        kinds = {assignment.target_kind for assignment in normalized.assignments}
        self.assertEqual(
            kinds,
            {"all_devices", "all_users", "include_group", "exclude_group"},
        )

    def test_group_display_name_does_not_affect_hash(self):
        policy = _base_modern_policy()
        policy["assignments"] = [
            {
                "target": {
                    "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                    "groupId": "group-1",
                    "displayName": "Label A",
                }
            }
        ]
        changed = copy.deepcopy(policy)
        changed["assignments"][0]["target"]["displayName"] = "Label B"
        self.assertEqual(_modern_policy_hash(policy), _modern_policy_hash(changed))

    def test_group_id_change_affects_hash(self):
        policy = _base_modern_policy()
        policy["assignments"] = [
            {
                "target": {
                    "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                    "groupId": "group-1",
                }
            }
        ]
        changed = copy.deepcopy(policy)
        changed["assignments"][0]["target"]["groupId"] = "group-2"
        self.assertNotEqual(_modern_policy_hash(policy), _modern_policy_hash(changed))

    def test_unknown_target_warning(self):
        policy = _base_modern_policy()
        policy["assignments"] = [{"target": {"@odata.type": "#microsoft.graph.futureAssignmentTarget"}}]
        normalized = normalize_policy_document(policy)
        self.assertEqual(normalized.assignments[0].target_kind, "unknown")
        self.assertIn("unknown_assignment_target", normalized.assignments[0].warnings)


class ConfigurationPolicyAssignmentFilterTests(unittest.TestCase):
    def test_filter_rule_change_changes_filter_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_synthetic_bundle(Path(tmp))
            snapshot_one = normalize_bundle(bundle)
            filter_hash_one = snapshot_one.assignment_filters[0].semantic_hash

            filters_path = bundle / "assignment_filters.json"
            filters = json.loads(filters_path.read_text(encoding="utf-8"))
            filters["assignmentFilters"][0]["rule"] = "device.model -eq 'Changed'"
            filters_path.write_text(json.dumps(filters, indent=2, sort_keys=True), encoding="utf-8")

            snapshot_two = normalize_bundle(bundle)
            filter_hash_two = snapshot_two.assignment_filters[0].semantic_hash
            self.assertNotEqual(filter_hash_one, filter_hash_two)

    def test_filter_display_name_only_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_synthetic_bundle(Path(tmp))
            snapshot_one = normalize_bundle(bundle)
            filter_hash_one = snapshot_one.assignment_filters[0].semantic_hash

            filters_path = bundle / "assignment_filters.json"
            filters = json.loads(filters_path.read_text(encoding="utf-8"))
            filters["assignmentFilters"][0]["displayName"] = "Renamed filter"
            filters_path.write_text(json.dumps(filters, indent=2, sort_keys=True), encoding="utf-8")

            snapshot_two = normalize_bundle(bundle)
            self.assertEqual(filter_hash_one, snapshot_two.assignment_filters[0].semantic_hash)


class ConfigurationPolicyUnknownModernTypeTests(unittest.TestCase):
    def test_unknown_modern_instance_preserved(self):
        policy = build_modern_policy_fixture(
            settings=[
                {
                    "id": "setting-future",
                    "settingInstance": {
                        "@odata.type": "#microsoft.graph.deviceManagementConfigurationFutureSettingInstance",
                        "settingDefinitionId": "def-future-001",
                        "customConfiguredValue": "alpha",
                    },
                }
            ],
            setting_definitions={
                "def-future-001": build_definition_object("def-future-001"),
            },
        )
        normalized = normalize_policy_document(policy)
        node = normalized.settings["nodes"][0]
        self.assertEqual(node["kind"], "unknown")
        self.assertIn("unknown_modern_setting_instance_type", node["warnings"])
        hash_before = normalized.semantic_hash

        changed = copy.deepcopy(policy)
        changed["settings"][0]["settingInstance"]["customConfiguredValue"] = "beta"
        hash_after = normalize_policy_document(changed).semantic_hash
        self.assertNotEqual(hash_before, hash_after)

        presentation_only = copy.deepcopy(policy)
        presentation_only["settingDefinitions"]["def-future-001"]["displayName"] = "Renamed"
        self.assertEqual(hash_before, normalize_policy_document(presentation_only).semantic_hash)


class ConfigurationPolicySourceSnapshotTrustTests(unittest.TestCase):
    def test_complete_source_with_clean_normalization_is_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _build_source_trust_bundle(Path(tmp), EXPORT_STATUS_COMPLETE)
            snapshot = normalize_bundle(bundle)
            self.assertEqual(snapshot.source_export_status, EXPORT_STATUS_COMPLETE)
            self.assertEqual(snapshot.normalization_status, "success")
            self.assertNotIn("source_export_incomplete", snapshot.normalization_warnings)

    def test_incomplete_source_is_never_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _build_source_trust_bundle(Path(tmp), EXPORT_STATUS_INCOMPLETE)
            snapshot = normalize_bundle(bundle)
            self.assertEqual(snapshot.source_export_status, EXPORT_STATUS_INCOMPLETE)
            self.assertEqual(snapshot.normalization_status, "partial")
            self.assertIn("source_export_incomplete", snapshot.normalization_warnings)

    def test_integrity_error_source_is_never_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _build_source_trust_bundle(Path(tmp), EXPORT_STATUS_INTEGRITY_ERROR)
            snapshot = normalize_bundle(bundle)
            self.assertEqual(snapshot.source_export_status, EXPORT_STATUS_INTEGRITY_ERROR)
            self.assertNotEqual(snapshot.normalization_status, "success")
            self.assertIn(snapshot.normalization_status, {"partial", "error"})
            self.assertIn("source_export_incomplete", snapshot.normalization_warnings)

    def test_incomplete_source_policy_may_remain_hash_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _build_source_trust_bundle(Path(tmp), EXPORT_STATUS_INCOMPLETE)
            snapshot = normalize_bundle(bundle)
            self.assertEqual(snapshot.normalization_status, "partial")
            self.assertEqual(len(snapshot.policies), 1)
            policy = snapshot.policies[0]
            self.assertTrue(policy.coverage.semantic_hash_eligible)
            self.assertTrue(policy.semantic_hash)

    def test_source_snapshot_warning_available_in_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _build_source_trust_bundle(Path(tmp), EXPORT_STATUS_INCOMPLETE)
            snapshot = normalize_bundle(bundle)
            summary = summarize_normalized_snapshot(snapshot)
            self.assertEqual(summary["normalizationStatus"], "partial")
            self.assertEqual(summary["sourceExportStatus"], EXPORT_STATUS_INCOMPLETE)
            self.assertIn("source_export_incomplete", summary["warningCategories"])


class ConfigurationPolicyDeterminismTests(unittest.TestCase):
    def test_repeat_normalization_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_synthetic_bundle(Path(tmp))
            first = normalize_bundle(bundle)
            second = normalize_bundle(bundle)
            first_payload = [policy.to_dict() for policy in first.policies]
            second_payload = [policy.to_dict() for policy in second.policies]
            self.assertEqual(first_payload, second_payload)
            self.assertEqual(
                [item.semantic_hash for item in first.assignment_filters],
                [item.semantic_hash for item in second.assignment_filters],
            )


@unittest.skipUnless(REAL_BUNDLE_PATH.exists(), "Real disposable Phase 0 bundle not present")
class ConfigurationPolicyRealBundleNormalizationTests(unittest.TestCase):
    def test_real_bundle_structural_summary(self):
        snapshot = normalize_bundle(REAL_BUNDLE_PATH)
        summary = summarize_normalized_snapshot(snapshot)

        self.assertEqual(summary["policyCount"], 5)
        self.assertEqual(summary["exportSourceCounts"].get("configurationPolicies"), 3)
        self.assertEqual(summary["exportSourceCounts"].get("deviceConfigurations"), 2)
        self.assertEqual(summary.get("admxSettingCount"), 0)
        self.assertEqual(summary["modernTopLevelSettingCount"], 15)
        self.assertEqual(summary["modernInstanceNodeCount"], 31)
        self.assertEqual(summary["modernKindCounts"].get("group_collection"), 3)
        self.assertEqual(summary["modernKindCounts"].get("choice"), 19)
        self.assertEqual(summary["modernKindCounts"].get("simple"), 9)
        self.assertGreaterEqual(summary["semanticHashEligiblePolicyCount"], 5)
        self.assertEqual(summary["assignmentFilterCount"], 0)

        first_hashes = [policy.semantic_hash for policy in snapshot.policies if policy.semantic_hash]
        second = normalize_bundle(REAL_BUNDLE_PATH)
        second_hashes = [policy.semantic_hash for policy in second.policies if policy.semantic_hash]
        self.assertEqual(first_hashes, second_hashes)
        self.assertGreater(summary["normalizationDurationSeconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
