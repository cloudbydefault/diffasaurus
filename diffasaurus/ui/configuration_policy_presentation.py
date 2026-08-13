"""Presentation helpers for Configuration Policies UI (Phase 3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from diffasaurus.core.configuration_policies.comparison_models import (
    ChangeEvent,
    ConfigurationPolicyComparison,
    PolicyDiff,
    SnapshotDescriptor,
)
from diffasaurus.core.configuration_policies.models import NormalizedPolicy, NormalizedSnapshot

EXPORT_SOURCE_LABELS = {
    "configurationPolicies": "Modern",
    "deviceConfigurations": "Classic",
    "groupPolicyConfigurations": "ADMX",
}

CHANGE_STATE_LABELS = {
    "added": "Added",
    "removed": "Removed",
    "modified": "Modified",
    "unchanged": "Unchanged",
    "indeterminate": "Indeterminate",
    "no_baseline": "No baseline",
}

EVENT_TYPE_LABELS = {
    "policy_added": "Policy added",
    "policy_removed": "Policy removed",
    "policy_renamed": "Policy renamed",
    "policy_description_changed": "Description changed",
    "setting_added": "Setting added",
    "setting_removed": "Setting removed",
    "setting_changed": "Setting changed",
    "assignment_added": "Assignment added",
    "assignment_removed": "Assignment removed",
    "scope_tags_changed": "Scope tags changed",
    "applicability_changed": "Applicability changed",
    "classic_property_added": "Observed property added",
    "classic_property_removed": "Observed property removed",
    "classic_property_changed": "Observed property changed",
    "admx_setting_added": "ADMX setting added",
    "admx_setting_removed": "ADMX setting removed",
    "admx_setting_changed": "ADMX setting changed",
    "assignment_filter_added": "Assignment filter added",
    "assignment_filter_removed": "Assignment filter removed",
    "assignment_filter_changed": "Assignment filter changed",
    "unexplained_policy_semantic_change": "Unexplained policy change",
}

COVERAGE_LABELS = {
    "success": "Available",
    "partial": "Partial",
    "error": "Unavailable",
    "not_applicable": "Not applicable",
    "unknown": "Unknown",
}

EXPORT_STATUS_LABELS = {
    "complete": "Complete",
    "incomplete": "Incomplete",
    "integrity_error": "Integrity error",
}

NORMALIZATION_STATUS_LABELS = {
    "success": "Success",
    "partial": "Partial",
    "error": "Error",
}

ASSIGNMENT_TARGET_LABELS = {
    "all_devices": "All devices",
    "all_users": "All users",
    "include_group": "Included group",
    "exclude_group": "Excluded group",
    "unknown": "Unknown target",
}


@dataclass
class SettingTreeRow:
    label: str
    value: str
    kind: str
    tooltip: str = ""
    warning: str = ""
    children: list[SettingTreeRow] = field(default_factory=list)


@dataclass
class PolicyInventoryRow:
    policy_key: str
    name: str
    platform: str
    policy_type: str
    source_label: str
    assignment_count: int
    change_state: str
    change_label: str
    search_text: str


@dataclass
class ConfigurationPolicyPageModel:
    discovery_diagnostics_count: int
    snapshots: list[SnapshotDescriptor]
    selected_snapshot: SnapshotDescriptor | None
    previous_snapshot: SnapshotDescriptor | None
    normalized: NormalizedSnapshot | None
    comparison: ConfigurationPolicyComparison | None
    normalization_error: str | None = None
    comparison_error: str | None = None
    policy_diff_by_key: dict[str, PolicyDiff] = field(default_factory=dict)
    filter_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    inventory_rows: list[PolicyInventoryRow] = field(default_factory=list)

    @property
    def policy_count(self) -> int:
        return len(self.normalized.policies) if self.normalized else 0

    @property
    def setting_count(self) -> int:
        return count_semantic_settings(self.normalized) if self.normalized else 0

    @property
    def assignment_count(self) -> int:
        if not self.normalized:
            return 0
        return sum(len(policy.assignments) for policy in self.normalized.policies)

    @property
    def change_summary(self) -> tuple[str, str]:
        if self.comparison_error:
            return ("—", "Comparison unavailable")
        if not self.previous_snapshot:
            return ("—", "No earlier policy snapshot")
        if not self.comparison:
            return ("—", "No comparison")
        summary = self.comparison.summary.get("policies", {})
        events = len(self.comparison.changes)
        modified = int(summary.get("modified", 0))
        added = int(summary.get("added", 0))
        removed = int(summary.get("removed", 0))
        detail = f"{events} events · {added} added · {removed} removed · {modified} modified"
        if self.comparison.comparison_status == "partial":
            detail += " · partial coverage"
        return (str(events), detail)


def parse_snapshot_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_snapshot_selector_label(descriptor: SnapshotDescriptor) -> str:
    parsed = parse_snapshot_datetime(descriptor.captured_at_utc)
    if parsed:
        stamp = parsed.strftime("%d %b %Y · %H:%M")
    else:
        stamp = descriptor.captured_at_utc
    status = EXPORT_STATUS_LABELS.get(descriptor.export_status, descriptor.export_status)
    return f"{stamp} · {status}"


def format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        if not value:
            return "—"
        return ", ".join(format_value(item) for item in value)
    if isinstance(value, dict):
        if not value:
            return "—"
        parts = [f"{key}: {format_value(item)}" for key, item in sorted(value.items())]
        return "; ".join(parts)
    return str(value)


def humanize_property_path(path: str) -> str:
    parts = path.split(".")
    words: list[str] = []
    for part in parts:
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", part)
        words.append(spaced.replace("_", " ").strip().capitalize())
    return " ".join(words)


def export_source_label(export_source: str) -> str:
    return EXPORT_SOURCE_LABELS.get(export_source, export_source or "Unknown")


def event_type_label(event_type: str) -> str:
    return EVENT_TYPE_LABELS.get(event_type, event_type.replace("_", " ").title())


def coverage_label(status: str) -> str:
    return COVERAGE_LABELS.get(status, status or "Unknown")


def assignment_target_label(target_kind: str) -> str:
    return ASSIGNMENT_TARGET_LABELS.get(target_kind, target_kind)


def filter_mode_label(filter_type: str | None) -> str:
    if filter_type == "include":
        return "Include"
    if filter_type == "exclude":
        return "Exclude"
    return "—"


def resolve_filter_presentation(
    filter_id: str | None,
    filters_by_id: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    if not filter_id:
        return ("—", "")
    entry = filters_by_id.get(filter_id, {})
    presentation = entry.get("presentation") if isinstance(entry, dict) else {}
    if isinstance(presentation, dict) and presentation.get("displayName"):
        return (str(presentation["displayName"]), filter_id)
    semantic = entry.get("semantic") if isinstance(entry, dict) else {}
    if isinstance(semantic, dict) and semantic.get("rule"):
        return (filter_id, str(semantic.get("rule", "")))
    return (filter_id, "")


def count_semantic_settings(snapshot: NormalizedSnapshot | None) -> int:
    if snapshot is None:
        return 0
    total = 0
    for policy in snapshot.policies:
        settings = policy.settings
        kind = settings.get("kind")
        if kind == "modern":
            total += _count_modern_nodes(settings)
        elif kind == "classic":
            total += len(settings.get("properties", []))
        elif kind == "admx":
            total += len(settings.get("settings", []))
    return total


def _count_modern_nodes(settings: dict[str, Any]) -> int:
    count = 0

    def visit(node: dict[str, Any]) -> None:
        nonlocal count
        count += 1
        for value in node.get("values", []):
            if not isinstance(value, dict):
                continue
            for child in value.get("children", []):
                if isinstance(child, dict):
                    visit(child)

    for node in settings.get("nodes", []):
        if isinstance(node, dict):
            visit(node)
    return count


def build_modern_setting_tree(node: dict[str, Any]) -> SettingTreeRow:
    presentation = node.get("presentation") if isinstance(node.get("presentation"), dict) else {}
    label = str(presentation.get("displayName") or node.get("definitionId") or "Setting")
    kind = str(node.get("kind", "unknown"))
    warning = ""
    if kind == "unknown":
        warning = "Unsupported setting shape — raw semantic content preserved"
    elif "unknown_modern_setting_instance_type" in (node.get("warnings") or []):
        warning = "Unsupported setting shape — raw semantic content preserved"

    children: list[SettingTreeRow] = []
    values = node.get("values") or []
    if kind == "group_collection":
        for index, value in enumerate(values, start=1):
            if not isinstance(value, dict):
                continue
            entry_children = [
                build_modern_setting_tree(child)
                for child in value.get("children", [])
                if isinstance(child, dict)
            ]
            children.append(
                SettingTreeRow(
                    label=f"Entry {index}",
                    value="",
                    kind="group_entry",
                    children=entry_children,
                )
            )
    else:
        primary = values[0] if values else {}
        if isinstance(primary, dict):
            display = primary.get("displayValue")
            raw = primary.get("rawValue")
            value_text = format_value(display if display not in (None, "") else raw)
            tooltip = format_value(raw) if display not in (None, "") else ""
            child_nodes = [
                build_modern_setting_tree(child)
                for child in primary.get("children", [])
                if isinstance(child, dict)
            ]
            return SettingTreeRow(
                label=label,
                value=value_text,
                kind=kind,
                tooltip=tooltip,
                warning=warning,
                children=child_nodes,
            )

    return SettingTreeRow(label=label, value="", kind=kind, warning=warning, children=children)


def build_inventory_rows(
    normalized: NormalizedSnapshot,
    policy_diff_by_key: dict[str, PolicyDiff],
    *,
    has_baseline: bool,
) -> list[PolicyInventoryRow]:
    rows: list[PolicyInventoryRow] = []
    for policy in normalized.policies:
        diff = policy_diff_by_key.get(policy.policy_key)
        if diff is not None:
            change_state = diff.state
        elif has_baseline:
            change_state = "unchanged"
        else:
            change_state = "no_baseline"
        change_label = CHANGE_STATE_LABELS.get(change_state, change_state)
        presentation = policy.presentation
        name = str(presentation.get("name") or policy.semantic_metadata.get("name") or "Unnamed policy")
        platform = str(presentation.get("platform") or "")
        policy_type = str(presentation.get("policyType") or "")
        source_label = export_source_label(policy.export_source)
        search_text = " ".join(
            [
                name,
                str(presentation.get("description") or ""),
                platform,
                policy_type,
                source_label,
                policy.export_source,
            ]
        ).casefold()
        rows.append(
            PolicyInventoryRow(
                policy_key=policy.policy_key,
                name=name,
                platform=platform,
                policy_type=policy_type,
                source_label=source_label,
                assignment_count=len(policy.assignments),
                change_state=change_state,
                change_label=change_label,
                search_text=search_text,
            )
        )
    rows.sort(key=lambda item: (item.platform, item.name.casefold(), item.policy_key))
    return rows


def filter_inventory_rows(
    rows: list[PolicyInventoryRow],
    *,
    search: str = "",
    platform: str = "All",
    source: str = "All",
    change: str = "All",
) -> list[PolicyInventoryRow]:
    query = search.strip().casefold()
    filtered: list[PolicyInventoryRow] = []
    for row in rows:
        if platform != "All" and row.platform != platform:
            continue
        if source != "All" and row.source_label != source:
            continue
        if change != "All" and row.change_label != change:
            continue
        if query and query not in row.search_text:
            continue
        filtered.append(row)
    return filtered


def policy_events(
    policy_key: str,
    comparison: ConfigurationPolicyComparison | None,
) -> list[ChangeEvent]:
    if comparison is None:
        return []
    diff = next((item for item in comparison.policy_diffs if item.policy_key == policy_key), None)
    if diff is None:
        return []
    return list(diff.changes)


def build_page_model(
    *,
    snapshots: list[SnapshotDescriptor],
    diagnostics_count: int,
    selected: SnapshotDescriptor | None,
    previous: SnapshotDescriptor | None,
    normalized: NormalizedSnapshot | None,
    comparison: ConfigurationPolicyComparison | None,
    normalization_error: str | None = None,
    comparison_error: str | None = None,
) -> ConfigurationPolicyPageModel:
    policy_diff_by_key: dict[str, PolicyDiff] = {}
    if comparison is not None:
        policy_diff_by_key = {item.policy_key: item for item in comparison.policy_diffs}
    filters_by_id: dict[str, dict[str, Any]] = {}
    if normalized is not None:
        filters_by_id = {item.filter_id: item.to_dict() for item in normalized.assignment_filters}
    inventory_rows = (
        build_inventory_rows(normalized, policy_diff_by_key, has_baseline=previous is not None)
        if normalized is not None
        else []
    )
    return ConfigurationPolicyPageModel(
        discovery_diagnostics_count=diagnostics_count,
        snapshots=snapshots,
        selected_snapshot=selected,
        previous_snapshot=previous,
        normalized=normalized,
        comparison=comparison,
        normalization_error=normalization_error,
        comparison_error=comparison_error,
        policy_diff_by_key=policy_diff_by_key,
        filter_by_id=filters_by_id,
        inventory_rows=inventory_rows,
    )
