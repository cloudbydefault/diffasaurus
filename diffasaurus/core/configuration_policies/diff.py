"""Semantic comparison engine for Configuration Policy snapshots (Phase 2)."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diffasaurus.core.configuration_policies.bundle_loader import load_configuration_policy_bundle
from diffasaurus.core.configuration_policies.canonical import canonical_json, semantic_hash
from diffasaurus.core.configuration_policies.comparison_models import (
    AssignmentFilterDiff,
    ChangeEvent,
    ComparisonStatus,
    ComparisonSuppression,
    ConfigurationPolicyComparison,
    PolicyDiff,
    SnapshotDescriptor,
)
from diffasaurus.core.configuration_policies.history import (
    SnapshotPair,
    _descriptor_from_bundle,
    _snapshot_sort_key,
    discover_policy_snapshots,
    select_latest_pair,
)
from diffasaurus.core.configuration_policies.models import NormalizedPolicy, NormalizedSnapshot
from diffasaurus.core.configuration_policies.normalizer import (
    EXPORT_SOURCE_ADMX,
    EXPORT_SOURCE_CLASSIC,
    EXPORT_SOURCE_MODERN,
    _normalize_loaded_bundle,
)

EXPORT_SOURCE_TO_COVERAGE_KEY: dict[str, str] = {
    EXPORT_SOURCE_MODERN: "modern",
    EXPORT_SOURCE_CLASSIC: "classic",
    EXPORT_SOURCE_ADMX: "administrativeTemplates",
}

TRUSTWORTHY_SOURCE_STATUSES = frozenset({"success"})


@dataclass(frozen=True)
class SnapshotComparisonContext:
    descriptor: SnapshotDescriptor
    snapshot: NormalizedSnapshot
    manifest: dict[str, Any]


def _source_coverage(manifest: dict[str, Any], export_source: str) -> dict[str, Any]:
    source_coverage = manifest.get("sourceCoverage")
    if not isinstance(source_coverage, dict):
        return {}
    coverage_key = EXPORT_SOURCE_TO_COVERAGE_KEY.get(export_source, "")
    coverage = source_coverage.get(coverage_key)
    return coverage if isinstance(coverage, dict) else {}


def _filter_coverage(manifest: dict[str, Any]) -> dict[str, Any]:
    source_coverage = manifest.get("sourceCoverage")
    if not isinstance(source_coverage, dict):
        return {}
    coverage = source_coverage.get("assignmentFilters")
    return coverage if isinstance(coverage, dict) else {}


def is_source_existence_trustworthy(manifest: dict[str, Any], export_source: str) -> bool:
    coverage = _source_coverage(manifest, export_source)
    status = str(coverage.get("status", ""))
    return status in TRUSTWORTHY_SOURCE_STATUSES


def is_filter_existence_trustworthy(manifest: dict[str, Any]) -> bool:
    coverage = _filter_coverage(manifest)
    status = str(coverage.get("status", ""))
    return status in TRUSTWORTHY_SOURCE_STATUSES


def _policy_settings_trustworthy(policy: NormalizedPolicy) -> bool:
    if policy.export_source == EXPORT_SOURCE_MODERN:
        return policy.coverage.settings in {"success", "not_applicable"}
    return True


def _policy_assignments_trustworthy(policy: NormalizedPolicy) -> bool:
    return policy.coverage.assignments in {"success", "not_applicable"}


def load_snapshot_comparison_context(bundle_path: Path | str) -> SnapshotComparisonContext:
    loaded = load_configuration_policy_bundle(Path(bundle_path))
    descriptor, issues = _descriptor_from_bundle(loaded.bundle_path)
    if descriptor is None or issues:
        raise ValueError(f"Invalid configuration policy bundle: {issues}")
    snapshot = _normalize_loaded_bundle(loaded, time.perf_counter())
    return SnapshotComparisonContext(
        descriptor=descriptor,
        snapshot=snapshot,
        manifest=loaded.manifest,
    )


def _descriptor_from_context(context: SnapshotComparisonContext) -> SnapshotDescriptor:
    return context.descriptor


def _metadata_name(policy: NormalizedPolicy) -> str:
    return str(policy.semantic_metadata.get("name", ""))


def _metadata_description(policy: NormalizedPolicy) -> str:
    return str(policy.semantic_metadata.get("description", ""))


def _metadata_scope_tags(policy: NormalizedPolicy) -> list[str]:
    tags = policy.semantic_metadata.get("roleScopeTagIds")
    if isinstance(tags, list):
        return sorted(str(item) for item in tags)
    return []


def _metadata_applicability(policy: NormalizedPolicy) -> dict[str, Any]:
    applicability = policy.semantic_metadata.get("applicability")
    return applicability if isinstance(applicability, dict) else {}


def _append_event(
    events: list[ChangeEvent],
    *,
    event_type: str,
    component_type: str,
    policy_key: str | None = None,
    component_key: str | None = None,
    before: Any = None,
    after: Any = None,
    warnings: list[str] | None = None,
) -> None:
    events.append(
        ChangeEvent(
            event_type=event_type,
            component_type=component_type,
            policy_key=policy_key,
            component_key=component_key,
            before=before,
            after=after,
            warnings=list(warnings or []),
        )
    )


def _compare_scope_tags(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    before_tags = set(_metadata_scope_tags(before))
    after_tags = set(_metadata_scope_tags(after))
    if before_tags == after_tags:
        return
    _append_event(
        events,
        event_type="scope_tags_changed",
        component_type="policy_metadata",
        policy_key=before.policy_key,
        before=sorted(before_tags),
        after=sorted(after_tags),
    )


def _compare_applicability(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    before_app = _metadata_applicability(before)
    after_app = _metadata_applicability(after)
    if semantic_hash(before_app) == semantic_hash(after_app):
        return
    _append_event(
        events,
        event_type="applicability_changed",
        component_type="policy_metadata",
        policy_key=before.policy_key,
        before=before_app,
        after=after_app,
    )


def _compare_policy_metadata(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    if _metadata_name(before) != _metadata_name(after):
        _append_event(
            events,
            event_type="policy_renamed",
            component_type="policy_metadata",
            policy_key=before.policy_key,
            before=_metadata_name(before),
            after=_metadata_name(after),
        )
    if _metadata_description(before) != _metadata_description(after):
        _append_event(
            events,
            event_type="policy_description_changed",
            component_type="policy_metadata",
            policy_key=before.policy_key,
            before=_metadata_description(before),
            after=_metadata_description(after),
        )
    _compare_scope_tags(before, after, events)
    _compare_applicability(before, after, events)


def _modern_node_map(policy: NormalizedPolicy) -> dict[str, dict[str, Any]]:
    nodes = policy.settings.get("nodes", [])
    return {
        str(node.get("definitionId")): node
        for node in nodes
        if isinstance(node, dict) and node.get("definitionId")
    }


def _classic_property_map(policy: NormalizedPolicy) -> dict[str, dict[str, Any]]:
    properties = policy.settings.get("properties", [])
    return {
        str(item.get("propertyPath")): item
        for item in properties
        if isinstance(item, dict) and item.get("propertyPath")
    }


def _admx_setting_map(policy: NormalizedPolicy) -> dict[str, dict[str, Any]]:
    settings = policy.settings.get("settings", [])
    result: dict[str, dict[str, Any]] = {}
    for item in settings:
        if not isinstance(item, dict):
            continue
        definition_id = str(item.get("definitionId", ""))
        if definition_id:
            result[definition_id] = item
    return result


def _assignment_map(policy: NormalizedPolicy) -> dict[str, Any]:
    return {assignment.assignment_key: assignment for assignment in policy.assignments}


def _compare_modern_settings(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    if not (_policy_settings_trustworthy(before) and _policy_settings_trustworthy(after)):
        return

    before_map = _modern_node_map(before)
    after_map = _modern_node_map(after)
    before_keys = set(before_map)
    after_keys = set(after_map)

    for definition_id in sorted(after_keys - before_keys):
        _append_event(
            events,
            event_type="setting_added",
            component_type="modern_setting",
            policy_key=before.policy_key,
            component_key=definition_id,
            after=after_map[definition_id].get("semanticHash"),
        )

    for definition_id in sorted(before_keys - after_keys):
        _append_event(
            events,
            event_type="setting_removed",
            component_type="modern_setting",
            policy_key=before.policy_key,
            component_key=definition_id,
            before=before_map[definition_id].get("semanticHash"),
        )

    for definition_id in sorted(before_keys & after_keys):
        before_node = before_map[definition_id]
        after_node = after_map[definition_id]
        before_hash = str(before_node.get("semanticHash", ""))
        after_hash = str(after_node.get("semanticHash", ""))
        if before_hash == after_hash:
            continue
        warnings: list[str] = []
        if before_node.get("kind") == "unknown" or after_node.get("kind") == "unknown":
            warnings.append("unknown_modern_setting_instance_type")
        _append_event(
            events,
            event_type="setting_changed",
            component_type="modern_setting",
            policy_key=before.policy_key,
            component_key=definition_id,
            before=before_node.get("semantic"),
            after=after_node.get("semantic"),
            warnings=warnings,
        )


def _compare_classic_properties(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    before_map = _classic_property_map(before)
    after_map = _classic_property_map(after)
    warnings = ["classic_explicitness_unknown"]

    for path in sorted(set(after_map) - set(before_map)):
        _append_event(
            events,
            event_type="classic_property_added",
            component_type="classic_property",
            policy_key=before.policy_key,
            component_key=path,
            after=after_map[path].get("rawValue"),
            warnings=warnings,
        )

    for path in sorted(set(before_map) - set(after_map)):
        _append_event(
            events,
            event_type="classic_property_removed",
            component_type="classic_property",
            policy_key=before.policy_key,
            component_key=path,
            before=before_map[path].get("rawValue"),
            warnings=warnings,
        )

    for path in sorted(set(before_map) & set(after_map)):
        before_value = before_map[path].get("rawValue")
        after_value = after_map[path].get("rawValue")
        if semantic_hash(before_value) == semantic_hash(after_value):
            continue
        _append_event(
            events,
            event_type="classic_property_changed",
            component_type="classic_property",
            policy_key=before.policy_key,
            component_key=path,
            before=before_value,
            after=after_value,
            warnings=warnings,
        )


def _compare_admx_settings(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    before_map = _admx_setting_map(before)
    after_map = _admx_setting_map(after)

    for definition_id in sorted(set(after_map) - set(before_map)):
        _append_event(
            events,
            event_type="admx_setting_added",
            component_type="admx_setting",
            policy_key=before.policy_key,
            component_key=definition_id,
            after=after_map[definition_id],
        )

    for definition_id in sorted(set(before_map) - set(after_map)):
        _append_event(
            events,
            event_type="admx_setting_removed",
            component_type="admx_setting",
            policy_key=before.policy_key,
            component_key=definition_id,
            before=before_map[definition_id],
        )

    for definition_id in sorted(set(before_map) & set(after_map)):
        before_semantic = {
            "enabled": before_map[definition_id].get("enabled"),
            "presentationValues": before_map[definition_id].get("presentationValues"),
        }
        after_semantic = {
            "enabled": after_map[definition_id].get("enabled"),
            "presentationValues": after_map[definition_id].get("presentationValues"),
        }
        if semantic_hash(before_semantic) == semantic_hash(after_semantic):
            continue
        _append_event(
            events,
            event_type="admx_setting_changed",
            component_type="admx_setting",
            policy_key=before.policy_key,
            component_key=definition_id,
            before=before_semantic,
            after=after_semantic,
        )


def _compare_assignments(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    if not (_policy_assignments_trustworthy(before) and _policy_assignments_trustworthy(after)):
        return

    before_map = _assignment_map(before)
    after_map = _assignment_map(after)

    for key in sorted(set(after_map) - set(before_map)):
        _append_event(
            events,
            event_type="assignment_added",
            component_type="assignment",
            policy_key=before.policy_key,
            component_key=key,
            after=after_map[key].semantic_dict(),
        )

    for key in sorted(set(before_map) - set(after_map)):
        _append_event(
            events,
            event_type="assignment_removed",
            component_type="assignment",
            policy_key=before.policy_key,
            component_key=key,
            before=before_map[key].semantic_dict(),
        )


def _compare_policy_components(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
    events: list[ChangeEvent],
) -> None:
    _compare_policy_metadata(before, after, events)

    settings_kind = before.settings.get("kind")
    if settings_kind == "modern":
        _compare_modern_settings(before, after, events)
    elif settings_kind == "classic":
        _compare_classic_properties(before, after, events)
    elif settings_kind == "admx":
        _compare_admx_settings(before, after, events)

    _compare_assignments(before, after, events)


def _compare_existing_policy(
    before: NormalizedPolicy,
    after: NormalizedPolicy,
) -> PolicyDiff:
    diff = PolicyDiff(
        policy_key=before.policy_key,
        export_source=before.export_source,
        state="unchanged",
        before_semantic_hash=before.semantic_hash,
        after_semantic_hash=after.semantic_hash,
        semantic_hash_eligible_before=before.coverage.semantic_hash_eligible,
        semantic_hash_eligible_after=after.coverage.semantic_hash_eligible,
        presentation_before=dict(before.presentation),
        presentation_after=dict(after.presentation),
    )

    if not before.coverage.semantic_hash_eligible or not after.coverage.semantic_hash_eligible:
        diff.state = "indeterminate"
        diff.suppressions.append(
            ComparisonSuppression(
                category="policy_semantics_unavailable",
                scope="policy",
                reason="semantic_hash_ineligible",
                policy_key=before.policy_key,
                export_source=before.export_source,
            )
        )
        return diff

    if before.semantic_hash == after.semantic_hash:
        return diff

    events: list[ChangeEvent] = []
    _compare_policy_components(before, after, events)

    if not events:
        _append_event(
            events,
            event_type="unexplained_policy_semantic_change",
            component_type="policy",
            policy_key=before.policy_key,
            before=before.semantic_hash,
            after=after.semantic_hash,
        )

    diff.changes = sorted(events, key=lambda event: event.sort_key())
    diff.state = "modified"
    return diff


def _filter_map(snapshot: NormalizedSnapshot) -> dict[str, Any]:
    return {item.filter_id: item for item in snapshot.assignment_filters}


def _compare_assignment_filters(
    baseline: SnapshotComparisonContext,
    target: SnapshotComparisonContext,
) -> tuple[list[AssignmentFilterDiff], list[ChangeEvent], list[ComparisonSuppression]]:
    diffs: list[AssignmentFilterDiff] = []
    events: list[ChangeEvent] = []
    suppressions: list[ComparisonSuppression] = []

    baseline_trust = is_filter_existence_trustworthy(baseline.manifest)
    target_trust = is_filter_existence_trustworthy(target.manifest)

    before_map = _filter_map(baseline.snapshot)
    after_map = _filter_map(target.snapshot)
    all_ids = sorted(set(before_map) | set(after_map))

    for filter_id in all_ids:
        before_filter = before_map.get(filter_id)
        after_filter = after_map.get(filter_id)

        if before_filter is None and after_filter is not None:
            if not baseline_trust:
                suppressions.append(
                    ComparisonSuppression(
                        category="assignment_filter_existence_unavailable",
                        scope="assignment_filter",
                        reason="baseline_filter_coverage_unavailable",
                    )
                )
                diffs.append(AssignmentFilterDiff(filter_id=filter_id, state="indeterminate"))
                continue
            diffs.append(
                AssignmentFilterDiff(
                    filter_id=filter_id,
                    state="added",
                    after_semantic_hash=after_filter.semantic_hash,
                )
            )
            events.append(
                ChangeEvent(
                    event_type="assignment_filter_added",
                    component_type="assignment_filter",
                    component_key=filter_id,
                )
            )
            continue

        if before_filter is not None and after_filter is None:
            if not target_trust:
                suppressions.append(
                    ComparisonSuppression(
                        category="assignment_filter_existence_unavailable",
                        scope="assignment_filter",
                        reason="target_filter_coverage_unavailable",
                    )
                )
                diffs.append(AssignmentFilterDiff(filter_id=filter_id, state="indeterminate"))
                continue
            diffs.append(
                AssignmentFilterDiff(
                    filter_id=filter_id,
                    state="removed",
                    before_semantic_hash=before_filter.semantic_hash,
                )
            )
            events.append(
                ChangeEvent(
                    event_type="assignment_filter_removed",
                    component_type="assignment_filter",
                    component_key=filter_id,
                )
            )
            continue

        assert before_filter is not None and after_filter is not None
        if before_filter.semantic_hash == after_filter.semantic_hash:
            diffs.append(
                AssignmentFilterDiff(
                    filter_id=filter_id,
                    state="unchanged",
                    before_semantic_hash=before_filter.semantic_hash,
                    after_semantic_hash=after_filter.semantic_hash,
                )
            )
            continue

        filter_diff = AssignmentFilterDiff(
            filter_id=filter_id,
            state="changed",
            before_semantic_hash=before_filter.semantic_hash,
            after_semantic_hash=after_filter.semantic_hash,
            changes=[
                ChangeEvent(
                    event_type="assignment_filter_changed",
                    component_type="assignment_filter",
                    component_key=filter_id,
                    before=before_filter.semantic,
                    after=after_filter.semantic,
                )
            ],
        )
        diffs.append(filter_diff)
        events.extend(filter_diff.changes)

    return diffs, events, suppressions


def compare_normalized_snapshots(
    baseline: SnapshotComparisonContext,
    target: SnapshotComparisonContext,
    *,
    allow_reverse: bool = False,
) -> ConfigurationPolicyComparison:
    started = time.perf_counter()

    if (
        not allow_reverse
        and _snapshot_sort_key(baseline.descriptor) > _snapshot_sort_key(target.descriptor)
    ):
        raise ValueError("baseline snapshot must be chronologically before or equal to target snapshot")

    comparison = ConfigurationPolicyComparison(
        baseline_snapshot=baseline.descriptor,
        target_snapshot=target.descriptor,
    )

    before_policies = {policy.policy_key: policy for policy in baseline.snapshot.policies}
    after_policies = {policy.policy_key: policy for policy in target.snapshot.policies}
    all_keys = sorted(set(before_policies) | set(after_policies))

    policy_events: list[ChangeEvent] = []

    for policy_key in all_keys:
        before_policy = before_policies.get(policy_key)
        after_policy = after_policies.get(policy_key)
        export_source = (before_policy or after_policy).export_source  # type: ignore[union-attr]

        if before_policy is None and after_policy is not None:
            if not is_source_existence_trustworthy(baseline.manifest, export_source):
                comparison.suppressions.append(
                    ComparisonSuppression(
                        category="baseline_source_coverage_unavailable",
                        scope="policy",
                        reason="cannot_infer_policy_added",
                        policy_key=policy_key,
                        export_source=export_source,
                    )
                )
                comparison.policy_diffs.append(
                    PolicyDiff(
                        policy_key=policy_key,
                        export_source=export_source,
                        state="indeterminate",
                    )
                )
                continue

            policy_diff = PolicyDiff(
                policy_key=policy_key,
                export_source=export_source,
                state="added",
                after_semantic_hash=after_policy.semantic_hash,
                semantic_hash_eligible_after=after_policy.coverage.semantic_hash_eligible,
                presentation_after=dict(after_policy.presentation),
            )
            event = ChangeEvent(
                event_type="policy_added",
                component_type="policy",
                policy_key=policy_key,
            )
            policy_diff.changes = [event]
            policy_events.append(event)
            comparison.policy_diffs.append(policy_diff)
            continue

        if before_policy is not None and after_policy is None:
            if not is_source_existence_trustworthy(target.manifest, export_source):
                comparison.suppressions.append(
                    ComparisonSuppression(
                        category="target_source_coverage_unavailable",
                        scope="policy",
                        reason="cannot_infer_policy_removed",
                        policy_key=policy_key,
                        export_source=export_source,
                    )
                )
                comparison.policy_diffs.append(
                    PolicyDiff(
                        policy_key=policy_key,
                        export_source=export_source,
                        state="indeterminate",
                    )
                )
                continue

            policy_diff = PolicyDiff(
                policy_key=policy_key,
                export_source=export_source,
                state="removed",
                before_semantic_hash=before_policy.semantic_hash,
                semantic_hash_eligible_before=before_policy.coverage.semantic_hash_eligible,
                presentation_before=dict(before_policy.presentation),
            )
            event = ChangeEvent(
                event_type="policy_removed",
                component_type="policy",
                policy_key=policy_key,
            )
            policy_diff.changes = [event]
            policy_events.append(event)
            comparison.policy_diffs.append(policy_diff)
            continue

        assert before_policy is not None and after_policy is not None
        policy_diff = _compare_existing_policy(before_policy, after_policy)
        comparison.policy_diffs.append(policy_diff)
        policy_events.extend(policy_diff.changes)

    filter_diffs, filter_events, filter_suppressions = _compare_assignment_filters(baseline, target)
    comparison.assignment_filter_diffs = filter_diffs
    comparison.suppressions.extend(filter_suppressions)

    all_events = policy_events + filter_events
    comparison.changes = sorted(all_events, key=lambda event: event.sort_key())
    comparison.suppressions = sorted(comparison.suppressions, key=lambda item: item.sort_key())
    comparison.policy_diffs = sorted(comparison.policy_diffs, key=lambda item: item.sort_key())

    comparison.summary = _build_summary(comparison)
    comparison.comparison_status = _resolve_comparison_status(comparison)
    comparison.summary["comparisonStatus"] = comparison.comparison_status
    comparison.comparison_duration_seconds = round(time.perf_counter() - started, 4)
    return comparison


def _resolve_comparison_status(comparison: ConfigurationPolicyComparison) -> ComparisonStatus:
    if comparison.summary.get("errors"):
        return "error"
    if comparison.suppressions:
        return "partial"
    return "success"


def _build_summary(comparison: ConfigurationPolicyComparison) -> dict[str, Any]:
    policy_counts = Counter(diff.state for diff in comparison.policy_diffs)
    filter_counts = Counter(diff.state for diff in comparison.assignment_filter_diffs)
    events_by_type = Counter(event.event_type for event in comparison.changes)
    suppression_categories = Counter(item.category for item in comparison.suppressions)

    return {
        "snapshotPair": {
            "baselineCapturedAtUtc": comparison.baseline_snapshot.captured_at_utc
            if comparison.baseline_snapshot
            else "",
            "targetCapturedAtUtc": comparison.target_snapshot.captured_at_utc
            if comparison.target_snapshot
            else "",
        },
        "policies": {
            "added": policy_counts.get("added", 0),
            "removed": policy_counts.get("removed", 0),
            "modified": policy_counts.get("modified", 0),
            "unchanged": policy_counts.get("unchanged", 0),
            "indeterminate": policy_counts.get("indeterminate", 0),
        },
        "assignmentFilters": {
            "added": filter_counts.get("added", 0),
            "removed": filter_counts.get("removed", 0),
            "changed": filter_counts.get("changed", 0),
            "unchanged": filter_counts.get("unchanged", 0),
            "indeterminate": filter_counts.get("indeterminate", 0),
        },
        "eventsByType": dict(sorted(events_by_type.items())),
        "suppressionCount": len(comparison.suppressions),
        "suppressionCategories": dict(sorted(suppression_categories.items())),
        "comparisonStatus": comparison.comparison_status,
        "errors": [],
    }


def compare_policy_bundles(
    baseline_path: Path | str,
    target_path: Path | str,
    *,
    allow_reverse: bool = False,
) -> ConfigurationPolicyComparison:
    baseline = load_snapshot_comparison_context(baseline_path)
    target = load_snapshot_comparison_context(target_path)
    return compare_normalized_snapshots(baseline, target, allow_reverse=allow_reverse)


def compare_latest_pair(root: Path | str) -> ConfigurationPolicyComparison | None:
    pair = select_latest_pair(root)
    if pair is None:
        return None
    baseline = load_snapshot_comparison_context(pair.baseline.path)
    target = load_snapshot_comparison_context(pair.target.path)
    return compare_normalized_snapshots(baseline, target)


def comparison_canonical_dict(comparison: ConfigurationPolicyComparison) -> dict[str, Any]:
    payload = comparison.to_dict()
    payload.pop("comparisonDurationSeconds", None)
    return payload


def comparison_canonical_json(comparison: ConfigurationPolicyComparison) -> str:
    return canonical_json(comparison_canonical_dict(comparison))


def summarize_comparison(comparison: ConfigurationPolicyComparison) -> dict[str, Any]:
    summary = dict(comparison.summary)
    summary["comparisonStatus"] = comparison.comparison_status
    summary["comparisonDurationSeconds"] = comparison.comparison_duration_seconds
    summary["semanticEventCount"] = len(comparison.changes)
    return summary
