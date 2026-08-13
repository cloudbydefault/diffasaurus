from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

from tests.fixtures.configuration_policy_bundle import (
    CAPTURED_AT_UTC,
    SNAPSHOT_ID,
    build_depth3_modern_setting_tree,
    build_cache_propagation_bundle,
    build_incomplete_bundle,
    build_inventory_only_bundle,
    build_modern_policy_fixture,
    build_orchestration_regression_bundle,
    build_synthetic_bundle,
)
from tools.configuration_policy_inventory import (
    EXPORT_STATUS_COMPLETE,
    EXPORT_STATUS_INCOMPLETE,
    EXPORT_STATUS_INTEGRITY_ERROR,
    INVENTORY_COLUMNS,
    REQUIRED_INVENTORY_COLUMNS,
    bundle_is_complete,
    read_inventory_csv,
    resolve_export_status_from_coverage,
    validate_inventory_schema,
    validate_source_export_accounting,
    write_inventory_csv,
)
from tools.inspect_configuration_policy_bundle import (
    CHOICE_SETTING_DEFINITION_TYPE,
    _walk_modern_setting_instances,
    collect_recursive_definition_ids,
    inspect_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "psscripts" / "app_INTUNE_ConfigurationPolicy.ps1"
PS_HELPER_TEST_SCRIPT = ROOT / "tests" / "fixtures" / "run_configuration_policy_helper_tests.ps1"
DEPTH3_SETTING_JSON = ROOT / "tests" / "fixtures" / "depth3_setting.json"
REAL_SHAPE_SETTING_JSON = ROOT / "tests" / "fixtures" / "real_shape_children_setting.json"
MIXED_MODERN_SETTINGS_JSON = ROOT / "tests" / "fixtures" / "mixed_modern_settings.json"


class ConfigurationPolicyScriptContractTests(unittest.TestCase):
    def test_script_uses_reports_dir_convention(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("$env:REPORTS_DIR", text)
        self.assertIn('throw "REPORTS_DIR environment variable is not set', text)
        self.assertNotIn("$PSScriptRoot", text)

    def test_script_has_common_captured_at_contract(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("$capturedAtUtc =", text)
        self.assertIn("policyExportSchemaVersion = 4", text)
        self.assertIn("snapshotSchemaVersion = 1", text)
        self.assertRegex(text, r"\$timestamp\s*=\s*Get-Date")

    def test_script_requires_powershell_7(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("#Requires -Version 7", text)

    def test_script_has_relative_json_path_contract(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("JsonRelativePath", text)
        self.assertIn("snapshot_manifest.json", text)
        self.assertIn("assignment_filters.json", text)
        self.assertIn("retrieval_diagnostics.json", text)

    def test_script_captures_setting_definitions(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("settingDefinitions", text)
        self.assertIn("Get-ModernPolicySettingDefinitions", text)
        self.assertIn("Get-RecursiveSettingDefinitionIds", text)
        self.assertIn("Invoke-ModernSettingGraphWalk", text)
        self.assertIn("Get-ModernSettingStructuralMetrics", text)
        self.assertIn("Resolve-ConfigurationSettingDefinitions", text)

    def test_script_captures_admx_presentation_values(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("presentationValues", text)
        self.assertIn("Get-AdmxDefinitionValuesWithPresentations", text)

    def test_script_distinguishes_retrieval_error_from_empty(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("New-RetrievalComponent", text)
        self.assertIn('"error"', text)
        self.assertIn("skipped_by_option", text)

    def test_script_exports_inventory_via_pscustomobject_not_ordered_dict(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("ConvertTo-InventoryRecord", text)
        self.assertIn("InventoryColumnOrder", text)
        self.assertIn("exportStatus", text)
        self.assertIn('"complete"', text)

    def test_script_does_not_use_unary_comma_csv_serialization(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn(",$records | Export-Csv", text)
        self.assertRegex(text, r"\$records\s*\|\s*Export-Csv")

    def test_script_has_no_markdown_graph_urls(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertNotRegex(text, r"\[https://")

    def test_setting_definition_batch_uses_beta_batch_endpoint(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn('[string]$ApiVersion = "v1.0"', text)
        self.assertIn("https://graph.microsoft.com/$ApiVersion/`$batch", text)
        self.assertNotIn('-Uri "https://graph.microsoft.com/v1.0/`$batch"', text)
        self.assertRegex(
            text,
            r"Resolve-ConfigurationSettingDefinitions[\s\S]*Invoke-GraphBatchGet\s+-RelativeUrls\s+\$requests\s+-ApiVersion\s+beta",
        )

    def test_batch_inner_urls_use_direct_configuration_settings_lookup(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn(
            'url = "/deviceManagement/configurationSettings/$encodedId"',
            text,
        )
        self.assertIn("[System.Uri]::EscapeDataString($definitionId)", text)
        self.assertNotRegex(text, r"/configurationPolicies/.*/settings/.*/settingDefinitions")
        self.assertNotRegex(text, r'url\s*=\s*"/beta/deviceManagement/')

    def test_manifest_exposes_batch_http_and_item_counts(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("batchHttpRequestCount", text)
        self.assertIn("batchItemCount", text)
        self.assertIn("batchRequestCount", text)
        self.assertIn("settingDefinitionsFound", text)
        self.assertIn("settingDefinitionsMissing", text)
        self.assertIn("Copy-DefinitionToPolicyMap", text)
        self.assertIn("DefinitionCache", text)
        self.assertIn("DefinitionFailedIds", text)

    def test_manifest_exposes_structural_definition_coverage_metrics(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("policyLocalDefinitionReferences", text)
        self.assertIn("uniqueDefinitionIdsRequired", text)
        self.assertIn("policyLocalDefinitionsResolved", text)
        self.assertIn("policyLocalDefinitionsMissing", text)

    def test_modern_policy_loop_has_per_policy_try_catch(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        modern_section = text.split("# --- Modern policies ---", 1)[1].split(
            "# --- Classic policies ---", 1
        )[0]
        self.assertRegex(
            modern_section,
            r"foreach \(\$policySummary in \$modernPolicies\) \{[\s\S]*try \{[\s\S]*\$modernCoverage\.exportedCount\+\+",
        )
        self.assertRegex(
            modern_section,
            r"catch \{[\s\S]*\$modernCoverage\.processingErrors\+\+",
        )
        self.assertRegex(
            modern_section,
            r"try \{[\s\S]*\$modernPolicies = @\(Invoke-GraphPagedGet[\s\S]*configurationPolicies",
        )

    def test_export_integrity_accounting_guard(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("Add-SourceExportAccountingCheck", text)
        self.assertIn("exportIntegrityErrors", text)
        self.assertIn('exportStatus              = $exportStatus', text)
        self.assertIn('"integrity_error"', text)

    def test_modern_inventory_uses_safe_template_reference_helper(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("Get-TemplateReferenceValue", text)
        self.assertNotIn(
            "[string]$policy.templateReference.templateFamily",
            text,
        )

    def test_recursive_definition_ids_avoids_hashset_toarray(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        body = ConfigurationPolicyHelperControlFlowTests._function_body(
            text,
            "Get-RecursiveSettingDefinitionIds",
        )
        self.assertNotRegex(body, r"\$definitionIdSet\.ToArray\(\)")
        self.assertNotRegex(body, r"@\(\$definitionIdSet\.ToArray\(\)\)")
        self.assertIn("[string[]]@($definitionIdSet)", body)

    def test_modern_policy_processing_errors_use_policy_processing_component(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        modern_section = text.split("# --- Modern policies ---", 1)[1].split(
            "# --- Classic policies ---", 1
        )[0]
        self.assertIn('-Component "policyProcessing"', modern_section)
        self.assertRegex(
            modern_section,
            r"foreach \(\$policySummary in \$modernPolicies\) \{[\s\S]*catch \{[\s\S]*-Component \"policyProcessing\"",
        )


class ConfigurationPolicyRecursiveStructureTests(unittest.TestCase):
    def test_depth3_fixture_counts(self):
        setting = build_depth3_modern_setting_tree()
        walked = _walk_modern_setting_instances(setting)
        self.assertEqual(len(walked), 8)
        self.assertEqual(max(depth for _, depth, _ in walked), 3)
        self.assertEqual(sum(1 for _, depth, _ in walked if depth > 0), 7)
        type_counts = Counter(odata for odata, _, _ in walked if odata)
        self.assertEqual(
            type_counts["#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance"],
            3,
        )
        self.assertEqual(
            type_counts["#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance"],
            3,
        )
        self.assertEqual(
            type_counts["#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance"],
            2,
        )
        definition_ids = {definition_id for _, _, definition_id in walked if definition_id}
        self.assertEqual(len(definition_ids), 8)

    def test_depth3_fixture_via_inspector(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tempdir.name)
            bundle_root = root / SNAPSHOT_ID
            bundle_root.mkdir(parents=True, exist_ok=True)
            setting = build_depth3_modern_setting_tree()
            policy = build_modern_policy_fixture(settings=[setting])
            rel_path = "Windows/Modern/Nested Policy__policy-nested-001.json"
            policy_path = bundle_root / rel_path
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            write_inventory_csv(
                bundle_root / "inventory.csv",
                [
                    {
                        "SnapshotId": SNAPSHOT_ID,
                        "CapturedAtUtc": CAPTURED_AT_UTC,
                        "Platform": "Windows",
                        "PolicyType": "Settings catalog",
                        "Source": "Modern",
                        "PolicyName": "Nested Policy",
                        "Description": "",
                        "PolicyId": "policy-nested-001",
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
                        "JsonRelativePath": rel_path,
                        "RetrievalStatus": "success",
                        "SettingsRetrievalStatus": "success",
                        "AssignmentsRetrievalStatus": "success",
                        "DefinitionsRetrievalStatus": "success",
                    }
                ],
            )
            manifest = {
                "snapshotSchemaVersion": 1,
                "policyExportSchemaVersion": 4,
                "snapshotId": SNAPSHOT_ID,
                "capturedAtUtc": CAPTURED_AT_UTC,
                "exportStatus": "complete",
                "inventoryRelativePath": "inventory.csv",
                "policyCount": 1,
                "sourceCoverage": {},
                "platformCounts": {},
                "sourceCounts": {},
                "policyTypeCounts": {},
                "retrievalSummary": {},
            }
            (bundle_root / "snapshot_manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            report = inspect_bundle(bundle_root)
            modern = report["modern"]
            self.assertEqual(modern["topLevelSettingCount"], 1)
            self.assertEqual(modern["settingInstanceNodeCount"], 8)
            self.assertEqual(modern["maxNestingDepth"], 3)
            self.assertEqual(modern["childSettingCount"], 7)
            self.assertEqual(modern["policyLocalDefinitionReferences"], 8)
            self.assertEqual(modern["uniqueDefinitionsAcrossBundle"], 8)
            self.assertEqual(modern["recursiveDefinitionIds"], 8)
        finally:
            tempdir.cleanup()

    def test_unknown_nested_setting_instance_is_counted_without_leaking_values(self):
        setting = {
            "id": "setting-unknown-nested",
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
                "settingDefinitionId": "def-group-parent",
                "groupSettingCollectionValue": [
                    {
                        "children": [
                            {
                                "settingInstance": {
                                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationFutureSettingInstance",
                                    "settingDefinitionId": "def-future-child",
                                    "futureSettingValue": {"value": "tenant-secret-value"},
                                    "children": [
                                        {
                                            "settingInstance": {
                                                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
                                                "settingDefinitionId": "def-simple-grandchild",
                                            }
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                ],
            },
        }
        walked = _walk_modern_setting_instances(setting)
        self.assertEqual(len(walked), 3)
        self.assertEqual(max(depth for _, depth, _ in walked), 2)
        unknown = [
            odata
            for odata, _, _ in walked
            if odata.endswith("FutureSettingInstance")
        ]
        self.assertEqual(len(unknown), 1)

        tempdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tempdir.name)
            bundle_root = root / SNAPSHOT_ID
            bundle_root.mkdir(parents=True, exist_ok=True)
            policy = build_modern_policy_fixture(settings=[setting], policy_id="policy-unknown-001")
            rel_path = "Windows/Modern/Unknown Nested__policy-unknown-001.json"
            (bundle_root / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (bundle_root / rel_path).write_text(json.dumps(policy), encoding="utf-8")
            write_inventory_csv(
                bundle_root / "inventory.csv",
                [
                    {
                        "SnapshotId": SNAPSHOT_ID,
                        "CapturedAtUtc": CAPTURED_AT_UTC,
                        "Platform": "Windows",
                        "PolicyType": "Settings catalog",
                        "Source": "Modern",
                        "PolicyName": "Unknown Nested",
                        "Description": "",
                        "PolicyId": "policy-unknown-001",
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
                        "JsonRelativePath": rel_path,
                        "RetrievalStatus": "success",
                        "SettingsRetrievalStatus": "success",
                        "AssignmentsRetrievalStatus": "success",
                        "DefinitionsRetrievalStatus": "success",
                    }
                ],
            )
            (bundle_root / "snapshot_manifest.json").write_text(
                json.dumps(
                    {
                        "snapshotSchemaVersion": 1,
                        "policyExportSchemaVersion": 4,
                        "snapshotId": SNAPSHOT_ID,
                        "capturedAtUtc": CAPTURED_AT_UTC,
                        "exportStatus": "complete",
                        "inventoryRelativePath": "inventory.csv",
                        "policyCount": 1,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                report = inspect_bundle(bundle_root)
            output = buffer.getvalue()
            self.assertEqual(
                report["modern"]["unknownSettingInstanceTypes"][
                    "#microsoft.graph.deviceManagementConfigurationFutureSettingInstance"
                ],
                1,
            )
            self.assertNotIn("tenant-secret-value", output)
            self.assertNotIn("def-future-child", output)
            self.assertNotIn("def-simple-grandchild", output)
        finally:
            tempdir.cleanup()

    def test_setting_definitions_multiple_definitions_drive_found_missing_counts(self):
        setting = build_depth3_modern_setting_tree()
        setting_definitions = {
            "def-group-0": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionDefinition",
                "id": "def-group-0",
                "displayName": "Group 0",
            },
            "def-choice-1": {
                "@odata.type": CHOICE_SETTING_DEFINITION_TYPE,
                "id": "def-choice-1",
                "displayName": "Choice 1",
                "options": [{"itemId": "choice-1", "displayName": "Choice option"}],
                "defaultOptionId": "choice-1",
            },
            "def-simple-1": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition",
                "id": "def-simple-1",
                "displayName": "Simple 1",
            },
        }
        policy = build_modern_policy_fixture(
            settings=[setting],
            setting_definitions=setting_definitions,
            setting_definitions_retrieval={
                "status": "partial",
                "count": 3,
                "requestedCount": 8,
                "foundCount": 3,
                "missingCount": 5,
                "error": "5 setting definition lookups failed",
            },
            policy_id="policy-defs-001",
        )

        tempdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tempdir.name)
            bundle_root = root / SNAPSHOT_ID
            bundle_root.mkdir(parents=True, exist_ok=True)
            rel_path = "Windows/Modern/Definitions Policy__policy-defs-001.json"
            (bundle_root / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (bundle_root / rel_path).write_text(json.dumps(policy), encoding="utf-8")
            write_inventory_csv(
                bundle_root / "inventory.csv",
                [
                    {
                        "SnapshotId": SNAPSHOT_ID,
                        "CapturedAtUtc": CAPTURED_AT_UTC,
                        "Platform": "Windows",
                        "PolicyType": "Settings catalog",
                        "Source": "Modern",
                        "PolicyName": "Definitions Policy",
                        "Description": "",
                        "PolicyId": "policy-defs-001",
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
                        "JsonRelativePath": rel_path,
                        "RetrievalStatus": "success",
                        "SettingsRetrievalStatus": "success",
                        "AssignmentsRetrievalStatus": "success",
                        "DefinitionsRetrievalStatus": "success",
                    }
                ],
            )
            (bundle_root / "snapshot_manifest.json").write_text(
                json.dumps(
                    {
                        "snapshotSchemaVersion": 1,
                        "policyExportSchemaVersion": 4,
                        "snapshotId": SNAPSHOT_ID,
                        "capturedAtUtc": CAPTURED_AT_UTC,
                        "exportStatus": "complete",
                        "inventoryRelativePath": "inventory.csv",
                        "policyCount": 1,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            report = inspect_bundle(bundle_root)
            modern = report["modern"]
            self.assertEqual(modern["recursiveDefinitionIds"], 8)
            self.assertEqual(modern["definitionsFound"], 3)
            self.assertEqual(modern["definitionsMissing"], 5)
            self.assertEqual(modern["definitionCoveragePercent"], 37.5)
            self.assertEqual(modern["choiceDefinitions"], 1)
            self.assertEqual(modern["choiceDefinitionsWithOptions"], 1)
            self.assertEqual(modern["choiceOptionCount"], 1)
            self.assertEqual(modern["choiceDefinitionsWithDefaultOptionId"], 1)
            self.assertEqual(report["validation"]["errors"], [])
        finally:
            tempdir.cleanup()


class ConfigurationPolicyBundleFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.bundle_root = build_synthetic_bundle(Path(self.tempdir.name))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_bundle_has_required_artifacts(self):
        expected = {
            "snapshot_manifest.json",
            "inventory.csv",
            "assignment_filters.json",
            "retrieval_diagnostics.json",
        }
        present = {path.name for path in self.bundle_root.iterdir()}
        self.assertTrue(expected.issubset(present))

    def test_inventory_uses_relative_json_paths(self):
        report = inspect_bundle(self.bundle_root)
        self.assertEqual(report["contract"]["absolutePaths"], [])
        self.assertEqual(report["contract"]["missingJsonFiles"], [])

    def test_snapshot_manifest_required_fields(self):
        manifest = json.loads((self.bundle_root / "snapshot_manifest.json").read_text(encoding="utf-8"))
        for field in (
            "snapshotSchemaVersion",
            "policyExportSchemaVersion",
            "snapshotId",
            "capturedAtUtc",
            "timestamp",
            "requiredGraphScope",
            "bundleName",
            "exportStatus",
            "sourceCoverage",
            "policyCount",
            "inventoryRelativePath",
            "anchorRelativePath",
            "assignmentFiltersRelativePath",
            "diagnosticsRelativePath",
        ):
            self.assertIn(field, manifest)
        self.assertEqual(manifest["exportStatus"], "complete")

    def test_modern_raw_settings_and_definitions_preserved(self):
        policy_path = self.bundle_root / "Windows/Modern/Synthetic Policy__policy-modern-001.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(len(policy["settings"]), 2)
        self.assertIn("def-simple-001", policy["settingDefinitions"])
        self.assertEqual(
            policy["settingDefinitions"]["def-simple-001"]["id"],
            "def-simple-001",
        )
        self.assertEqual(
            policy["settings"][0]["settingInstance"]["@odata.type"],
            "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
        )

    def test_assignment_filters_artifact_present(self):
        filters_doc = json.loads((self.bundle_root / "assignment_filters.json").read_text(encoding="utf-8"))
        self.assertEqual(filters_doc["retrieval"]["status"], "success")
        self.assertEqual(len(filters_doc["assignmentFilters"]), 1)

    def test_admx_presentation_values_preserved(self):
        policy_path = self.bundle_root / "Windows/AdministrativeTemplates/Synthetic ADMX__policy-admx-001.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        definition_value = policy["definitionValues"][0]
        self.assertEqual(len(definition_value["presentationValues"]), 1)
        self.assertEqual(
            definition_value["presentationValues"][0]["@odata.type"],
            "#microsoft.graph.groupPolicyPresentationValueText",
        )

    def test_retrieval_error_is_not_known_zero(self):
        policy_path = self.bundle_root / "Android/Modern/Synthetic Error Policy__policy-modern-error-001.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(policy["retrieval"]["settings"]["status"], "error")
        self.assertEqual(policy["settings"], [])

    def test_skipped_source_coverage_supported_in_manifest(self):
        manifest = json.loads((self.bundle_root / "snapshot_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("modern", manifest["sourceCoverage"])
        self.assertEqual(manifest["sourceCoverage"]["modern"]["status"], "success")

    def test_anchor_csv_matches_bundle_inventory(self):
        anchor_path = Path(self.tempdir.name) / f"{SNAPSHOT_ID}.csv"
        self.assertTrue(anchor_path.exists())
        bundle_inventory = (self.bundle_root / "inventory.csv").read_text(encoding="utf-8-sig")
        anchor_inventory = anchor_path.read_text(encoding="utf-8-sig")
        self.assertEqual(bundle_inventory, anchor_inventory)


class ConfigurationPolicyBundleInspectorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.bundle_root = build_synthetic_bundle(Path(self.tempdir.name))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_inspector_validates_correct_bundle(self):
        report = inspect_bundle(self.bundle_root)
        self.assertEqual(report["validation"]["errors"], [])
        self.assertEqual(report["modern"]["policyCount"], 2)
        self.assertEqual(report["classic"]["policyCount"], 1)
        self.assertEqual(report["admx"]["policyCount"], 1)
        self.assertGreater(report["modern"]["maxNestingDepth"], 0)

    def test_inspector_catches_absolute_paths(self):
        inventory_path = self.bundle_root / "inventory.csv"
        text = inventory_path.read_text(encoding="utf-8-sig")
        text = text.replace(
            "Windows/Modern/Synthetic Policy__policy-modern-001.json",
            "/tmp/absolute/policy.json",
        )
        inventory_path.write_text(text, encoding="utf-8")

        report = inspect_bundle(self.bundle_root)
        self.assertIn("absolute_json_path", report["validation"]["errors"])

    def test_inspector_catches_snapshot_id_mismatch(self):
        policy_path = self.bundle_root / "Windows/Modern/Synthetic Policy__policy-modern-001.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["snapshotId"] = "wrong-snapshot"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")

        report = inspect_bundle(self.bundle_root)
        self.assertIn("snapshot_id_mismatch", report["validation"]["errors"])

    def test_inspector_catches_missing_json_file(self):
        missing = self.bundle_root / "Windows/Modern/Synthetic Policy__policy-modern-001.json"
        missing.unlink()

        report = inspect_bundle(self.bundle_root)
        self.assertIn("missing_json_file", report["validation"]["errors"])

    def test_inspector_catches_missing_json_relative_path_column(self):
        inventory_path = self.bundle_root / "inventory.csv"
        fieldnames, rows = read_inventory_csv(inventory_path)
        fieldnames = [name for name in fieldnames if name != "JsonRelativePath"]
        with inventory_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        report = inspect_bundle(self.bundle_root)
        self.assertIn(
            "inventory_missing_required_column:JsonRelativePath",
            report["validation"]["errors"],
        )
        self.assertEqual(report["contract"]["jsonPolicyFiles"], 0)

    def test_inspector_catches_blank_json_relative_path(self):
        inventory_path = self.bundle_root / "inventory.csv"
        text = inventory_path.read_text(encoding="utf-8-sig")
        text = re.sub(
            r"Windows/Modern/Synthetic Policy__policy-modern-001\.json",
            "",
            text,
            count=1,
        )
        inventory_path.write_text(text, encoding="utf-8")

        report = inspect_bundle(self.bundle_root)
        self.assertIn("inventory_blank_json_relative_path", report["validation"]["errors"])

    def test_inspector_catches_directory_json_relative_path(self):
        inventory_path = self.bundle_root / "inventory.csv"
        text = inventory_path.read_text(encoding="utf-8-sig")
        text = text.replace(
            "Windows/Modern/Synthetic Policy__policy-modern-001.json",
            "Windows/Modern",
        )
        inventory_path.write_text(text, encoding="utf-8")

        report = inspect_bundle(self.bundle_root)
        self.assertIn("inventory_json_path_is_directory", report["validation"]["errors"])

    def test_inspector_loads_five_policy_synthetic_inventory(self):
        root = Path(self.tempdir.name) / "five-policy"
        root.mkdir(parents=True, exist_ok=True)
        bundle_root = root / SNAPSHOT_ID
        bundle_root.mkdir(parents=True, exist_ok=True)

        template = {
            "policyExportSchemaVersion": 4,
            "snapshotId": SNAPSHOT_ID,
            "capturedAtUtc": CAPTURED_AT_UTC,
            "exportSource": "configurationPolicies",
            "platform": "Windows",
            "policyType": "Settings catalog",
            "retrieval": {
                "policyDetail": {"status": "success", "count": 1, "error": None},
                "settings": {"status": "success", "count": 0, "error": None},
                "assignments": {"status": "success", "count": 0, "error": None},
                "settingDefinitions": {"status": "success", "count": 0, "error": None},
            },
            "policy": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationPolicy",
                "id": "",
                "name": "",
                "platforms": "windows10",
                "technologies": "mdm",
                "settingCount": 0,
                "isAssigned": False,
                "roleScopeTagIds": ["0"],
                "createdDateTime": CAPTURED_AT_UTC,
                "lastModifiedDateTime": CAPTURED_AT_UTC,
                "templateReference": {},
            },
            "settings": [],
            "settingDefinitions": {},
            "assignments": [],
        }

        inventory_rows = []
        for index in range(1, 6):
            policy_id = f"policy-five-{index:03d}"
            rel_path = f"Windows/Modern/Five Policy {index}__{policy_id}.json"
            policy_doc = json.loads(json.dumps(template))
            policy_doc["policy"]["id"] = policy_id
            policy_doc["policy"]["name"] = f"Five Policy {index}"
            policy_path = bundle_root / rel_path
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy_path.write_text(json.dumps(policy_doc), encoding="utf-8")
            inventory_rows.append(
                {
                    "SnapshotId": SNAPSHOT_ID,
                    "CapturedAtUtc": CAPTURED_AT_UTC,
                    "Platform": "Windows",
                    "PolicyType": "Settings catalog",
                    "Source": "Modern",
                    "PolicyName": f"Five Policy {index}",
                    "Description": "",
                    "PolicyId": policy_id,
                    "ODataType": "#microsoft.graph.deviceManagementConfigurationPolicy",
                    "PlatformsRaw": "windows10",
                    "Technologies": "mdm",
                    "TemplateFamily": "",
                    "TemplateDisplayName": "",
                    "TemplateDisplayVersion": "",
                    "SettingCount": "0",
                    "RetrievedSettingCount": "0",
                    "AssignmentCount": "0",
                    "AssignmentTargets": "",
                    "IsAssigned": "False",
                    "RoleScopeTagIds": "0",
                    "CreatedDateTime": CAPTURED_AT_UTC,
                    "LastModifiedDateTime": CAPTURED_AT_UTC,
                    "Version": "",
                    "JsonRelativePath": rel_path,
                    "RetrievalStatus": "success",
                    "SettingsRetrievalStatus": "success",
                    "AssignmentsRetrievalStatus": "success",
                    "DefinitionsRetrievalStatus": "success",
                }
            )

        write_inventory_csv(bundle_root / "inventory.csv", inventory_rows)
        write_inventory_csv(root / f"{SNAPSHOT_ID}.csv", inventory_rows)
        manifest = {
            "snapshotSchemaVersion": 1,
            "policyExportSchemaVersion": 4,
            "snapshotId": SNAPSHOT_ID,
            "capturedAtUtc": CAPTURED_AT_UTC,
            "exportStatus": "complete",
            "inventoryRelativePath": "inventory.csv",
            "policyCount": 5,
            "sourceCoverage": {},
            "platformCounts": {},
            "sourceCounts": {},
            "policyTypeCounts": {},
            "retrievalSummary": {},
        }
        (bundle_root / "snapshot_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        report = inspect_bundle(bundle_root)
        self.assertEqual(report["contract"]["inventoryRows"], 5)
        self.assertEqual(report["contract"]["jsonPolicyFiles"], 5)
        self.assertEqual(report["modern"]["policyCount"], 5)
        self.assertEqual(report["validation"]["errors"], [])

    def test_inspector_reports_unknown_setting_instance_type_safely(self):
        policy_path = self.bundle_root / "Windows/Modern/Synthetic Policy__policy-modern-001.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["settings"].append(
            {
                "id": "setting-unknown-001",
                "settingInstance": {
                    "@odata.type": "#microsoft.graph.deviceManagementConfigurationFutureSettingInstance",
                    "settingDefinitionId": "def-future-001",
                },
            }
        )
        policy_path.write_text(json.dumps(policy), encoding="utf-8")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = inspect_bundle(self.bundle_root)
        output = buffer.getvalue()

        self.assertIn(
            "#microsoft.graph.deviceManagementConfigurationFutureSettingInstance",
            json.dumps(report["modern"]["unknownSettingInstanceTypes"]),
        )
        self.assertNotIn("Synthetic Policy", output)
        self.assertNotIn("policy-modern-001", output)

    def test_inspector_cli_json_output_is_sanitized(self):
        json_path = Path(self.tempdir.name) / "lab-report.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "inspect_configuration_policy_bundle.py"),
                str(self.bundle_root),
                "--json-output",
                str(json_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        serialized = json.dumps(payload)
        self.assertNotIn("Synthetic Policy", serialized)
        self.assertNotIn("group-synthetic-001", serialized)
        self.assertNotIn("filter-synthetic-001", serialized)

    def test_all_policy_json_uses_common_captured_at(self):
        for row_path in self.bundle_root.rglob("*.json"):
            if row_path.name in {
                "snapshot_manifest.json",
                "assignment_filters.json",
                "retrieval_diagnostics.json",
            }:
                continue
            if row_path.parent == self.bundle_root:
                continue
            payload = json.loads(row_path.read_text(encoding="utf-8"))
            if "capturedAtUtc" in payload:
                self.assertEqual(payload["capturedAtUtc"], CAPTURED_AT_UTC)


class ConfigurationPolicyInventoryCsvTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def _inventory_row(self, **overrides: str) -> dict[str, str]:
        row = {
            "SnapshotId": SNAPSHOT_ID,
            "CapturedAtUtc": CAPTURED_AT_UTC,
            "Platform": "Windows",
            "PolicyType": "Settings catalog",
            "Source": "Modern",
            "PolicyName": "Synthetic Policy",
            "Description": "",
            "PolicyId": "policy-modern-001",
            "ODataType": "#microsoft.graph.deviceManagementConfigurationPolicy",
            "PlatformsRaw": "windows10",
            "Technologies": "mdm",
            "TemplateFamily": "",
            "TemplateDisplayName": "",
            "TemplateDisplayVersion": "",
            "SettingCount": "1",
            "RetrievedSettingCount": "1",
            "AssignmentCount": "1",
            "AssignmentTargets": "",
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
        row.update(overrides)
        return row

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_zero_policy_tenant_writes_header_only_csv(self):
        root = Path(self.tempdir.name)
        bundle_root = build_inventory_only_bundle(root, [])
        rows = self._read_csv(bundle_root / "inventory.csv")
        self.assertEqual(rows, [])
        header = (bundle_root / "inventory.csv").read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertEqual(header.split(","), list(INVENTORY_COLUMNS))

    def test_one_row_inventory(self):
        root = Path(self.tempdir.name)
        row = self._inventory_row()
        bundle_root = build_inventory_only_bundle(root, [row])
        rows = self._read_csv(bundle_root / "inventory.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["PolicyId"], "policy-modern-001")
        self.assertIn("JsonRelativePath", rows[0])
        self.assertTrue(rows[0]["JsonRelativePath"])

    def test_five_row_inventory_has_json_relative_path_header(self):
        rows = [
            self._inventory_row(
                PolicyId=f"policy-{index:03d}",
                PolicyName=f"Policy {index}",
                JsonRelativePath=f"Windows/Modern/Policy {index}__policy-{index:03d}.json",
            )
            for index in range(1, 6)
        ]
        path = Path(self.tempdir.name) / "inventory.csv"
        write_inventory_csv(path, rows)
        parsed = self._read_csv(path)
        self.assertEqual(len(parsed), 5)
        header = path.read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertIn("JsonRelativePath", header.split(","))
        for row in parsed:
            self.assertTrue(row["JsonRelativePath"])

    def test_anchor_row_count_matches_inventory_row_count(self):
        rows = [
            self._inventory_row(
                PolicyId=f"policy-{index:03d}",
                PolicyName=f"Policy {index}",
                JsonRelativePath=f"Windows/Modern/Policy {index}__policy-{index:03d}.json",
            )
            for index in range(1, 6)
        ]
        root = Path(self.tempdir.name)
        bundle_root = build_inventory_only_bundle(root, rows)
        inventory_rows = self._read_csv(bundle_root / "inventory.csv")
        anchor_rows = self._read_csv(root / f"{SNAPSHOT_ID}.csv")
        self.assertEqual(len(inventory_rows), 5)
        self.assertEqual(len(anchor_rows), 5)

    def test_validate_inventory_schema_requires_contract_columns(self):
        errors = validate_inventory_schema(list(INVENTORY_COLUMNS))
        self.assertEqual(errors, [])
        missing = [column for column in INVENTORY_COLUMNS if column != "JsonRelativePath"]
        errors = validate_inventory_schema(missing)
        self.assertIn("inventory_missing_required_column:JsonRelativePath", errors)
        self.assertEqual(set(REQUIRED_INVENTORY_COLUMNS).issubset(set(INVENTORY_COLUMNS)), True)

    def test_zero_admx_policies_inventory_shape(self):
        root = Path(self.tempdir.name)
        rows = [
            self._inventory_row(Platform="Windows", Source="Modern"),
            self._inventory_row(
                Platform="macOS",
                PolicyId="policy-modern-002",
                JsonRelativePath="macOS/Modern/Synthetic Policy__policy-modern-002.json",
            ),
            self._inventory_row(
                Platform="iOS/iPadOS",
                Source="Classic",
                PolicyType="Device restrictions",
                PolicyId="policy-classic-001",
                JsonRelativePath="iOS-iPadOS/Classic/Synthetic Classic__policy-classic-001.json",
                SettingsRetrievalStatus="not_applicable",
                DefinitionsRetrievalStatus="not_applicable",
            ),
        ]
        bundle_root = build_inventory_only_bundle(root, rows)
        inventory = self._read_csv(bundle_root / "inventory.csv")
        platforms = {row["Platform"] for row in inventory}
        self.assertEqual(platforms, {"Windows", "macOS", "iOS/iPadOS"})
        self.assertNotIn("AdministrativeTemplate", {row["Source"] for row in inventory})

    def test_zero_android_policies(self):
        rows = [
            self._inventory_row(Platform="Windows"),
            self._inventory_row(Platform="macOS", PolicyId="policy-modern-002"),
            self._inventory_row(
                Platform="iOS/iPadOS",
                Source="Classic",
                PolicyType="Device restrictions",
                PolicyId="policy-classic-001",
            ),
        ]
        self.assertFalse(any(row["Platform"] == "Android" for row in rows))
        path = Path(self.tempdir.name) / "inventory.csv"
        write_inventory_csv(path, rows)
        self.assertEqual(len(self._read_csv(path)), 3)

    def test_zero_macos_classic_policies(self):
        rows = [
            self._inventory_row(Platform="Windows", Source="Modern"),
            self._inventory_row(
                Platform="macOS",
                Source="Modern",
                PolicyId="policy-modern-mac",
            ),
            self._inventory_row(
                Platform="iOS/iPadOS",
                Source="Classic",
                PolicyType="Device restrictions",
                PolicyId="policy-classic-ios",
            ),
        ]
        classic_macos = [
            row for row in rows if row["Platform"] == "macOS" and row["Source"] == "Classic"
        ]
        self.assertEqual(classic_macos, [])

    def test_normal_multi_platform_inventory(self):
        root = Path(self.tempdir.name)
        bundle_root = build_synthetic_bundle(root)
        rows = self._read_csv(bundle_root / "inventory.csv")
        self.assertGreaterEqual(len(rows), 4)
        self.assertTrue((root / f"{SNAPSHOT_ID}.csv").exists())

    def test_anchor_exists_after_successful_export(self):
        root = Path(self.tempdir.name)
        build_synthetic_bundle(root)
        self.assertTrue((root / f"{SNAPSHOT_ID}.csv").exists())
        manifest = json.loads((root / SNAPSHOT_ID / "snapshot_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("exportStatus"), "complete")

    def test_incomplete_bundle_is_distinguishable(self):
        root = Path(self.tempdir.name)
        bundle_root = build_incomplete_bundle(root)
        self.assertFalse(bundle_is_complete(bundle_root))
        report = inspect_bundle(bundle_root)
        self.assertIn("export_not_complete", report["validation"]["errors"])

    def test_null_rows_are_treated_as_empty_inventory(self):
        path = Path(self.tempdir.name) / "inventory.csv"
        write_inventory_csv(path, None)
        self.assertEqual(self._read_csv(path), [])


class ConfigurationPolicySchema4DefinitionTests(unittest.TestCase):
    def test_recursive_id_extraction_returns_eight_ids(self):
        setting = build_depth3_modern_setting_tree()
        ids = collect_recursive_definition_ids([setting])
        self.assertEqual(len(ids), 8)

    def test_schema4_definition_storage_shape(self):
        setting = build_depth3_modern_setting_tree()
        definitions = {
            definition_id: {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition",
                "id": definition_id,
                "displayName": f"Definition {definition_id}",
            }
            for definition_id in sorted(collect_recursive_definition_ids([setting]))
        }
        policy = build_modern_policy_fixture(
            settings=[setting],
            setting_definitions=definitions,
            policy_id="policy-schema4-storage",
        )
        self.assertEqual(policy["policyExportSchemaVersion"], 4)
        for definition_id, definition in definitions.items():
            self.assertIn(definition_id, policy["settingDefinitions"])
            self.assertEqual(policy["settingDefinitions"][definition_id]["id"], definition_id)

    def test_deduplication_cache_prevents_repeat_graph_counter_increment(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("$script:DefinitionCache", text)
        self.assertIn("$script:DefinitionFailedIds", text)
        self.assertIn("if ($script:DefinitionCache.ContainsKey($definitionId))", text)
        self.assertIn("elseif ($script:DefinitionFailedIds.Contains($definitionId))", text)

    def test_inspector_schema4_synthetic_bundle_reports_choice_metrics(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            bundle_root = build_synthetic_bundle(Path(tempdir.name))
            report = inspect_bundle(bundle_root)
            modern = report["modern"]
            self.assertEqual(report["snapshot"]["policyExportSchemaVersion"], 4)
            self.assertGreaterEqual(modern["policyLocalDefinitionReferences"], 3)
            self.assertGreaterEqual(modern["policyLocalDefinitionsFound"], 3)
            self.assertGreaterEqual(modern["uniqueDefinitionsAcrossBundle"], 3)
            self.assertEqual(modern["choiceDefinitions"], 1)
            self.assertEqual(modern["choiceDefinitionsWithOptions"], 1)
            self.assertGreaterEqual(modern["choiceOptionCount"], 2)
            self.assertEqual(modern["choiceDefinitionsWithDefaultOptionId"], 1)
            self.assertEqual(report["validation"]["errors"], [])
        finally:
            tempdir.cleanup()

    def _policy_with_definitions(
        self,
        *,
        definitions: dict[str, dict[str, object]],
        retrieval: dict[str, object],
        policy_id: str,
    ) -> tuple[Path, Path]:
        tempdir = tempfile.TemporaryDirectory()
        root = Path(tempdir.name)
        bundle_root = root / SNAPSHOT_ID
        bundle_root.mkdir(parents=True, exist_ok=True)
        setting = build_depth3_modern_setting_tree()
        policy = build_modern_policy_fixture(
            settings=[setting],
            setting_definitions=definitions,
            setting_definitions_retrieval=retrieval,
            policy_id=policy_id,
        )
        rel_path = f"Windows/Modern/Status Policy__{policy_id}.json"
        (bundle_root / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (bundle_root / rel_path).write_text(json.dumps(policy), encoding="utf-8")
        write_inventory_csv(
            bundle_root / "inventory.csv",
            [
                {
                    "SnapshotId": SNAPSHOT_ID,
                    "CapturedAtUtc": CAPTURED_AT_UTC,
                    "Platform": "Windows",
                    "PolicyType": "Settings catalog",
                    "Source": "Modern",
                    "PolicyName": "Status Policy",
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
                    "JsonRelativePath": rel_path,
                    "RetrievalStatus": retrieval["status"],
                    "SettingsRetrievalStatus": "success",
                    "AssignmentsRetrievalStatus": "success",
                    "DefinitionsRetrievalStatus": retrieval["status"],
                }
            ],
        )
        (bundle_root / "snapshot_manifest.json").write_text(
            json.dumps(
                {
                    "snapshotSchemaVersion": 1,
                    "policyExportSchemaVersion": 4,
                    "snapshotId": SNAPSHOT_ID,
                    "capturedAtUtc": CAPTURED_AT_UTC,
                    "exportStatus": "complete",
                    "inventoryRelativePath": "inventory.csv",
                    "policyCount": 1,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.addCleanup(tempdir.cleanup)
        return bundle_root, root

    def test_retrieval_success_when_all_definitions_present(self):
        ids = sorted(collect_recursive_definition_ids([build_depth3_modern_setting_tree()]))
        definitions = {
            definition_id: {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition",
                "id": definition_id,
            }
            for definition_id in ids
        }
        bundle_root, _ = self._policy_with_definitions(
            definitions=definitions,
            retrieval={
                "status": "success",
                "count": 8,
                "requestedCount": 8,
                "foundCount": 8,
                "missingCount": 0,
                "error": None,
            },
            policy_id="policy-def-success",
        )
        report = inspect_bundle(bundle_root)
        self.assertEqual(report["modern"]["definitionsFound"], 8)
        self.assertEqual(report["modern"]["definitionsMissing"], 0)
        self.assertEqual(report["modern"]["definitionCoveragePercent"], 100.0)
        self.assertEqual(report["validation"]["errors"], [])

    def test_retrieval_partial_does_not_fail_bundle_validation(self):
        ids = sorted(collect_recursive_definition_ids([build_depth3_modern_setting_tree()]))
        definitions = {
            definition_id: {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition",
                "id": definition_id,
            }
            for definition_id in ids[:6]
        }
        bundle_root, _ = self._policy_with_definitions(
            definitions=definitions,
            retrieval={
                "status": "partial",
                "count": 6,
                "requestedCount": 8,
                "foundCount": 6,
                "missingCount": 2,
                "error": "2 setting definition lookups failed",
            },
            policy_id="policy-def-partial",
        )
        report = inspect_bundle(bundle_root)
        self.assertEqual(report["modern"]["definitionsFound"], 6)
        self.assertEqual(report["modern"]["definitionsMissing"], 2)
        self.assertEqual(report["validation"]["errors"], [])

    def test_retrieval_error_does_not_fail_bundle_validation(self):
        bundle_root, _ = self._policy_with_definitions(
            definitions={},
            retrieval={
                "status": "error",
                "count": 0,
                "requestedCount": 8,
                "foundCount": 0,
                "missingCount": 8,
                "error": "8 setting definition lookups failed",
            },
            policy_id="policy-def-error",
        )
        report = inspect_bundle(bundle_root)
        self.assertEqual(report["modern"]["definitionsFound"], 0)
        self.assertEqual(report["modern"]["definitionsMissing"], 8)
        self.assertEqual(report["validation"]["errors"], [])

    def test_cache_propagation_bundle_reports_full_policy_local_coverage(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            bundle_root = build_cache_propagation_bundle(Path(tempdir.name))
            policy_a = json.loads(
                (bundle_root / "Windows/Modern/Policy A__policy-a.json").read_text(encoding="utf-8")
            )
            policy_b = json.loads(
                (bundle_root / "Windows/Modern/Policy B__policy-b.json").read_text(encoding="utf-8")
            )

            self.assertIn("def-A", policy_a["settingDefinitions"])
            self.assertIn("def-B", policy_a["settingDefinitions"])
            self.assertNotIn("def-C", policy_a["settingDefinitions"])

            self.assertIn("def-B", policy_b["settingDefinitions"])
            self.assertIn("def-C", policy_b["settingDefinitions"])
            self.assertNotIn("def-A", policy_b["settingDefinitions"])
            self.assertEqual(policy_b["settingDefinitions"]["def-B"]["id"], "def-B")

            report = inspect_bundle(bundle_root)
            modern = report["modern"]
            self.assertEqual(modern["policyLocalDefinitionReferences"], 4)
            self.assertEqual(modern["uniqueDefinitionsAcrossBundle"], 3)
            self.assertEqual(modern["policyLocalDefinitionsFound"], 4)
            self.assertEqual(modern["definitionsFound"], 4)
            self.assertEqual(modern["definitionsMissing"], 0)
            self.assertEqual(modern["definitionCoveragePercent"], 100.0)
            self.assertEqual(modern["uniqueDefinitionsFoundAcrossBundle"], 3)
            self.assertEqual(report["snapshot"]["settingDefinitionRequests"], 3)
            self.assertEqual(report["validation"]["errors"], [])
        finally:
            tempdir.cleanup()

    def test_retrieval_zero_ids_is_success(self):
        policy = build_modern_policy_fixture(settings=[], setting_definitions={})
        policy["retrieval"]["settingDefinitions"] = {
            "status": "success",
            "count": 0,
            "requestedCount": 0,
            "foundCount": 0,
            "missingCount": 0,
            "error": None,
        }
        tempdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tempdir.name)
            bundle_root = root / SNAPSHOT_ID
            bundle_root.mkdir(parents=True, exist_ok=True)
            rel_path = "Windows/Modern/Zero Definitions__policy-zero.json"
            (bundle_root / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (bundle_root / rel_path).write_text(json.dumps(policy), encoding="utf-8")
            write_inventory_csv(
                bundle_root / "inventory.csv",
                [
                    {
                        "SnapshotId": SNAPSHOT_ID,
                        "CapturedAtUtc": CAPTURED_AT_UTC,
                        "Platform": "Windows",
                        "PolicyType": "Settings catalog",
                        "Source": "Modern",
                        "PolicyName": "Zero Definitions",
                        "Description": "",
                        "PolicyId": "policy-zero",
                        "ODataType": "#microsoft.graph.deviceManagementConfigurationPolicy",
                        "PlatformsRaw": "windows10",
                        "Technologies": "mdm",
                        "TemplateFamily": "",
                        "TemplateDisplayName": "",
                        "TemplateDisplayVersion": "",
                        "SettingCount": "0",
                        "RetrievedSettingCount": "0",
                        "AssignmentCount": "0",
                        "AssignmentTargets": "",
                        "IsAssigned": "False",
                        "RoleScopeTagIds": "0",
                        "CreatedDateTime": CAPTURED_AT_UTC,
                        "LastModifiedDateTime": CAPTURED_AT_UTC,
                        "Version": "",
                        "JsonRelativePath": rel_path,
                        "RetrievalStatus": "success",
                        "SettingsRetrievalStatus": "success",
                        "AssignmentsRetrievalStatus": "success",
                        "DefinitionsRetrievalStatus": "success",
                    }
                ],
            )
            (bundle_root / "snapshot_manifest.json").write_text(
                json.dumps(
                    {
                        "snapshotSchemaVersion": 1,
                        "policyExportSchemaVersion": 4,
                        "snapshotId": SNAPSHOT_ID,
                        "capturedAtUtc": CAPTURED_AT_UTC,
                        "exportStatus": "complete",
                        "inventoryRelativePath": "inventory.csv",
                        "policyCount": 1,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            report = inspect_bundle(bundle_root)
            self.assertEqual(report["modern"]["recursiveDefinitionIds"], 0)
            self.assertEqual(report["modern"]["definitionsFound"], 0)
            self.assertEqual(report["validation"]["errors"], [])
        finally:
            tempdir.cleanup()


class ConfigurationPolicyExportIntegrityTests(unittest.TestCase):
    def test_integrity_case_a_complete_allowed(self):
        coverage = {
            "modern": {
                "status": "success",
                "count": 3,
                "exportedCount": 3,
                "processingErrors": 0,
            }
        }
        self.assertEqual(resolve_export_status_from_coverage(coverage), EXPORT_STATUS_COMPLETE)
        self.assertEqual(
            validate_source_export_accounting(
                listed=3,
                exported=3,
                processing_errors=0,
                source_name="modern",
            ),
            [],
        )

    def test_integrity_case_b_processing_error_not_complete(self):
        coverage = {
            "modern": {
                "status": "success",
                "count": 3,
                "exportedCount": 2,
                "processingErrors": 1,
            }
        }
        self.assertEqual(resolve_export_status_from_coverage(coverage), EXPORT_STATUS_INCOMPLETE)
        self.assertEqual(
            validate_source_export_accounting(
                listed=3,
                exported=2,
                processing_errors=1,
                source_name="modern",
            ),
            [],
        )

    def test_integrity_case_c_unaccounted_policies_are_integrity_error(self):
        coverage = {
            "modern": {
                "status": "success",
                "count": 3,
                "exportedCount": 0,
                "processingErrors": 0,
            }
        }
        self.assertEqual(
            resolve_export_status_from_coverage(coverage),
            EXPORT_STATUS_INTEGRITY_ERROR,
        )
        self.assertEqual(
            validate_source_export_accounting(
                listed=3,
                exported=0,
                processing_errors=0,
                source_name="modern",
            ),
            ["modern_source_accounting_mismatch"],
        )

    def test_integrity_case_d_zero_list_is_valid(self):
        coverage = {
            "modern": {
                "status": "success",
                "count": 0,
                "exportedCount": 0,
                "processingErrors": 0,
            }
        }
        self.assertEqual(resolve_export_status_from_coverage(coverage), EXPORT_STATUS_COMPLETE)
        self.assertEqual(
            validate_source_export_accounting(
                listed=0,
                exported=0,
                processing_errors=0,
                source_name="modern",
            ),
            [],
        )


class ConfigurationPolicyOrchestrationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.bundle_root = build_orchestration_regression_bundle(Path(self.tempdir.name))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_three_modern_and_two_classic_policies_are_represented(self):
        _, rows = read_inventory_csv(self.bundle_root / "inventory.csv")
        self.assertEqual(len(rows), 5)
        modern_rows = [row for row in rows if row["Source"] == "Modern"]
        classic_rows = [row for row in rows if row["Source"] == "Classic"]
        self.assertEqual(len(modern_rows), 3)
        self.assertEqual(len(classic_rows), 2)

        report = inspect_bundle(self.bundle_root)
        self.assertEqual(report["contract"]["inventoryRows"], 5)
        self.assertEqual(report["contract"]["jsonPolicyFiles"], 5)
        self.assertEqual(report["modern"]["policyCount"], 3)
        self.assertEqual(report["classic"]["policyCount"], 2)

    def test_first_modern_policy_has_nested_definitions(self):
        macos_policy_path = self.bundle_root / (
            "macOS/Modern/Settings catalog Modern__policy-modern-macos-001.json"
        )
        payload = json.loads(macos_policy_path.read_text(encoding="utf-8"))
        definition_ids = collect_recursive_definition_ids(payload["settings"])
        self.assertGreater(len(definition_ids), 1)
        self.assertTrue(payload["settingDefinitions"])
        self.assertGreaterEqual(
            len(payload["settingDefinitions"]),
            len(definition_ids),
        )

        manifest = json.loads((self.bundle_root / "snapshot_manifest.json").read_text(encoding="utf-8"))
        self.assertGreater(int(manifest.get("settingDefinitionRequests", 0)), 0)
        self.assertGreater(int(manifest.get("batchItemCount", 0)), 0)

    def test_orchestration_manifest_accounting_is_complete(self):
        manifest = json.loads((self.bundle_root / "snapshot_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["exportStatus"], EXPORT_STATUS_COMPLETE)
        self.assertEqual(
            resolve_export_status_from_coverage(manifest["sourceCoverage"]),
            EXPORT_STATUS_COMPLETE,
        )


class ConfigurationPolicyHelperControlFlowTests(unittest.TestCase):
    @staticmethod
    def _function_body(script_text: str, function_name: str) -> str:
        pattern = rf"function {re.escape(function_name)}\b"
        match = re.search(pattern, script_text)
        if not match:
            return ""
        brace_start = script_text.find("{", match.end())
        if brace_start < 0:
            return ""
        start = brace_start + 1
        depth = 1
        index = start
        while index < len(script_text) and depth > 0:
            char = script_text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        return script_text[start : index - 1]

    def test_modern_tree_helpers_do_not_use_loop_flow_control(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8", errors="ignore")
        for function_name in (
            "Invoke-ModernSettingGraphWalk",
            "Get-RecursiveSettingDefinitionIds",
            "Get-AssignmentTargetSummary",
        ):
            body = self._function_body(text, function_name)
            self.assertTrue(body, msg=f"missing function body: {function_name}")
            self.assertNotIn("continue", body, msg=function_name)
            self.assertNotIn("break", body, msg=function_name)


class ConfigurationPolicyPowerShellHelperTests(unittest.TestCase):
    def _run_helper_harness(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                shutil.which("pwsh"),
                "-NoProfile",
                "-File",
                str(PS_HELPER_TEST_SCRIPT),
                "-Depth3SettingJsonPath",
                str(DEPTH3_SETTING_JSON),
                "-RealShapeSettingJsonPath",
                str(REAL_SHAPE_SETTING_JSON),
                "-MixedSettingsJsonPath",
                str(MIXED_MODERN_SETTINGS_JSON),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_recursive_definition_id_collection_zero_one_many_and_nested(self):
        completed = self._run_helper_harness()
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )
        self.assertIn("DEPTH3_COUNT=8", completed.stdout)
        self.assertIn("REAL_SHAPE_COUNT=8", completed.stdout)
        self.assertIn("MIXED_COUNT=5", completed.stdout)
        self.assertIn("OK", completed.stdout)


class ConfigurationPolicyDefinitionIdParityTests(unittest.TestCase):
    def _powershell_definition_id_count(self, settings_json_path: Path) -> int:
        completed = subprocess.run(
            [
                shutil.which("pwsh"),
                "-NoProfile",
                "-Command",
                (
                    f"$ErrorActionPreference='Stop'; "
                    f"$text = Get-Content -Raw '{settings_json_path}'; "
                    f"$exporter = Get-Content -Raw '{SCRIPT_PATH}'; "
                    f"$block = ($exporter -split '# --- Connect ---', 2)[0]; "
                    f"Invoke-Expression $block.Substring($block.IndexOf('function Add-GraphRequestCount')); "
                    f"function Invoke-GraphBatchGet {{ param($RelativeUrls,$ApiVersion='beta') @{{}} }}; "
                    f"$settings = $text | ConvertFrom-Json; "
                    f"if ($settings -is [System.Array]) {{ $arr = @($settings) }} else {{ $arr = @($settings) }}; "
                    f"$count = @(Get-RecursiveSettingDefinitionIds -Settings $arr).Count; "
                    f"Write-Output $count"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        return int(completed.stdout.strip())

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_depth3_fixture_matches_python_definition_id_count(self):
        setting = build_depth3_modern_setting_tree()
        python_count = len(collect_recursive_definition_ids([setting]))
        powershell_count = self._powershell_definition_id_count(DEPTH3_SETTING_JSON)
        self.assertEqual(powershell_count, python_count)
        self.assertEqual(python_count, 8)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_real_shape_children_fixture_matches_python_definition_id_count(self):
        payload = json.loads(REAL_SHAPE_SETTING_JSON.read_text(encoding="utf-8"))
        python_count = len(collect_recursive_definition_ids([payload]))
        powershell_count = self._powershell_definition_id_count(REAL_SHAPE_SETTING_JSON)
        self.assertEqual(powershell_count, python_count)
        self.assertEqual(python_count, 8)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_mixed_tree_fixture_matches_python_definition_id_count(self):
        settings = json.loads(MIXED_MODERN_SETTINGS_JSON.read_text(encoding="utf-8"))
        python_count = len(collect_recursive_definition_ids(settings))
        powershell_count = self._powershell_definition_id_count(MIXED_MODERN_SETTINGS_JSON)
        self.assertEqual(powershell_count, python_count)
        self.assertEqual(python_count, 5)


if __name__ == "__main__":
    unittest.main()
