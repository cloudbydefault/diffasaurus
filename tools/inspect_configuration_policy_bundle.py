#!/usr/bin/env python3
"""Structural inspector for Intune Configuration Policy Snapshot Bundles (Phase 0)."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.configuration_policy_inventory import validate_inventory_schema


SETTING_INSTANCE_TYPES = {
    "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance",
    "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
    "#microsoft.graph.deviceManagementConfigurationSimpleSettingCollectionInstance",
    "#microsoft.graph.deviceManagementConfigurationChoiceSettingCollectionInstance",
    "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance",
    "#microsoft.graph.deviceManagementConfigurationGroupSettingInstance",
}

SUPPORTED_POLICY_EXPORT_SCHEMA_VERSION = 4
CHOICE_SETTING_DEFINITION_TYPE = (
    "#microsoft.graph.deviceManagementConfigurationChoiceSettingDefinition"
)


def collect_recursive_definition_ids(settings: list[dict[str, Any]] | None) -> set[str]:
    ids: set[str] = set()
    for setting in settings or []:
        if not isinstance(setting, dict):
            continue
        for _, _, definition_id in _walk_modern_setting_instances(setting):
            if definition_id:
                ids.add(definition_id)
    return ids


def _hash_id(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_inventory(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def _validate_json_relative_path(bundle_path: Path, json_rel: str) -> str | None:
    if not json_rel or not json_rel.strip():
        return "inventory_blank_json_relative_path"
    if _is_absolute_path(json_rel):
        return "absolute_json_path"
    candidate = (bundle_path / json_rel).resolve()
    bundle_resolved = bundle_path.resolve()
    try:
        candidate.relative_to(bundle_resolved)
    except ValueError:
        return "inventory_json_path_outside_bundle"
    if candidate.is_dir():
        return "inventory_json_path_is_directory"
    if not candidate.is_file():
        return "missing_json_file"
    if not json_rel.lower().endswith(".json"):
        return "inventory_json_path_not_json"
    return None


def _is_absolute_path(value: str) -> bool:
    if not value:
        return False
    return value.startswith("/") or (len(value) > 2 and value[1] == ":")


_CHILD_CONTAINER_KEYS: tuple[str, ...] = (
    "children",
    "groupSettingCollectionValue",
    "choiceSettingCollectionValue",
    "simpleSettingCollectionValue",
)


def _looks_like_setting_instance(node: dict[str, Any]) -> bool:
    odata_type = str(node.get("@odata.type", ""))
    return "deviceManagementConfiguration" in odata_type and odata_type.endswith("Instance")


def _iter_child_setting_instances(instance: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add_instance(candidate: dict[str, Any]) -> None:
        marker = id(candidate)
        if marker in seen:
            return
        if _looks_like_setting_instance(candidate):
            seen.add(marker)
            found.append(candidate)

    def absorb(container: Any) -> None:
        if isinstance(container, list):
            for item in container:
                absorb(item)
        elif isinstance(container, dict):
            nested = container.get("settingInstance")
            if isinstance(nested, dict):
                add_instance(nested)
            elif _looks_like_setting_instance(container):
                add_instance(container)
            else:
                for key in _CHILD_CONTAINER_KEYS:
                    if key in container:
                        absorb(container[key])
                choice_value = container.get("choiceSettingValue")
                if isinstance(choice_value, dict) and choice_value.get("children") is not None:
                    absorb(choice_value["children"])
                simple_value = container.get("simpleSettingValue")
                if isinstance(simple_value, dict) and simple_value.get("children") is not None:
                    absorb(simple_value["children"])
                for key, value in container.items():
                    if key in {
                        "settingDefinitionId",
                        "@odata.type",
                        "value",
                        "id",
                        "name",
                        "displayName",
                        "description",
                    }:
                        continue
                    if isinstance(value, (dict, list)):
                        absorb(value)

    for key in _CHILD_CONTAINER_KEYS:
        if key in instance:
            absorb(instance[key])

    choice_value = instance.get("choiceSettingValue")
    if isinstance(choice_value, dict) and choice_value.get("children") is not None:
        absorb(choice_value["children"])

    simple_value = instance.get("simpleSettingValue")
    if isinstance(simple_value, dict) and simple_value.get("children") is not None:
        absorb(simple_value["children"])

    return found


def _walk_modern_setting_instances(
    setting: dict[str, Any],
    depth: int = 0,
) -> list[tuple[str, int, str | None]]:
    found: list[tuple[str, int, str | None]] = []

    def visit_instance(instance: dict[str, Any], current_depth: int) -> None:
        odata_type = str(instance.get("@odata.type", ""))
        definition_id = instance.get("settingDefinitionId")
        if not isinstance(definition_id, str):
            definition_id = None
        found.append((odata_type, current_depth, definition_id))
        for child_instance in _iter_child_setting_instances(instance):
            visit_instance(child_instance, current_depth + 1)

    if not isinstance(setting, dict):
        return found

    top_instance = setting.get("settingInstance")
    if isinstance(top_instance, dict):
        visit_instance(top_instance, depth)
    elif _looks_like_setting_instance(setting):
        visit_instance(setting, depth)

    return found


def _property_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _classic_property_inventory(policy: dict[str, Any]) -> dict[str, Counter[str]]:
    skip = {
        "@odata.type",
        "id",
        "displayName",
        "description",
        "createdDateTime",
        "lastModifiedDateTime",
        "roleScopeTagIds",
        "version",
    }
    inventory: dict[str, Counter[str]] = {}
    for key, value in policy.items():
        if key in skip:
            continue
        inventory[key] = Counter({_property_type_name(value): 1})
    return inventory


def inspect_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle_path = bundle_path.resolve()
    if bundle_path.is_file():
        bundle_path = bundle_path.parent

    manifest_path = bundle_path / "snapshot_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing snapshot_manifest.json in {bundle_path}")

    manifest = _load_json(manifest_path)
    inventory_path = bundle_path / str(manifest.get("inventoryRelativePath", "inventory.csv"))
    fieldnames, inventory = _read_inventory(inventory_path)

    report: dict[str, Any] = {
        "bundlePath": str(bundle_path),
        "snapshot": {
            "snapshotSchemaVersion": manifest.get("snapshotSchemaVersion"),
            "policyExportSchemaVersion": manifest.get("policyExportSchemaVersion"),
            "snapshotId": manifest.get("snapshotId"),
            "sourceCoverage": manifest.get("sourceCoverage", {}),
            "policyCount": manifest.get("policyCount", len(inventory)),
            "platformCounts": manifest.get("platformCounts", {}),
            "sourceCounts": manifest.get("sourceCounts", {}),
            "policyTypeCounts": manifest.get("policyTypeCounts", {}),
            "retrievalSummary": manifest.get("retrievalSummary", {}),
            "exportDurationSeconds": manifest.get("exportDurationSeconds"),
            "graphRequestCount": manifest.get("graphRequestCount"),
            "batchHttpRequestCount": manifest.get("batchHttpRequestCount"),
            "batchItemCount": manifest.get("batchItemCount", manifest.get("batchRequestCount")),
            "batchRequestCount": manifest.get("batchRequestCount"),
            "settingDefinitionRequests": manifest.get("settingDefinitionRequests"),
            "settingDefinitionsFound": manifest.get("settingDefinitionsFound"),
            "settingDefinitionsMissing": manifest.get("settingDefinitionsMissing"),
            "definitionRetrievalErrors": manifest.get("definitionRetrievalErrors"),
            "presentationValueRequests": manifest.get("presentationValueRequests"),
        },
        "validation": {
            "errors": [],
            "warnings": [],
        },
        "modern": {
            "policyCount": 0,
            "topLevelSettingCount": 0,
            "totalSettings": 0,
            "settingInstanceNodeCount": 0,
            "settingInstanceODataTypes": Counter(),
            "maxNestingDepth": 0,
            "childSettingCount": 0,
            "recursiveDefinitionIdSet": set(),
            "uniqueDefinitionsAcrossBundleSet": set(),
            "returnedDefinitionIdSet": set(),
            "uniqueSettingDefinitionIds": 0,
            "recursiveDefinitionIds": 0,
            "policyLocalDefinitionReferences": 0,
            "uniqueDefinitionsAcrossBundle": 0,
            "policyLocalDefinitionsFound": 0,
            "policyLocalDefinitionsMissing": 0,
            "uniqueDefinitionsFoundAcrossBundle": 0,
            "definitionCoveragePercent": None,
            "definitionODataTypes": Counter(),
            "definitionsFound": 0,
            "definitionsMissing": 0,
            "definitionKeyMismatches": 0,
            "choiceDefinitions": 0,
            "choiceDefinitionsWithOptions": 0,
            "choiceDefinitionsWithDefaultOptionId": 0,
            "choiceOptionCount": 0,
            "unknownSettingInstanceTypes": Counter(),
            "assignmentTargetODataTypes": Counter(),
            "assignmentFilterUsageCount": 0,
            "retrievalStatusCounts": Counter(),
        },
        "classic": {
            "policyCount": 0,
            "policyODataTypes": Counter(),
            "propertyTypesByODataType": {},
            "assignmentTargetODataTypes": Counter(),
            "unknownPolicyTypes": Counter(),
            "retrievalStatusCounts": Counter(),
        },
        "admx": {
            "policyCount": 0,
            "definitionValueCount": 0,
            "definitionODataTypes": Counter(),
            "enabledCount": 0,
            "disabledCount": 0,
            "presentationValueODataTypes": Counter(),
            "presentationMetadataCoverage": Counter(),
            "multiplePresentationValues": 0,
            "zeroPresentationValues": 0,
            "retrievalStatusCounts": Counter(),
        },
        "assignments": {
            "targetODataTypes": Counter(),
            "filtersUsed": False,
            "filterReferenceCount": 0,
            "assignmentFilterRecords": 0,
        },
        "contract": {
            "inventoryRows": len(inventory),
            "jsonPolicyFiles": 0,
            "duplicateKeys": [],
            "absolutePaths": [],
            "missingJsonFiles": [],
            "snapshotIdMismatches": 0,
            "capturedAtMismatches": 0,
        },
    }

    if manifest.get("exportStatus") != "complete":
        report["validation"]["errors"].append("export_not_complete")
    expected_snapshot_id = str(manifest.get("snapshotId", ""))
    expected_captured_at = str(manifest.get("capturedAtUtc", ""))

    schema_errors = validate_inventory_schema(fieldnames)
    for schema_error in schema_errors:
        report["validation"]["errors"].append(schema_error)

    assignment_filters_path = bundle_path / str(
        manifest.get("assignmentFiltersRelativePath", "assignment_filters.json")
    )
    if assignment_filters_path.exists():
        filters_doc = _load_json(assignment_filters_path)
        filters = filters_doc.get("assignmentFilters", [])
        if isinstance(filters, list):
            report["assignments"]["assignmentFilterRecords"] = len(filters)

    if schema_errors:
        report["modern"]["recursiveDefinitionIdSet"] = set()
        report["modern"]["uniqueDefinitionsAcrossBundleSet"] = set()
        report["modern"]["returnedDefinitionIdSet"] = set()
        report["modern"]["uniqueSettingDefinitionIds"] = 0
        report["modern"]["recursiveDefinitionIds"] = 0
        report["modern"]["settingInstanceODataTypes"] = {}
        report["modern"]["definitionODataTypes"] = {}
        report["modern"]["unknownSettingInstanceTypes"] = {}
        report["modern"]["assignmentTargetODataTypes"] = {}
        report["modern"]["retrievalStatusCounts"] = {}
        report["classic"]["propertyTypesByODataType"] = {}
        report["classic"]["assignmentTargetODataTypes"] = {}
        report["classic"]["retrievalStatusCounts"] = {}
        report["admx"]["definitionODataTypes"] = {}
        report["admx"]["presentationValueODataTypes"] = {}
        report["admx"]["presentationMetadataCoverage"] = {}
        report["admx"]["retrievalStatusCounts"] = {}
        report["assignments"]["targetODataTypes"] = {}
        report["validation"]["errors"] = sorted(set(report["validation"]["errors"]))
        report["validation"]["warnings"] = sorted(set(report["validation"]["warnings"]))
        return report

    seen_keys: set[tuple[str, str]] = set()

    for row in inventory:
        json_rel = str(row.get("JsonRelativePath", row.get("JsonFile", "")))
        path_error = _validate_json_relative_path(bundle_path, json_rel)
        if path_error:
            if path_error == "absolute_json_path":
                report["contract"]["absolutePaths"].append(json_rel)
            elif path_error == "missing_json_file":
                report["contract"]["missingJsonFiles"].append(json_rel)
            report["validation"]["errors"].append(path_error)
            continue

        policy_id = str(row.get("PolicyId", ""))
        export_source = _map_source(str(row.get("Source", "")))
        key = (export_source, policy_id)
        if key in seen_keys:
            report["contract"]["duplicateKeys"].append(
                {"exportSource": export_source, "policyIdHash": _hash_id(policy_id)}
            )
        seen_keys.add(key)

        json_path = bundle_path / json_rel
        report["contract"]["jsonPolicyFiles"] += 1
        policy_doc = _load_json(json_path)

        expected_policy_schema = manifest.get("policyExportSchemaVersion")
        policy_schema = policy_doc.get("policyExportSchemaVersion")
        if (
            expected_policy_schema is not None
            and policy_schema is not None
            and policy_schema != expected_policy_schema
        ):
            report["validation"]["warnings"].append("policy_export_schema_version_mismatch")
        if (
            policy_schema is not None
            and isinstance(policy_schema, int)
            and policy_schema > SUPPORTED_POLICY_EXPORT_SCHEMA_VERSION
        ):
            report["validation"]["warnings"].append("policy_export_schema_version_unsupported")

        if str(policy_doc.get("snapshotId", "")) != expected_snapshot_id:
            report["contract"]["snapshotIdMismatches"] += 1
            report["validation"]["errors"].append("snapshot_id_mismatch")

        if str(policy_doc.get("capturedAtUtc", "")) != expected_captured_at:
            report["contract"]["capturedAtMismatches"] += 1
            report["validation"]["errors"].append("captured_at_mismatch")

        retrieval = policy_doc.get("retrieval", {})
        overall = str(row.get("RetrievalStatus", ""))
        if overall:
            _increment_retrieval(report, policy_doc.get("exportSource", ""), overall)

        export_source = str(policy_doc.get("exportSource", ""))
        if export_source == "configurationPolicies":
            _inspect_modern_policy(report, policy_doc)
        elif export_source == "deviceConfigurations":
            _inspect_classic_policy(report, policy_doc)
        elif export_source == "groupPolicyConfigurations":
            _inspect_admx_policy(report, policy_doc)

        for assignment in policy_doc.get("assignments", []) or []:
            target = assignment.get("target", {}) if isinstance(assignment, dict) else {}
            target_type = str(target.get("@odata.type", "unknown"))
            report["assignments"]["targetODataTypes"][target_type] += 1
            report["modern"]["assignmentTargetODataTypes"][target_type] += 1
            report["classic"]["assignmentTargetODataTypes"][target_type] += 1
            filter_id = target.get("deviceAndAppManagementAssignmentFilterId")
            if filter_id:
                report["assignments"]["filtersUsed"] = True
                report["assignments"]["filterReferenceCount"] += 1
                report["modern"]["assignmentFilterUsageCount"] += 1

    if report["contract"]["inventoryRows"] != report["contract"]["jsonPolicyFiles"]:
        report["validation"]["errors"].append("inventory_json_count_mismatch")

    report["modern"]["recursiveDefinitionIdSet"] = set(report["modern"]["recursiveDefinitionIdSet"])
    report["modern"]["uniqueDefinitionsAcrossBundleSet"] = set(
        report["modern"]["uniqueDefinitionsAcrossBundleSet"]
    )
    report["modern"]["returnedDefinitionIdSet"] = set(report["modern"]["returnedDefinitionIdSet"])
    report["modern"]["uniqueDefinitionsAcrossBundle"] = len(
        report["modern"]["uniqueDefinitionsAcrossBundleSet"]
    )
    report["modern"]["recursiveDefinitionIds"] = report["modern"]["policyLocalDefinitionReferences"]
    report["modern"]["definitionsFound"] = report["modern"]["policyLocalDefinitionsFound"]
    report["modern"]["definitionsMissing"] = report["modern"]["policyLocalDefinitionsMissing"]
    report["modern"]["uniqueDefinitionsFoundAcrossBundle"] = len(
        report["modern"]["returnedDefinitionIdSet"]
    )
    report["modern"]["uniqueSettingDefinitionIds"] = report["modern"]["uniqueDefinitionsAcrossBundle"]
    if report["modern"]["policyLocalDefinitionReferences"] > 0:
        report["modern"]["definitionCoveragePercent"] = round(
            100.0
            * report["modern"]["policyLocalDefinitionsFound"]
            / report["modern"]["policyLocalDefinitionReferences"],
            2,
        )
    if report["modern"]["definitionKeyMismatches"]:
        report["validation"]["warnings"].append("setting_definition_key_id_mismatch")
    del report["modern"]["recursiveDefinitionIdSet"]
    del report["modern"]["uniqueDefinitionsAcrossBundleSet"]
    del report["modern"]["returnedDefinitionIdSet"]
    report["modern"]["settingInstanceODataTypes"] = dict(
        report["modern"]["settingInstanceODataTypes"]
    )
    report["modern"]["definitionODataTypes"] = dict(report["modern"]["definitionODataTypes"])
    report["modern"]["unknownSettingInstanceTypes"] = dict(
        report["modern"]["unknownSettingInstanceTypes"]
    )
    report["modern"]["assignmentTargetODataTypes"] = dict(
        report["modern"]["assignmentTargetODataTypes"]
    )
    report["modern"]["retrievalStatusCounts"] = dict(report["modern"]["retrievalStatusCounts"])
    report["classic"]["propertyTypesByODataType"] = {
        key: dict(counter)
        for key, counter in report["classic"]["propertyTypesByODataType"].items()
    }
    report["classic"]["assignmentTargetODataTypes"] = dict(
        report["classic"]["assignmentTargetODataTypes"]
    )
    report["classic"]["retrievalStatusCounts"] = dict(report["classic"]["retrievalStatusCounts"])
    report["admx"]["definitionODataTypes"] = dict(report["admx"]["definitionODataTypes"])
    report["admx"]["presentationValueODataTypes"] = dict(
        report["admx"]["presentationValueODataTypes"]
    )
    report["admx"]["presentationMetadataCoverage"] = dict(
        report["admx"]["presentationMetadataCoverage"]
    )
    report["admx"]["retrievalStatusCounts"] = dict(report["admx"]["retrievalStatusCounts"])
    report["assignments"]["targetODataTypes"] = dict(report["assignments"]["targetODataTypes"])
    report["validation"]["errors"] = sorted(set(report["validation"]["errors"]))
    report["validation"]["warnings"] = sorted(set(report["validation"]["warnings"]))
    return report


def _map_source(source: str) -> str:
    mapping = {
        "Modern": "configurationPolicies",
        "Classic": "deviceConfigurations",
        "AdministrativeTemplate": "groupPolicyConfigurations",
    }
    return mapping.get(source, source)


def _increment_retrieval(report: dict[str, Any], export_source: str, status: str) -> None:
    if export_source == "configurationPolicies":
        report["modern"]["retrievalStatusCounts"][status] += 1
    elif export_source == "deviceConfigurations":
        report["classic"]["retrievalStatusCounts"][status] += 1
    elif export_source == "groupPolicyConfigurations":
        report["admx"]["retrievalStatusCounts"][status] += 1


def _policy_definition_keys(
    definitions_map: dict[str, Any],
    schema_version: int,
) -> set[str]:
    if not isinstance(definitions_map, dict):
        return set()
    if schema_version >= 4:
        return {str(key) for key in definitions_map.keys()}
    keys: set[str] = set()
    for items in definitions_map.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                definition_id = item.get("id")
                if isinstance(definition_id, str) and definition_id:
                    keys.add(definition_id)
    return keys


def _record_definition_object(report: dict[str, Any], definition: dict[str, Any]) -> None:
    modern = report["modern"]
    definition_id = definition.get("id")
    if isinstance(definition_id, str) and definition_id:
        modern["returnedDefinitionIdSet"].add(definition_id)
    odata_type = str(definition.get("@odata.type", "unknown"))
    modern["definitionODataTypes"][odata_type] += 1
    if odata_type == CHOICE_SETTING_DEFINITION_TYPE or odata_type.endswith(
        "ChoiceSettingDefinition"
    ):
        modern["choiceDefinitions"] += 1
        options = definition.get("options")
        if isinstance(options, list) and options:
            modern["choiceDefinitionsWithOptions"] += 1
            modern["choiceOptionCount"] += len(options)
        if definition.get("defaultOptionId"):
            modern["choiceDefinitionsWithDefaultOptionId"] += 1


def _inspect_modern_policy(report: dict[str, Any], policy_doc: dict[str, Any]) -> None:
    modern = report["modern"]
    modern["policyCount"] += 1

    settings = policy_doc.get("settings", []) or []
    modern["topLevelSettingCount"] += len(settings)
    modern["totalSettings"] += len(settings)

    walked: list[tuple[str, int, str | None]] = []
    for setting in settings:
        if isinstance(setting, dict):
            walked.extend(_walk_modern_setting_instances(setting))

    modern["settingInstanceNodeCount"] += len(walked)

    definitions_map = policy_doc.get("settingDefinitions", {}) or {}
    schema_version = int(policy_doc.get("policyExportSchemaVersion", 0) or 0)
    policy_definition_keys = _policy_definition_keys(definitions_map, schema_version)

    for odata_type, depth, definition_id in walked:
        if odata_type:
            modern["settingInstanceODataTypes"][odata_type] += 1
            if odata_type not in SETTING_INSTANCE_TYPES:
                modern["unknownSettingInstanceTypes"][odata_type] += 1
        modern["maxNestingDepth"] = max(modern["maxNestingDepth"], depth)
        if depth > 0:
            modern["childSettingCount"] += 1
        if definition_id:
            modern["recursiveDefinitionIdSet"].add(definition_id)
            modern["uniqueDefinitionsAcrossBundleSet"].add(definition_id)
            modern["policyLocalDefinitionReferences"] += 1
            if definition_id in policy_definition_keys:
                modern["policyLocalDefinitionsFound"] += 1
            else:
                modern["policyLocalDefinitionsMissing"] += 1

    if isinstance(definitions_map, dict):
        if schema_version >= 4:
            for key, definition in definitions_map.items():
                if not isinstance(definition, dict):
                    continue
                embedded_id = definition.get("id")
                if isinstance(embedded_id, str) and embedded_id and embedded_id != str(key):
                    modern["definitionKeyMismatches"] += 1
                _record_definition_object(report, definition)
        else:
            for items in definitions_map.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        _record_definition_object(report, item)


def _inspect_classic_policy(report: dict[str, Any], policy_doc: dict[str, Any]) -> None:
    classic = report["classic"]
    classic["policyCount"] += 1
    policy = policy_doc.get("policy", {}) or {}
    odata_type = str(policy.get("@odata.type", "unknown"))
    classic["policyODataTypes"][odata_type] += 1
    if not odata_type.startswith("#microsoft.graph."):
        classic["unknownPolicyTypes"][odata_type] += 1

    prop_inventory = _classic_property_inventory(policy)
    merged = classic["propertyTypesByODataType"].setdefault(odata_type, Counter())
    for prop_name, type_counter in prop_inventory.items():
        for type_name, count in type_counter.items():
            merged[f"{prop_name}:{type_name}"] += count


def _inspect_admx_policy(report: dict[str, Any], policy_doc: dict[str, Any]) -> None:
    admx = report["admx"]
    admx["policyCount"] += 1

    for definition_value in policy_doc.get("definitionValues", []) or []:
        admx["definitionValueCount"] += 1
        definition = definition_value.get("definition", {}) if isinstance(definition_value, dict) else {}
        if isinstance(definition, dict):
            admx["definitionODataTypes"][str(definition.get("@odata.type", "unknown"))] += 1

        enabled = definition_value.get("enabled")
        if enabled is True:
            admx["enabledCount"] += 1
        elif enabled is False:
            admx["disabledCount"] += 1

        presentation_values = definition_value.get("presentationValues", []) or []
        if len(presentation_values) == 0:
            admx["zeroPresentationValues"] += 1
        elif len(presentation_values) > 1:
            admx["multiplePresentationValues"] += 1

        presentation_retrieval = definition_value.get("presentationRetrieval", {})
        status = str(presentation_retrieval.get("status", "unknown"))
        admx["presentationMetadataCoverage"][status] += 1

        for presentation in presentation_values:
            admx["presentationValueODataTypes"][
                str(presentation.get("@odata.type", "unknown"))
            ] += 1


def _format_report(report: dict[str, Any]) -> str:
    lines = [
        "Configuration Policy Bundle — Schema Laboratory Report",
        "=====================================================",
        f"Snapshot schema version: {report['snapshot']['snapshotSchemaVersion']}",
        f"Policy export schema version: {report['snapshot']['policyExportSchemaVersion']}",
        f"Policy count: {report['snapshot']['policyCount']}",
        f"Platform counts: {report['snapshot']['platformCounts']}",
        f"Source counts: {report['snapshot']['sourceCounts']}",
        "",
        "Validation",
        f"  errors: {report['validation']['errors']}",
        f"  absolute paths: {len(report['contract']['absolutePaths'])}",
        f"  missing JSON: {len(report['contract']['missingJsonFiles'])}",
        f"  snapshotId mismatches: {report['contract']['snapshotIdMismatches']}",
        "",
        "Modern",
        f"  policies: {report['modern']['policyCount']}",
        f"  top-level settings: {report['modern']['topLevelSettingCount']}",
        f"  setting-instance nodes: {report['modern']['settingInstanceNodeCount']}",
        f"  settings (legacy totalSettings): {report['modern']['totalSettings']}",
        f"  settingInstance @odata.type: {report['modern']['settingInstanceODataTypes']}",
        f"  max nesting depth: {report['modern']['maxNestingDepth']}",
        f"  child setting count: {report['modern']['childSettingCount']}",
        f"  policy-local definition references: {report['modern']['policyLocalDefinitionReferences']}",
        f"  unique definitions across bundle: {report['modern']['uniqueDefinitionsAcrossBundle']}",
        f"  recursive definition ids (legacy alias): {report['modern']['recursiveDefinitionIds']}",
        f"  definition coverage %: {report['modern']['definitionCoveragePercent']}",
        f"  unique settingDefinitionIds (legacy): {report['modern']['uniqueSettingDefinitionIds']}",
        f"  policy-local definitions found: {report['modern']['definitionsFound']}",
        f"  policy-local definitions missing: {report['modern']['definitionsMissing']}",
        f"  unique definitions found across bundle: {report['modern']['uniqueDefinitionsFoundAcrossBundle']}",
        f"  choice definitions: {report['modern']['choiceDefinitions']}",
        f"  choice definitions with options: {report['modern']['choiceDefinitionsWithOptions']}",
        f"  choice option count: {report['modern']['choiceOptionCount']}",
        f"  choice definitions with defaultOptionId: {report['modern']['choiceDefinitionsWithDefaultOptionId']}",
        f"  definition @odata.type: {report['modern']['definitionODataTypes']}",
        f"  unknown instance types: {report['modern']['unknownSettingInstanceTypes']}",
        "",
        "Classic",
        f"  policies: {report['classic']['policyCount']}",
        f"  policy @odata.type: {report['classic']['policyODataTypes']}",
        "",
        "ADMX",
        f"  policies: {report['admx']['policyCount']}",
        f"  definition values: {report['admx']['definitionValueCount']}",
        f"  presentation @odata.type: {report['admx']['presentationValueODataTypes']}",
        "",
        "Assignments",
        f"  target @odata.type: {report['assignments']['targetODataTypes']}",
        f"  filters used: {report['assignments']['filtersUsed']}",
        f"  filter references: {report['assignments']['filterReferenceCount']}",
        f"  assignment filter records: {report['assignments']['assignmentFilterRecords']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect an Intune Configuration Policy Snapshot Bundle (Phase 0)."
    )
    parser.add_argument("bundle_path", type=Path, help="Path to bundle directory")
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path to write sanitized JSON report",
    )
    args = parser.parse_args(argv)

    report = inspect_bundle(args.bundle_path)
    print(_format_report(report))

    if args.json_output:
        with args.json_output.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")

    return 0 if not report["validation"]["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
