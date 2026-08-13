"""Phase 2 tests for Configuration Policy semantic comparison."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from diffasaurus.core.configuration_policies.diff import (
    compare_normalized_snapshots,
    compare_policy_bundles,
    comparison_canonical_json,
    load_snapshot_comparison_context,
)
from diffasaurus.core.configuration_policies.normalizer import normalize_bundle
from tests.fixtures.configuration_policy_bundle import (
    CAPTURED_AT_UTC,
    POLICY_EXPORT_SCHEMA_VERSION,
    SNAPSHOT_ID,
    build_definition_object,
    build_modern_policy_fixture,
)
from tests.fixtures.configuration_policy_comparison import (
    DEFAULT_SOURCE_COVERAGE,
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)

REAL_BUNDLE_PATH = Path("/tmp/diffasaurus-policy-phase0/Intune_ConfigurationPolicies_20260813-124328")
BASELINE_ID = "Intune_ConfigurationPolicies_20990101-120000"
TARGET_ID = "Intune_ConfigurationPolicies_20990102-120000"


def _bundle_pair(
    root: Path,
    *,
    baseline_doc: dict | None = None,
    target_doc: dict | None = None,
    baseline_coverage: dict | None = None,
    target_coverage: dict | None = None,
    baseline_status: str = "complete",
    target_status: str = "complete",
) -> tuple[Path, Path]:
    policy_id = "policy-modern-001"
    rel = f"Windows/Modern/Synthetic Policy__{policy_id}.json"
    baseline_doc = baseline_doc or build_basic_modern_policy_document(policy_id=policy_id)
    target_doc = target_doc or copy.deepcopy(baseline_doc)
    row = build_modern_inventory_row(
        policy_id=policy_id,
        policy_name="Synthetic Policy",
        json_relative_path=rel,
    )
    baseline = build_comparison_bundle(
        root,
        snapshot_id=BASELINE_ID,
        captured_at_utc="2099-01-01T12:00:00.0000000Z",
        policies=[(rel, baseline_doc, row)],
        export_status=baseline_status,
        source_coverage=baseline_coverage or DEFAULT_SOURCE_COVERAGE,
    )
    target = build_comparison_bundle(
        root,
        snapshot_id=TARGET_ID,
        captured_at_utc="2099-01-02T12:00:00.0000000Z",
        policies=[(rel, target_doc, row)],
        export_status=target_status,
        source_coverage=target_coverage or DEFAULT_SOURCE_COVERAGE,
    )
    return baseline, target


def _event_types(comparison) -> set[str]:
    return {event.event_type for event in comparison.changes}


def _admx_policy_document(*, enabled: bool = True, presentation_value: str = "configured-text") -> dict:
    return {
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
            "id": "policy-admx-001",
            "displayName": "ADMX Policy",
            "description": "",
        },
        "definitionValues": [
            {
                "id": "def-value-001",
                "enabled": enabled,
                "definition": {
                    "@odata.type": "#microsoft.graph.groupPolicyDefinition",
                    "id": "admx-def-001",
                    "displayName": "ADMX definition label",
                },
                "presentationValues": [
                    {
                        "@odata.type": "#microsoft.graph.groupPolicyPresentationValueText",
                        "value": presentation_value,
                    }
                ],
                "presentationRetrieval": {"status": "success", "count": 1, "error": None},
            }
        ],
        "assignments": [],
    }


def _admx_bundle_pair(
    root: Path,
    *,
    baseline_doc: dict,
    target_doc: dict,
) -> tuple[Path, Path]:
    rel = "Windows/AdministrativeTemplates/ADMX__policy-admx-001.json"
    row = build_modern_inventory_row(
        policy_id="policy-admx-001",
        policy_name="ADMX Policy",
        json_relative_path=rel,
    )
    row.update(
        {
            "Platform": "Windows",
            "Source": "AdministrativeTemplate",
            "PolicyType": "Administrative Templates / ADMX",
            "ODataType": "#microsoft.graph.groupPolicyConfiguration",
        }
    )
    coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
    coverage["administrativeTemplates"] = {
        "status": "success",
        "count": 1,
        "exportedCount": 1,
        "processingErrors": 0,
    }
    baseline = build_comparison_bundle(
        root,
        snapshot_id=BASELINE_ID,
        captured_at_utc="2099-01-01T12:00:00.0000000Z",
        policies=[(rel, baseline_doc, row)],
        source_coverage=coverage,
    )
    target = build_comparison_bundle(
        root,
        snapshot_id=TARGET_ID,
        captured_at_utc="2099-01-02T12:00:00.0000000Z",
        policies=[(rel, target_doc, row)],
        source_coverage=coverage,
    )
    return baseline, target


class ConfigurationPolicyNoOpComparisonTests(unittest.TestCase):
    def test_semantically_identical_bundles_produce_zero_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_doc = build_basic_modern_policy_document(include_assignment=True)
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["settings"] = list(reversed(target_doc["settings"]))
            target_doc["assignments"] = list(reversed(target_doc["assignments"]))
            target_doc["settingDefinitions"]["def-choice-001"]["options"][0]["displayName"] = "Renamed Option"
            target_doc["settingDefinitions"]["def-simple-001"]["displayName"] = "Renamed Simple"
            baseline, target = _bundle_pair(root, baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(comparison.summary["policies"]["modified"], 0)
            self.assertEqual(len(comparison.changes), 0)
            self.assertEqual(comparison.comparison_status, "success")


class ConfigurationPolicyRenameAndDescriptionTests(unittest.TestCase):
    def test_policy_rename_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document(policy_name="Old Name")
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["policy"]["name"] = "New Name"
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(comparison.summary["policies"]["modified"], 1)
            self.assertEqual(_event_types(comparison), {"policy_renamed"})
            self.assertNotIn("policy_added", _event_types(comparison))
            self.assertNotIn("policy_removed", _event_types(comparison))

    def test_policy_description_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document()
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["policy"]["description"] = "Changed description"
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(_event_types(comparison), {"policy_description_changed"})


class ConfigurationPolicyAddRemoveCoverageTests(unittest.TestCase):
    def test_policy_added_and_removed_with_trustworthy_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel_a = "Windows/Modern/Policy A__policy-a.json"
            rel_b = "Windows/Modern/Policy B__policy-b.json"
            doc_a = build_basic_modern_policy_document(policy_id="policy-a", policy_name="Policy A")
            doc_b = build_basic_modern_policy_document(policy_id="policy-b", policy_name="Policy B")
            row_a = build_modern_inventory_row(policy_id="policy-a", policy_name="Policy A", json_relative_path=rel_a)
            row_b = build_modern_inventory_row(policy_id="policy-b", policy_name="Policy B", json_relative_path=rel_b)
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[(rel_a, doc_a, row_a)],
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel_b, doc_b, row_b)],
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(comparison.summary["policies"]["added"], 1)
            self.assertEqual(comparison.summary["policies"]["removed"], 1)
            self.assertIn("policy_added", _event_types(comparison))
            self.assertIn("policy_removed", _event_types(comparison))

    def test_baseline_source_error_suppresses_policy_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel_a = "Windows/Modern/Policy A__policy-a.json"
            rel_b = "Windows/Modern/Policy B__policy-b.json"
            doc_b = build_basic_modern_policy_document(policy_id="policy-b", policy_name="Policy B")
            row_b = build_modern_inventory_row(policy_id="policy-b", policy_name="Policy B", json_relative_path=rel_b)
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {"status": "error", "count": 0, "exportedCount": 0, "processingErrors": 1}
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[],
                source_coverage=coverage,
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel_b, doc_b, row_b)],
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertNotIn("policy_added", _event_types(comparison))
            self.assertGreater(comparison.summary["suppressionCount"], 0)

    def test_target_source_error_suppresses_policy_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel_a = "Windows/Modern/Policy A__policy-a.json"
            doc_a = build_basic_modern_policy_document(policy_id="policy-a", policy_name="Policy A")
            row_a = build_modern_inventory_row(policy_id="policy-a", policy_name="Policy A", json_relative_path=rel_a)
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {"status": "error", "count": 0, "exportedCount": 0, "processingErrors": 1}
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[(rel_a, doc_a, row_a)],
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[],
                source_coverage=coverage,
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertNotIn("policy_removed", _event_types(comparison))
            self.assertGreater(comparison.summary["suppressionCount"], 0)

    def test_known_zero_allows_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel_b = "Windows/Modern/Policy B__policy-b.json"
            doc_b = build_basic_modern_policy_document(policy_id="policy-b", policy_name="Policy B")
            row_b = build_modern_inventory_row(policy_id="policy-b", policy_name="Policy B", json_relative_path=rel_b)
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {"status": "success", "count": 0, "exportedCount": 0, "processingErrors": 0}
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[],
                source_coverage=coverage,
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel_b, doc_b, row_b)],
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertIn("policy_added", _event_types(comparison))


class ConfigurationPolicyModernSettingTests(unittest.TestCase):
    def test_simple_setting_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document(simple_value="30")
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["settings"][0]["settingInstance"]["simpleSettingValue"]["value"] = "60"
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertIn("setting_changed", _event_types(comparison))

    def test_choice_option_id_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document(choice_value="option-a")
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["settings"][1]["settingInstance"]["choiceSettingValue"]["value"] = "option-b"
            target_doc["settingDefinitions"]["def-choice-001"]["options"].append(
                {"itemId": "option-b", "displayName": "Option B"}
            )
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertIn("setting_changed", _event_types(comparison))

    def test_choice_display_label_only_no_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document()
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["settingDefinitions"]["def-choice-001"]["options"][0]["displayName"] = "Renamed"
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(len(comparison.changes), 0)

    def test_setting_added_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document()
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["settings"].append(
                {
                    "id": "setting-extra",
                    "settingInstance": {
                        "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                        "settingDefinitionId": "def-extra",
                        "simpleSettingValue": {"value": "x"},
                    },
                }
            )
            target_doc["settingDefinitions"]["def-extra"] = build_definition_object("def-extra")
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertIn("setting_added", _event_types(comparison))

    def test_group_collection_top_level_change_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document()
            baseline_doc["settings"] = [
                {
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
            ]
            baseline_doc["settingDefinitions"] = {
                "def-group": build_definition_object(
                    "def-group",
                    odata_type="#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionDefinition",
                ),
                "def-child": build_definition_object("def-child"),
            }
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["settings"][0]["settingInstance"]["groupSettingCollectionValue"][0]["children"][0][
                "settingInstance"
            ]["simpleSettingValue"]["value"] = "two"
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(_event_types(comparison), {"setting_changed"})
            self.assertEqual(
                sum(1 for event in comparison.changes if event.event_type == "setting_changed"),
                1,
            )


class ConfigurationPolicyClassicAdmxAssignmentTests(unittest.TestCase):
    def test_classic_property_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "iOS-iPadOS/Classic/Classic__policy-classic.json"
            baseline_doc = {
                "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
                "snapshotId": BASELINE_ID,
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
                    "id": "policy-classic",
                    "displayName": "Classic",
                    "description": "",
                    "appStoreBlocked": False,
                    "roleScopeTagIds": ["0"],
                },
                "assignments": [],
            }
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["policy"]["appStoreBlocked"] = True
            row = build_modern_inventory_row(
                policy_id="policy-classic",
                policy_name="Classic",
                json_relative_path=rel,
            )
            row.update(
                {
                    "Platform": "iOS/iPadOS",
                    "Source": "Classic",
                    "PolicyType": "Device restrictions",
                    "ODataType": "#microsoft.graph.iosGeneralDeviceConfiguration",
                }
            )
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["classic"] = {"status": "success", "count": 1, "exportedCount": 1, "processingErrors": 0}
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[(rel, baseline_doc, row)],
                source_coverage=coverage,
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel, target_doc, row)],
                source_coverage=coverage,
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertIn("classic_property_changed", _event_types(comparison))

    def test_assignment_added_removed_and_group_id_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document()
            baseline_doc["assignments"] = [
                {"target": {"@odata.type": "#microsoft.graph.allLicensedUsersAssignmentTarget"}},
                {
                    "target": {
                        "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                        "groupId": "group-1",
                        "displayName": "Label A",
                    }
                },
            ]
            baseline_doc["retrieval"]["assignments"] = {"status": "success", "count": 2, "error": None}
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["assignments"] = [
                {
                    "target": {
                        "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                        "groupId": "group-2",
                        "displayName": "Label B",
                    }
                },
                {"target": {"@odata.type": "#microsoft.graph.allLicensedUsersAssignmentTarget"}},
            ]
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertIn("assignment_added", _event_types(comparison))
            self.assertIn("assignment_removed", _event_types(comparison))
            self.assertNotIn("assignment_changed", _event_types(comparison))

    def test_assignment_filter_rule_change_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filters = {
                "snapshotId": BASELINE_ID,
                "capturedAtUtc": CAPTURED_AT_UTC,
                "retrieval": {"status": "success", "count": 1, "error": None},
                "assignmentFilters": [
                    {
                        "id": "filter-001",
                        "displayName": "Filter",
                        "rule": "device.manufacturer -eq 'A'",
                        "platform": "windows10AndLater",
                        "assignmentFilterManagementType": "devices",
                    }
                ],
            }
            baseline_doc = build_basic_modern_policy_document()
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc)
            build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[],
                assignment_filters=filters,
            )
            changed_filters = copy.deepcopy(filters)
            changed_filters["assignmentFilters"][0]["rule"] = "device.manufacturer -eq 'B'"
            build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[],
                assignment_filters=changed_filters,
            )
            comparison = compare_policy_bundles(root / BASELINE_ID, root / TARGET_ID)
            self.assertIn("assignment_filter_changed", _event_types(comparison))
            self.assertNotIn("assignment_added", _event_types(comparison))


class ConfigurationPolicyIndeterminateAndDeterminismTests(unittest.TestCase):
    def test_ineligible_policy_is_indeterminate_without_granular_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document()
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["retrieval"]["settings"] = {"status": "error", "count": 0, "error": "failed"}
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            policy_diff = comparison.policy_diffs[0]
            self.assertEqual(policy_diff.state, "indeterminate")
            self.assertNotIn("setting_removed", _event_types(comparison))

    def test_self_comparison_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline, _ = _bundle_pair(Path(tmp))
            comparison = compare_policy_bundles(baseline, baseline)
            self.assertEqual(comparison.summary["policies"]["unchanged"], 1)
            self.assertEqual(len(comparison.changes), 0)

    def test_deterministic_comparison_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document(simple_value="30")
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["settings"][0]["settingInstance"]["simpleSettingValue"]["value"] = "60"
            baseline, target = _bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            first = comparison_canonical_json(compare_policy_bundles(baseline, target))
            second = comparison_canonical_json(compare_policy_bundles(baseline, target))
            self.assertEqual(first, second)


@unittest.skipUnless(REAL_BUNDLE_PATH.exists(), "Real disposable Phase 0 bundle not present")
class ConfigurationPolicyRealBundleSelfComparisonTests(unittest.TestCase):
    def test_real_bundle_self_comparison(self):
        comparison = compare_policy_bundles(REAL_BUNDLE_PATH, REAL_BUNDLE_PATH)
        self.assertEqual(comparison.summary["policies"]["unchanged"], 5)
        self.assertEqual(comparison.summary["policies"]["modified"], 0)
        self.assertEqual(len(comparison.changes), 0)


class ConfigurationPolicyAcceptanceMatrixTests(unittest.TestCase):
    def test_admx_enabled_change_emits_admx_setting_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = _admx_policy_document(enabled=False)
            target_doc = _admx_policy_document(enabled=True)
            baseline, target = _admx_bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(_event_types(comparison), {"admx_setting_changed"})

    def test_admx_presentation_value_change_emits_admx_setting_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = _admx_policy_document(presentation_value="alpha")
            target_doc = _admx_policy_document(presentation_value="beta")
            baseline, target = _admx_bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertIn("admx_setting_changed", _event_types(comparison))

    def test_admx_presentation_label_only_no_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = _admx_policy_document()
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["definitionValues"][0]["definition"]["displayName"] = "Renamed ADMX label"
            baseline, target = _admx_bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(len(comparison.changes), 0)

    def test_admx_ineligible_side_is_indeterminate_without_fake_admx_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = _admx_policy_document()
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["definitionValues"][0]["presentationValues"] = []
            target_doc["definitionValues"][0]["presentationRetrieval"] = {
                "status": "error",
                "count": 0,
                "error": "failed",
            }
            baseline, target = _admx_bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            policy_diff = comparison.policy_diffs[0]
            self.assertEqual(policy_diff.state, "indeterminate")
            self.assertTrue(
                any(item.category == "policy_semantics_unavailable" for item in policy_diff.suppressions)
            )
            self.assertFalse(_event_types(comparison) & {"admx_setting_added", "admx_setting_removed", "admx_setting_changed"})

    def test_admx_known_zero_presentation_values_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = _admx_policy_document()
            target_doc = copy.deepcopy(baseline_doc)
            baseline_doc["definitionValues"][0]["presentationValues"] = []
            baseline_doc["definitionValues"][0]["presentationRetrieval"] = {
                "status": "success",
                "count": 0,
                "error": None,
            }
            target_doc["definitionValues"][0]["presentationValues"] = []
            target_doc["definitionValues"][0]["presentationRetrieval"] = {
                "status": "success",
                "count": 0,
                "error": None,
            }
            baseline, target = _admx_bundle_pair(Path(tmp), baseline_doc=baseline_doc, target_doc=target_doc)
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(comparison.summary["policies"]["unchanged"], 1)
            self.assertEqual(len(comparison.changes), 0)

    def test_unknown_modern_opaque_payload_unchanged_and_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_modern_policy_fixture(
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
                setting_definitions={"def-future-001": build_definition_object("def-future-001")},
                policy_id="policy-unknown-001",
            )
            target_same = copy.deepcopy(baseline_doc)
            target_changed = copy.deepcopy(baseline_doc)
            target_changed["settings"][0]["settingInstance"]["customConfiguredValue"] = "beta"
            rel = "Windows/Modern/Unknown__policy-unknown-001.json"
            row = build_modern_inventory_row(
                policy_id="policy-unknown-001",
                policy_name="Unknown",
                json_relative_path=rel,
            )
            baseline = build_comparison_bundle(
                Path(tmp),
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[(rel, baseline_doc, row)],
            )
            target_same_bundle = build_comparison_bundle(
                Path(tmp) / "same",
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel, target_same, row)],
            )
            target_changed_bundle = build_comparison_bundle(
                Path(tmp) / "changed",
                snapshot_id="Intune_ConfigurationPolicies_20990103-120000",
                captured_at_utc="2099-01-03T12:00:00.0000000Z",
                policies=[(rel, target_changed, row)],
            )
            same_comparison = compare_policy_bundles(baseline, target_same_bundle)
            self.assertEqual(len(same_comparison.changes), 0)
            changed_comparison = compare_policy_bundles(baseline, target_changed_bundle)
            self.assertEqual(_event_types(changed_comparison), {"setting_changed"})
            policy_diff = changed_comparison.policy_diffs[0]
            setting_events = [event for event in policy_diff.changes if event.event_type == "setting_changed"]
            self.assertTrue(any("unknown_modern_setting_instance_type" in event.warnings for event in setting_events))

    def test_scope_tags_reorder_no_event_and_id_change_emits_scope_tags_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_doc = build_basic_modern_policy_document()
            baseline_doc["policy"]["roleScopeTagIds"] = ["tag-b", "tag-a"]
            target_reorder = copy.deepcopy(baseline_doc)
            target_reorder["policy"]["roleScopeTagIds"] = ["tag-a", "tag-b"]
            baseline, target_reorder_bundle = _bundle_pair(
                Path(tmp),
                baseline_doc=baseline_doc,
                target_doc=target_reorder,
            )
            self.assertEqual(len(compare_policy_bundles(baseline, target_reorder_bundle).changes), 0)

            target_add = copy.deepcopy(baseline_doc)
            target_add["policy"]["roleScopeTagIds"] = ["tag-a", "tag-b", "tag-c"]
            _, target_add_bundle = _bundle_pair(
                Path(tmp) / "add",
                baseline_doc=baseline_doc,
                target_doc=target_add,
            )
            add_comparison = compare_policy_bundles(baseline, target_add_bundle)
            self.assertEqual(_event_types(add_comparison), {"scope_tags_changed"})

    def test_applicability_order_no_event_and_value_change_emits_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "iOS-iPadOS/Classic/Classic__policy-classic.json"
            baseline_doc = {
                "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
                "snapshotId": BASELINE_ID,
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
                    "id": "policy-classic",
                    "displayName": "Classic",
                    "description": "",
                    "deviceManagementApplicabilityRuleOsVersion": {
                        "name": "os-version",
                        "ruleType": "include",
                        "osMinimumVersion": "10.0",
                    },
                    "roleScopeTagIds": ["0"],
                },
                "assignments": [],
            }
            target_reorder = copy.deepcopy(baseline_doc)
            target_reorder["policy"]["deviceManagementApplicabilityRuleOsVersion"] = {
                "osMinimumVersion": "10.0",
                "ruleType": "include",
                "name": "os-version",
            }
            target_change = copy.deepcopy(baseline_doc)
            target_change["policy"]["deviceManagementApplicabilityRuleOsVersion"]["osMinimumVersion"] = "11.0"
            row = build_modern_inventory_row(
                policy_id="policy-classic",
                policy_name="Classic",
                json_relative_path=rel,
            )
            row.update(
                {
                    "Platform": "iOS/iPadOS",
                    "Source": "Classic",
                    "PolicyType": "Device restrictions",
                    "ODataType": "#microsoft.graph.iosGeneralDeviceConfiguration",
                }
            )
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["classic"] = {"status": "success", "count": 1, "exportedCount": 1, "processingErrors": 0}
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[(rel, baseline_doc, row)],
                source_coverage=coverage,
            )
            target_reorder_bundle = build_comparison_bundle(
                root / "reorder",
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel, target_reorder, row)],
                source_coverage=coverage,
            )
            target_change_bundle = build_comparison_bundle(
                root / "change",
                snapshot_id="Intune_ConfigurationPolicies_20990103-120000",
                captured_at_utc="2099-01-03T12:00:00.0000000Z",
                policies=[(rel, target_change, row)],
                source_coverage=coverage,
            )
            self.assertEqual(len(compare_policy_bundles(baseline, target_reorder_bundle).changes), 0)
            change_comparison = compare_policy_bundles(baseline, target_change_bundle)
            self.assertEqual(_event_types(change_comparison), {"applicability_changed"})

    def test_unexplained_policy_semantic_change_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline, target = _bundle_pair(Path(tmp))
            baseline_ctx = load_snapshot_comparison_context(baseline)
            target_ctx = load_snapshot_comparison_context(target)
            target_ctx.snapshot.policies[0].semantic_hash = "f" * 64
            comparison = compare_normalized_snapshots(baseline_ctx, target_ctx)
            self.assertEqual(_event_types(comparison), {"unexplained_policy_semantic_change"})
            self.assertEqual(comparison.policy_diffs[0].state, "modified")

    def test_filter_coverage_failure_suppresses_removed_and_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filters = {
                "snapshotId": BASELINE_ID,
                "capturedAtUtc": CAPTURED_AT_UTC,
                "retrieval": {"status": "success", "count": 1, "error": None},
                "assignmentFilters": [
                    {
                        "id": "filter-001",
                        "displayName": "Filter",
                        "rule": "device.manufacturer -eq 'A'",
                        "platform": "windows10AndLater",
                        "assignmentFilterManagementType": "devices",
                    }
                ],
            }
            coverage_error = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage_error["assignmentFilters"] = {"status": "error", "count": 0, "error": "failed"}
            coverage_zero = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage_zero["assignmentFilters"] = {"status": "success", "count": 0, "error": None}
            baseline_with_filter = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[],
                assignment_filters=filters,
            )
            target_no_filter = build_comparison_bundle(
                root / "removed",
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[],
                assignment_filters={
                    "snapshotId": TARGET_ID,
                    "capturedAtUtc": CAPTURED_AT_UTC,
                    "retrieval": {"status": "success", "count": 0, "error": None},
                    "assignmentFilters": [],
                },
                source_coverage=coverage_error,
            )
            removed_comparison = compare_policy_bundles(baseline_with_filter, target_no_filter)
            self.assertNotIn("assignment_filter_removed", _event_types(removed_comparison))
            self.assertTrue(
                any(item.category == "assignment_filter_existence_unavailable" for item in removed_comparison.suppressions)
            )

            baseline_error = build_comparison_bundle(
                root / "baseline-error",
                snapshot_id="Intune_ConfigurationPolicies_20990103-120000",
                captured_at_utc="2099-01-03T12:00:00.0000000Z",
                policies=[],
                assignment_filters={
                    "snapshotId": "Intune_ConfigurationPolicies_20990103-120000",
                    "capturedAtUtc": CAPTURED_AT_UTC,
                    "retrieval": {"status": "error", "count": 0, "error": "failed"},
                    "assignmentFilters": [],
                },
                source_coverage=coverage_error,
            )
            target_with_filter = build_comparison_bundle(
                root / "added",
                snapshot_id="Intune_ConfigurationPolicies_20990104-120000",
                captured_at_utc="2099-01-04T12:00:00.0000000Z",
                policies=[],
                assignment_filters=filters,
                source_coverage=coverage_zero,
            )
            added_comparison = compare_policy_bundles(baseline_error, target_with_filter)
            self.assertNotIn("assignment_filter_added", _event_types(added_comparison))
            self.assertTrue(
                any(item.category == "assignment_filter_existence_unavailable" for item in added_comparison.suppressions)
            )

    def test_comparison_status_success_with_informational_warnings_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "iOS-iPadOS/Classic/Classic__policy-classic.json"
            classic_doc = {
                "policyExportSchemaVersion": POLICY_EXPORT_SCHEMA_VERSION,
                "snapshotId": BASELINE_ID,
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
                    "id": "policy-classic",
                    "displayName": "Classic",
                    "description": "",
                    "appStoreBlocked": False,
                    "roleScopeTagIds": ["0"],
                },
                "assignments": [],
            }
            row = build_modern_inventory_row(
                policy_id="policy-classic",
                policy_name="Classic",
                json_relative_path=rel,
            )
            row.update(
                {
                    "Platform": "iOS/iPadOS",
                    "Source": "Classic",
                    "PolicyType": "Device restrictions",
                    "ODataType": "#microsoft.graph.iosGeneralDeviceConfiguration",
                }
            )
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["classic"] = {"status": "success", "count": 1, "exportedCount": 1, "processingErrors": 0}
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[(rel, classic_doc, row)],
                source_coverage=coverage,
            )
            target = build_comparison_bundle(
                root / "target",
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel, copy.deepcopy(classic_doc), row)],
                source_coverage=coverage,
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(comparison.comparison_status, "success")
            normalized = normalize_bundle(baseline)
            self.assertIn(
                "classic_explicitness_unknown",
                normalized.policies[0].coverage.normalization_warnings,
            )

    def test_comparison_status_partial_when_suppressions_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {"status": "error", "count": 0, "exportedCount": 0, "processingErrors": 1}
            rel_b = "Windows/Modern/P__b.json"
            doc_b = build_basic_modern_policy_document(policy_id="policy-b")
            row_b = build_modern_inventory_row(policy_id="policy-b", policy_name="B", json_relative_path=rel_b)
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[],
                source_coverage=coverage,
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel_b, doc_b, row_b)],
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(comparison.comparison_status, "partial")

    def test_reverse_chronology_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline, target = _bundle_pair(Path(tmp))
            with self.assertRaises(ValueError):
                compare_policy_bundles(target, baseline)

    def test_policy_add_remove_flood_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "Windows/Modern/Full__policy-full.json"
            doc = build_basic_modern_policy_document(policy_id="policy-full", include_assignment=True)
            doc["settings"].append(
                {
                    "id": "setting-extra",
                    "settingInstance": {
                        "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                        "settingDefinitionId": "def-extra",
                        "simpleSettingValue": {"value": "x"},
                    },
                }
            )
            doc["settingDefinitions"]["def-extra"] = build_definition_object("def-extra")
            row = build_modern_inventory_row(policy_id="policy-full", policy_name="Full", json_relative_path=rel)
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {"status": "success", "count": 0, "exportedCount": 0, "processingErrors": 0}
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[],
                source_coverage=coverage,
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel, doc, row)],
            )
            add_comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(_event_types(add_comparison), {"policy_added"})
            self.assertEqual(len(add_comparison.changes), 1)

            remove_baseline = build_comparison_bundle(
                root / "remove-baseline",
                snapshot_id="Intune_ConfigurationPolicies_20990105-120000",
                captured_at_utc="2099-01-05T12:00:00.0000000Z",
                policies=[(rel, doc, row)],
                source_coverage=coverage,
            )
            remove_target = build_comparison_bundle(
                root / "remove-target",
                snapshot_id="Intune_ConfigurationPolicies_20990106-120000",
                captured_at_utc="2099-01-06T12:00:00.0000000Z",
                policies=[],
                source_coverage=coverage,
            )
            remove_comparison = compare_policy_bundles(remove_baseline, remove_target)
            self.assertEqual(_event_types(remove_comparison), {"policy_removed"})
            self.assertEqual(len(remove_comparison.changes), 1)

    def test_filter_rule_change_does_not_emit_assignment_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filters = {
                "snapshotId": BASELINE_ID,
                "capturedAtUtc": CAPTURED_AT_UTC,
                "retrieval": {"status": "success", "count": 1, "error": None},
                "assignmentFilters": [
                    {
                        "id": "filter-001",
                        "displayName": "Filter",
                        "rule": "device.manufacturer -eq 'A'",
                        "platform": "windows10AndLater",
                        "assignmentFilterManagementType": "devices",
                    }
                ],
            }
            rel = "Windows/Modern/Filtered__policy-filtered.json"
            doc = build_basic_modern_policy_document(policy_id="policy-filtered")
            doc["assignments"] = [
                {
                    "target": {
                        "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                        "groupId": "group-001",
                        "deviceAndAppManagementAssignmentFilterId": "filter-001",
                        "deviceAndAppManagementAssignmentFilterType": "include",
                    }
                }
            ]
            doc["retrieval"]["assignments"] = {"status": "success", "count": 1, "error": None}
            row = build_modern_inventory_row(
                policy_id="policy-filtered",
                policy_name="Filtered",
                json_relative_path=rel,
            )
            changed_filters = copy.deepcopy(filters)
            changed_filters["assignmentFilters"][0]["rule"] = "device.manufacturer -eq 'B'"
            baseline = build_comparison_bundle(
                root,
                snapshot_id=BASELINE_ID,
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[(rel, doc, row)],
                assignment_filters=filters,
            )
            target = build_comparison_bundle(
                root,
                snapshot_id=TARGET_ID,
                captured_at_utc="2099-01-02T12:00:00.0000000Z",
                policies=[(rel, copy.deepcopy(doc), row)],
                assignment_filters=changed_filters,
            )
            comparison = compare_policy_bundles(baseline, target)
            self.assertEqual(_event_types(comparison), {"assignment_filter_changed"})
            self.assertNotIn("assignment_added", _event_types(comparison))
            self.assertNotIn("assignment_removed", _event_types(comparison))


if __name__ == "__main__":
    unittest.main()
