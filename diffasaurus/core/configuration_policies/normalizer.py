"""Normalize Phase 0 Configuration Policy Snapshot Bundles."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from diffasaurus.core.configuration_policies.admx import normalize_admx_settings
from diffasaurus.core.configuration_policies.assignments import (
    normalize_assignment_filters,
    normalize_assignments,
)
from diffasaurus.core.configuration_policies.bundle_loader import (
    LoadedConfigurationPolicyBundle,
    LoadedPolicyDocument,
    load_configuration_policy_bundle,
)
from diffasaurus.core.configuration_policies.canonical import (
    SEMANTIC_PAYLOAD_VERSION,
    semantic_hash,
)
from diffasaurus.core.configuration_policies.classic import (
    extract_classic_semantic_metadata,
    normalize_classic_properties,
)
from diffasaurus.core.configuration_policies.models import (
    NormalizedPolicy,
    NormalizedPolicyCoverage,
    NormalizedSnapshot,
)
from diffasaurus.core.configuration_policies.modern import normalize_modern_settings
from diffasaurus.core.configuration_policies.modern_walk import walk_modern_setting_instances

EXPORT_SOURCE_MODERN = "configurationPolicies"
EXPORT_SOURCE_CLASSIC = "deviceConfigurations"
EXPORT_SOURCE_ADMX = "groupPolicyConfigurations"


def build_policy_key(export_source: str, policy_id: str) -> str:
    return f"{export_source}:{policy_id}"


def _retrieval_status(retrieval: dict[str, Any] | None, component: str) -> str:
    if not isinstance(retrieval, dict):
        return "unknown"
    component_retrieval = retrieval.get(component)
    if not isinstance(component_retrieval, dict):
        return "unknown"
    return str(component_retrieval.get("status", "unknown"))


def _modern_semantic_metadata(policy_body: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": policy_body.get("name") or "",
        "description": policy_body.get("description") or "",
    }
    role_scope_tag_ids = policy_body.get("roleScopeTagIds")
    if isinstance(role_scope_tag_ids, list):
        metadata["roleScopeTagIds"] = sorted(str(item) for item in role_scope_tag_ids)
    return metadata


def _observational_metadata(policy_body: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "createdDateTime",
        "lastModifiedDateTime",
        "version",
        "@odata.type",
    ):
        if key in policy_body and policy_body[key] is not None:
            metadata[key] = policy_body[key]
    template_reference = policy_body.get("templateReference")
    if isinstance(template_reference, dict) and template_reference:
        metadata["templateReference"] = template_reference
    return metadata


def _presentation_metadata(
    *,
    policy_body: dict[str, Any],
    inventory_row: dict[str, str],
    policy_doc: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": policy_body.get("name")
        or policy_body.get("displayName")
        or inventory_row.get("PolicyName", ""),
        "description": policy_body.get("description") or inventory_row.get("Description", ""),
        "platform": policy_doc.get("platform") or inventory_row.get("Platform", ""),
        "policyType": policy_doc.get("policyType") or inventory_row.get("PolicyType", ""),
    }


def _policy_semantic_payload(policy: NormalizedPolicy) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "semanticPayloadVersion": SEMANTIC_PAYLOAD_VERSION,
        "metadata": policy.semantic_metadata,
        "assignments": [assignment.semantic_dict() for assignment in policy.assignments],
    }
    settings = policy.settings
    if settings.get("kind") == "modern":
        payload["settings"] = settings.get("semantic", [])
    elif settings.get("kind") == "classic":
        payload["settings"] = settings.get("semantic", [])
        payload["classicExplicitness"] = policy.classic_explicitness or "unknown"
    elif settings.get("kind") == "admx":
        payload["settings"] = settings.get("semantic", [])
    else:
        payload["settings"] = settings.get("semantic", [])
    return payload


def _finalize_policy_hash(policy: NormalizedPolicy) -> None:
    if not policy.coverage.semantic_hash_eligible:
        policy.semantic_hash = ""
        return
    policy.semantic_hash = semantic_hash(_policy_semantic_payload(policy))


def _evaluate_modern_hash_eligibility(
    retrieval: dict[str, Any] | None,
    coverage: NormalizedPolicyCoverage,
) -> None:
    policy_detail = _retrieval_status(retrieval, "policyDetail")
    settings = _retrieval_status(retrieval, "settings")
    assignments = _retrieval_status(retrieval, "assignments")

    coverage.policy_detail = policy_detail  # type: ignore[assignment]
    coverage.settings = settings  # type: ignore[assignment]
    coverage.assignments = assignments  # type: ignore[assignment]
    coverage.definitions = _retrieval_status(retrieval, "settingDefinitions")  # type: ignore[assignment]

    blockers: list[str] = []
    if policy_detail == "error":
        blockers.append("policy_detail_unavailable")
    if settings == "error":
        blockers.append("settings_unavailable")
    if assignments == "error":
        blockers.append("assignments_unavailable")

    coverage.semantic_hash_blockers = blockers
    coverage.semantic_hash_eligible = not blockers


def _evaluate_classic_hash_eligibility(
    retrieval: dict[str, Any] | None,
    coverage: NormalizedPolicyCoverage,
) -> None:
    policy_detail = _retrieval_status(retrieval, "policyDetail")
    assignments = _retrieval_status(retrieval, "assignments")

    coverage.policy_detail = policy_detail  # type: ignore[assignment]
    coverage.settings = "not_applicable"  # type: ignore[assignment]
    coverage.assignments = assignments  # type: ignore[assignment]
    coverage.definitions = "not_applicable"  # type: ignore[assignment]

    blockers: list[str] = []
    if policy_detail == "error":
        blockers.append("policy_detail_unavailable")
    if assignments == "error":
        blockers.append("assignments_unavailable")

    coverage.semantic_hash_blockers = blockers
    coverage.semantic_hash_eligible = not blockers


def _evaluate_admx_hash_eligibility(
    retrieval: dict[str, Any] | None,
    coverage: NormalizedPolicyCoverage,
    admx_blockers: list[str],
) -> None:
    policy_detail = _retrieval_status(retrieval, "policyDetail")
    assignments = _retrieval_status(retrieval, "assignments")
    presentation_values = _retrieval_status(retrieval, "presentationValues")

    coverage.policy_detail = policy_detail  # type: ignore[assignment]
    coverage.settings = "not_applicable"  # type: ignore[assignment]
    coverage.assignments = assignments  # type: ignore[assignment]
    coverage.definitions = _retrieval_status(retrieval, "definitionValues")  # type: ignore[assignment]
    coverage.presentation_values = presentation_values  # type: ignore[assignment]

    blockers: list[str] = []
    if policy_detail == "error":
        blockers.append("policy_detail_unavailable")
    if assignments == "error":
        blockers.append("assignments_unavailable")
    blockers.extend(admx_blockers)

    coverage.semantic_hash_blockers = blockers
    coverage.semantic_hash_eligible = not blockers


def normalize_policy_document(
    policy_doc: dict[str, Any],
    *,
    inventory_row: dict[str, str] | None = None,
) -> NormalizedPolicy:
    inventory_row = inventory_row or {}
    export_source = str(policy_doc.get("exportSource", ""))
    policy_body = policy_doc.get("policy")
    if not isinstance(policy_body, dict):
        policy_body = {}
    policy_id = str(policy_body.get("id") or inventory_row.get("PolicyId", ""))

    normalized = NormalizedPolicy(
        policy_key=build_policy_key(export_source, policy_id),
        policy_id=policy_id,
        export_source=export_source,
        presentation=_presentation_metadata(
            policy_body=policy_body,
            inventory_row=inventory_row,
            policy_doc=policy_doc,
        ),
        observational_metadata=_observational_metadata(policy_body),
        semantic_payload_version=SEMANTIC_PAYLOAD_VERSION,
    )

    retrieval = policy_doc.get("retrieval")
    assignments = normalize_assignments(policy_doc.get("assignments"))
    normalized.assignments = assignments

    if export_source == EXPORT_SOURCE_MODERN:
        normalized.semantic_metadata = _modern_semantic_metadata(policy_body)
        modern_nodes, modern_warnings = normalize_modern_settings(
            policy_doc.get("settings"),
            policy_doc.get("settingDefinitions"),
        )
        normalized.settings = {
            "kind": "modern",
            "nodes": [node.to_dict() for node in modern_nodes],
            "semantic": [node.semantic_dict() for node in modern_nodes],
        }
        coverage = NormalizedPolicyCoverage()
        _evaluate_modern_hash_eligibility(retrieval if isinstance(retrieval, dict) else None, coverage)
        coverage.normalization_warnings.extend(modern_warnings)
        for node in modern_nodes:
            coverage.normalization_warnings.extend(node.warnings)
        normalized.coverage = coverage
    elif export_source == EXPORT_SOURCE_CLASSIC:
        normalized.semantic_metadata = extract_classic_semantic_metadata(policy_body)
        classic_properties = normalize_classic_properties(policy_body)
        normalized.classic_explicitness = "unknown"
        normalized.settings = {
            "kind": "classic",
            "properties": [item.to_dict() for item in classic_properties],
            "semantic": [item.semantic_dict() for item in classic_properties],
        }
        coverage = NormalizedPolicyCoverage()
        _evaluate_classic_hash_eligibility(retrieval if isinstance(retrieval, dict) else None, coverage)
        coverage.normalization_warnings.append("classic_explicitness_unknown")
        normalized.coverage = coverage
    elif export_source == EXPORT_SOURCE_ADMX:
        normalized.semantic_metadata = {
            "name": policy_body.get("displayName") or "",
            "description": policy_body.get("description") or "",
        }
        role_scope_tag_ids = policy_body.get("roleScopeTagIds")
        if isinstance(role_scope_tag_ids, list):
            normalized.semantic_metadata["roleScopeTagIds"] = sorted(
                str(item) for item in role_scope_tag_ids
            )
        admx_settings, admx_warnings, admx_blockers = normalize_admx_settings(
            policy_doc.get("definitionValues")
        )
        normalized.settings = {
            "kind": "admx",
            "settings": [item.to_dict() for item in admx_settings],
            "semantic": [item.semantic_dict() for item in admx_settings],
        }
        coverage = NormalizedPolicyCoverage()
        _evaluate_admx_hash_eligibility(
            retrieval if isinstance(retrieval, dict) else None,
            coverage,
            admx_blockers,
        )
        coverage.normalization_warnings.extend(admx_warnings)
        normalized.coverage = coverage
    else:
        normalized.settings = {"kind": "unknown", "semantic": []}
        coverage = NormalizedPolicyCoverage()
        coverage.normalization_errors.append("unsupported_export_source")
        coverage.semantic_hash_eligible = False
        coverage.semantic_hash_blockers.append("unsupported_export_source")
        normalized.coverage = coverage

    _finalize_policy_hash(normalized)
    return normalized


def normalize_bundle(bundle_path: str | Any) -> NormalizedSnapshot:
    from pathlib import Path

    started = time.perf_counter()
    loaded = load_configuration_policy_bundle(Path(bundle_path))
    return _normalize_loaded_bundle(loaded, started)


def _normalize_loaded_bundle(
    loaded: LoadedConfigurationPolicyBundle,
    started: float,
) -> NormalizedSnapshot:
    manifest = loaded.manifest
    snapshot = NormalizedSnapshot(
        source_snapshot_id=str(manifest.get("snapshotId", "")),
        source_policy_export_schema_version=int(manifest.get("policyExportSchemaVersion", 0) or 0),
        captured_at_utc=str(manifest.get("capturedAtUtc", "")),
        source_export_status=str(manifest.get("exportStatus", "")),
    )

    if snapshot.source_export_status != "complete":
        snapshot.normalization_warnings.append("source_export_incomplete")

    filters, filter_warnings = normalize_assignment_filters(loaded.assignment_filters)
    snapshot.assignment_filters = filters
    snapshot.normalization_warnings.extend(filter_warnings)

    policies = [
        normalize_policy_document(
            item.document,
            inventory_row=item.inventory_row,
        )
        for item in loaded.policies
    ]
    policies.sort(key=lambda policy: policy.policy_key)
    snapshot.policies = policies

    if any(policy.coverage.normalization_errors for policy in policies):
        snapshot.normalization_status = "error"
    elif snapshot.normalization_warnings or any(
        policy.coverage.normalization_warnings for policy in policies
    ):
        snapshot.normalization_status = "partial"
    else:
        snapshot.normalization_status = "success"

    snapshot.normalization_duration_seconds = round(time.perf_counter() - started, 4)
    return snapshot


def _iter_modern_setting_nodes(settings: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        nodes.append(node)
        for value in node.get("values", []):
            if not isinstance(value, dict):
                continue
            for child in value.get("children", []):
                if isinstance(child, dict):
                    visit(child)

    if settings.get("kind") != "modern":
        return nodes
    for node in settings.get("nodes", []):
        if isinstance(node, dict):
            visit(node)
    return nodes


def summarize_normalized_snapshot(snapshot: NormalizedSnapshot) -> dict[str, Any]:
    modern_kind_counts: Counter[str] = Counter()
    modern_top_level_setting_count = 0
    modern_instance_node_count = 0
    classic_property_counts: Counter[str] = Counter()
    admx_count = 0
    assignment_kind_counts: Counter[str] = Counter()
    warning_categories: Counter[str] = Counter()
    blocker_categories: Counter[str] = Counter()
    export_source_counts: Counter[str] = Counter()
    platform_counts: Counter[str] = Counter()
    hash_eligible = 0
    unique_hash_count = 0
    hashes: set[str] = set()

    for policy in snapshot.policies:
        export_source_counts[policy.export_source] += 1
        platform_counts[policy.presentation.get("platform", "unknown")] += 1
        if policy.coverage.semantic_hash_eligible:
            hash_eligible += 1
            if policy.semantic_hash:
                hashes.add(policy.semantic_hash)

        for warning in policy.coverage.normalization_warnings:
            warning_categories[warning] += 1
        for blocker in policy.coverage.semantic_hash_blockers:
            blocker_categories[blocker] += 1

        settings = policy.settings
        kind = settings.get("kind")
        if kind == "modern":
            modern_top_level_setting_count += len(settings.get("nodes", []))
            for node in _iter_modern_setting_nodes(settings):
                modern_instance_node_count += 1
                modern_kind_counts[str(node.get("kind", "unknown"))] += 1
        elif kind == "classic":
            odata_type = policy.observational_metadata.get("@odata.type", "unknown")
            classic_property_counts[str(odata_type)] += len(settings.get("properties", []))
        elif kind == "admx":
            admx_count += len(settings.get("settings", []))

        for assignment in policy.assignments:
            assignment_kind_counts[assignment.target_kind] += 1

    for warning in snapshot.normalization_warnings:
        warning_categories[warning] += 1

    unique_hash_count = len(hashes)

    return {
        "normalizationSchemaVersion": snapshot.normalization_schema_version,
        "normalizationStatus": snapshot.normalization_status,
        "sourceExportStatus": snapshot.source_export_status,
        "policyCount": len(snapshot.policies),
        "exportSourceCounts": dict(sorted(export_source_counts.items())),
        "platformCounts": dict(sorted(platform_counts.items())),
        "modernTopLevelSettingCount": modern_top_level_setting_count,
        "modernInstanceNodeCount": modern_instance_node_count,
        "modernKindCounts": dict(sorted(modern_kind_counts.items())),
        "classicPropertyCountsByODataType": dict(sorted(classic_property_counts.items())),
        "admxSettingCount": admx_count,
        "assignmentKindCounts": dict(sorted(assignment_kind_counts.items())),
        "assignmentFilterCount": len(snapshot.assignment_filters),
        "semanticHashEligiblePolicyCount": hash_eligible,
        "uniqueSemanticHashCount": unique_hash_count,
        "warningCategories": dict(sorted(warning_categories.items())),
        "blockerCategories": dict(sorted(blocker_categories.items())),
        "normalizationDurationSeconds": snapshot.normalization_duration_seconds,
    }


def count_modern_instance_nodes(policy_doc: dict[str, Any]) -> int:
    settings = policy_doc.get("settings")
    if not isinstance(settings, list):
        return 0
    total = 0
    for setting in settings:
        if isinstance(setting, dict):
            total += len(walk_modern_setting_instances(setting))
    return total
