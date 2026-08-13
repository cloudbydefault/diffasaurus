"""Configuration Policy integration with generic Diffasaurus shell (Phase 4)."""

from __future__ import annotations

import json
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from diffasaurus.core.configuration_policies import (
    compare_policy_bundles,
    discover_policy_snapshots,
    normalize_bundle,
)
from diffasaurus.core.configuration_policies.comparison_models import (
    ChangeEvent,
    ConfigurationPolicyComparison,
    DiscoveryDiagnostic,
    SnapshotDescriptor,
)
from diffasaurus.core.configuration_policies.constants import (
    CONFIGURATION_POLICY_FAMILY,
    INFORMATIONAL_NORMALIZATION_WARNINGS,
    TRUST_LIMITING_NORMALIZATION_WARNINGS,
)
from diffasaurus.core.configuration_policies.history import (
    _parse_captured_at_utc,
    _snapshot_sort_key,
)
from diffasaurus.core.configuration_policies.models import NormalizedSnapshot
from diffasaurus.core.report_history import (
    REASON_NO_BASELINE,
    REASON_NOT_ENOUGH_SNAPSHOTS,
    REASON_SINGLE_SNAPSHOT,
    REASON_STALE_LATEST,
    REASON_UNABLE_TO_COMPARE,
    ComparisonSummary,
    FamilyChangeStatus,
    PeriodPairResult,
    ReportSnapshot,
    period_window,
    read_csv_rows,
)

ENTRA_GROUPS_FAMILY = "Entra_Groups_Dependencies"
GROUP_ID_HEADER = "GroupId"
GROUP_NAME_HEADERS = ("DisplayName", "displayName", "Name", "name")
_EXPORT_SOURCE_LABELS = {
    "configurationPolicies": "Modern",
    "deviceConfigurations": "Classic",
    "groupPolicyConfigurations": "ADMX",
}


class ConfigurationPolicyCsvComparisonError(RuntimeError):
    """Raised when generic CSV comparison is attempted for Configuration Policies."""


def is_configuration_policy_family(family: str | None) -> bool:
    return family == CONFIGURATION_POLICY_FAMILY


def guard_generic_csv_comparison(family: str | None) -> None:
    if is_configuration_policy_family(family):
        raise ConfigurationPolicyCsvComparisonError(
            "Intune Configuration Policies must use semantic bundle comparison, not CSV row diff."
        )


def anchor_snapshot_id(anchor: ReportSnapshot | Path) -> str:
    path = anchor if isinstance(anchor, Path) else anchor.path
    return path.stem


def discovery_index(report_dir: Path | str) -> dict[str, SnapshotDescriptor]:
    result = discover_policy_snapshots(report_dir)
    return {descriptor.snapshot_id: descriptor for descriptor in result.snapshots}


def resolve_bundle_for_anchor(
    report_dir: Path | str,
    anchor: ReportSnapshot | Path,
    *,
    index: dict[str, SnapshotDescriptor] | None = None,
) -> SnapshotDescriptor | None:
    snapshot_id = anchor_snapshot_id(anchor)
    mapping = index if index is not None else discovery_index(report_dir)
    return mapping.get(snapshot_id)


def resolve_anchor_for_descriptor(
    anchors: list[ReportSnapshot],
    descriptor: SnapshotDescriptor,
) -> ReportSnapshot | None:
    for anchor in anchors:
        if anchor_snapshot_id(anchor) == descriptor.snapshot_id:
            return anchor
    return None


def bundle_freshness_signature(descriptor: SnapshotDescriptor) -> str:
    manifest = Path(descriptor.path) / "snapshot_manifest.json"
    try:
        stat = manifest.stat()
        return f"{descriptor.snapshot_id}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return descriptor.snapshot_id


@dataclass
class PolicySessionCache:
    report_dir: str = ""
    normalized_by_path: dict[str, NormalizedSnapshot] = field(default_factory=dict)
    comparison_by_pair: dict[tuple[str, str], ConfigurationPolicyComparison] = field(
        default_factory=dict
    )
    freshness_by_path: dict[str, str] = field(default_factory=dict)
    group_name_by_snapshot: dict[str, dict[str, str]] = field(default_factory=dict)

    def invalidate(self, report_dir: Path | str | None = None) -> None:
        if report_dir is not None:
            self.report_dir = str(Path(report_dir).resolve())
        self.normalized_by_path.clear()
        self.comparison_by_pair.clear()
        self.freshness_by_path.clear()
        self.group_name_by_snapshot.clear()

    def get_normalized(self, descriptor: SnapshotDescriptor) -> NormalizedSnapshot:
        signature = bundle_freshness_signature(descriptor)
        cached = self.normalized_by_path.get(descriptor.path)
        if cached is not None and self.freshness_by_path.get(descriptor.path) == signature:
            return cached
        normalized = normalize_bundle(descriptor.path)
        self.normalized_by_path[descriptor.path] = normalized
        self.freshness_by_path[descriptor.path] = signature
        return normalized

    def compare(
        self,
        baseline: SnapshotDescriptor,
        target: SnapshotDescriptor,
    ) -> ConfigurationPolicyComparison:
        key = (baseline.path, target.path)
        baseline_sig = bundle_freshness_signature(baseline)
        target_sig = bundle_freshness_signature(target)
        pair_sig = (baseline_sig, target_sig)
        cached = self.comparison_by_pair.get(key)
        if cached is not None and getattr(cached, "_freshness_sig", None) == pair_sig:
            return cached
        comparison = compare_policy_bundles(baseline.path, target.path)
        comparison._freshness_sig = pair_sig  # type: ignore[attr-defined]
        self.comparison_by_pair[key] = comparison
        return comparison


POLICY_SESSION_CACHE = PolicySessionCache()


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


@dataclass(frozen=True)
class PolicyPeriodPair:
    baseline: SnapshotDescriptor | None
    target: SnapshotDescriptor | None
    baseline_anchor: ReportSnapshot | None
    target_anchor: ReportSnapshot | None
    reason: str
    reference: datetime
    cutoff: datetime


def resolve_policy_period_pair(
    report_dir: Path | str,
    anchors: list[ReportSnapshot],
    period,
    reference: datetime | None = None,
    *,
    index: dict[str, SnapshotDescriptor] | None = None,
) -> PolicyPeriodPair:
    reference_at, cutoff = period_window(period, reference)
    mapping = index if index is not None else discovery_index(report_dir)
    descriptors = [
        mapping[anchor_snapshot_id(anchor)]
        for anchor in anchors
        if anchor_snapshot_id(anchor) in mapping
    ]
    descriptors.sort(key=_snapshot_sort_key)

    if not descriptors:
        return PolicyPeriodPair(
            None,
            None,
            None,
            None,
            REASON_NOT_ENOUGH_SNAPSHOTS,
            reference_at,
            cutoff,
        )
    if len(descriptors) < 2:
        anchor = resolve_anchor_for_descriptor(anchors, descriptors[-1])
        return PolicyPeriodPair(
            None,
            descriptors[-1],
            None,
            anchor,
            REASON_NO_BASELINE,
            reference_at,
            cutoff,
        )

    target = descriptors[-1]
    target_anchor = resolve_anchor_for_descriptor(anchors, target)
    if target.captured_at_utc:
        target_dt = _parse_captured_at_utc(target.captured_at_utc)
    else:
        target_dt = target_anchor.captured_at if target_anchor else None
    cutoff_cmp = _as_naive_utc(cutoff)
    if target_dt and _as_naive_utc(target_dt) <= cutoff_cmp:
        return PolicyPeriodPair(
            None,
            target,
            None,
            target_anchor,
            REASON_STALE_LATEST,
            reference_at,
            cutoff,
        )

    baseline: SnapshotDescriptor | None = None
    baseline_anchor: ReportSnapshot | None = None
    for descriptor in reversed(descriptors[:-1]):
        captured = _parse_captured_at_utc(descriptor.captured_at_utc)
        if captured is None:
            continue
        if _as_naive_utc(captured) <= cutoff_cmp:
            baseline = descriptor
            baseline_anchor = resolve_anchor_for_descriptor(anchors, descriptor)
            break

    if baseline is None:
        return PolicyPeriodPair(
            None,
            target,
            None,
            target_anchor,
            REASON_NO_BASELINE,
            reference_at,
            cutoff,
        )

    if baseline.snapshot_id == target.snapshot_id:
        return PolicyPeriodPair(
            baseline,
            target,
            baseline_anchor,
            target_anchor,
            REASON_SINGLE_SNAPSHOT,
            reference_at,
            cutoff,
        )

    return PolicyPeriodPair(
        baseline,
        target,
        baseline_anchor,
        target_anchor,
        "",
        reference_at,
        cutoff,
    )


@dataclass(frozen=True)
class PolicyFamilySummary:
    added: int
    removed: int
    modified: int
    unchanged: int
    indeterminate: int
    event_count: int
    suppression_count: int
    comparison_status: str

    @property
    def count_text(self) -> str:
        return (
            f"{self.added} added · {self.removed} removed · "
            f"{self.modified} modified · {self.indeterminate} indeterminate"
        )


def summarize_policy_comparison(comparison: ConfigurationPolicyComparison) -> PolicyFamilySummary:
    summary = comparison.summary.get("policies", {})
    return PolicyFamilySummary(
        added=int(summary.get("added", 0)),
        removed=int(summary.get("removed", 0)),
        modified=int(summary.get("modified", 0)),
        unchanged=int(summary.get("unchanged", 0)),
        indeterminate=int(summary.get("indeterminate", 0)),
        event_count=len(comparison.changes),
        suppression_count=len(comparison.suppressions),
        comparison_status=comparison.comparison_status,
    )


def policy_status_from_comparison(
    comparison: ConfigurationPolicyComparison,
) -> Literal["changed", "unchanged", "partial"]:
    summary = summarize_policy_comparison(comparison)
    if comparison.comparison_status == "partial" or summary.indeterminate > 0:
        return "partial"
    if summary.event_count or summary.added or summary.removed or summary.modified:
        return "changed"
    return "unchanged"


def comparison_to_csv_summary(comparison: ConfigurationPolicyComparison) -> ComparisonSummary:
    summary = summarize_policy_comparison(comparison)
    return ComparisonSummary(
        added=summary.added,
        removed=summary.removed,
        changed=summary.modified,
        stable=summary.unchanged,
        details=(),
    )


def _serialize_semantic_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _policy_display_name_from_comparison(
    comparison: ConfigurationPolicyComparison,
    policy_key: str | None,
    *,
    before: bool,
) -> str:
    if not policy_key:
        return ""
    diff = next((item for item in comparison.policy_diffs if item.policy_key == policy_key), None)
    if diff is None:
        return policy_key
    presentation = diff.presentation_before if before else diff.presentation_after
    return str(presentation.get("name") or policy_key)


def build_semantic_event_details(
    comparison: ConfigurationPolicyComparison,
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for event in comparison.changes:
        policy_name = _policy_display_name_from_comparison(
            comparison,
            event.policy_key,
            before=event.event_type.endswith("_removed"),
        )
        rows.append(
            {
                "event_type": event.event_type,
                "policy_key": event.policy_key or "",
                "policy_name": policy_name,
                "component_type": event.component_type,
                "component_key": event.component_key or "",
                "before": _serialize_semantic_value(event.before),
                "after": _serialize_semantic_value(event.after),
            }
        )
    return tuple(rows)


def policy_run_health_observation(
    report_dir: Path | str,
    anchor: ReportSnapshot,
    *,
    index: dict[str, SnapshotDescriptor] | None = None,
) -> tuple[ReportSnapshot | None, bool]:
    descriptor = resolve_bundle_for_anchor(report_dir, anchor, index=index)
    if descriptor is None:
        return None, False
    if descriptor.export_status == "complete":
        return anchor, False
    if descriptor.export_status == "incomplete":
        return anchor, True
    return None, False


def configuration_policy_family_change_status(
    report_dir: Path | str,
    anchors: list[ReportSnapshot],
    period,
    reference: datetime | None = None,
    *,
    include_details: bool = False,
    cache: PolicySessionCache | None = None,
) -> FamilyChangeStatus:
    pairing = resolve_policy_period_pair(report_dir, anchors, period, reference)
    if pairing.reason:
        return FamilyChangeStatus(
            family=CONFIGURATION_POLICY_FAMILY,
            status="no_data",
            baseline=pairing.baseline_anchor,
            latest=pairing.target_anchor,
            key_column="",
            summary=None,
            reason=pairing.reason,
            policy_summary=None,
            semantic_details=(),
            partial_coverage=False,
        )

    assert pairing.baseline is not None and pairing.target is not None
    session = cache or POLICY_SESSION_CACHE
    try:
        comparison = session.compare(pairing.baseline, pairing.target)
    except Exception:
        return FamilyChangeStatus(
            family=CONFIGURATION_POLICY_FAMILY,
            status="no_data",
            baseline=pairing.baseline_anchor,
            latest=pairing.target_anchor,
            key_column="",
            summary=None,
            reason=REASON_UNABLE_TO_COMPARE,
            policy_summary=None,
            semantic_details=(),
            partial_coverage=False,
        )

    policy_summary = summarize_policy_comparison(comparison)
    status = policy_status_from_comparison(comparison)
    generic_status: Literal["changed", "unchanged", "no_data", "partial"]
    if status == "partial":
        generic_status = "partial"
    elif status == "changed":
        generic_status = "changed"
    else:
        generic_status = "unchanged"

    details = build_semantic_event_details(comparison) if include_details else ()
    return FamilyChangeStatus(
        family=CONFIGURATION_POLICY_FAMILY,
        status=generic_status,
        baseline=pairing.baseline_anchor,
        latest=pairing.target_anchor,
        key_column="",
        summary=comparison_to_csv_summary(comparison),
        reason="",
        policy_summary=policy_summary,
        semantic_details=details,
        partial_coverage=status == "partial",
        policy_comparison=comparison,
        policy_baseline_descriptor=pairing.baseline,
        policy_target_descriptor=pairing.target,
    )


def _count_semantic_settings(snapshot: NormalizedSnapshot) -> int:
    total = 0
    for policy in snapshot.policies:
        settings = policy.settings
        kind = settings.get("kind")
        if kind == "modern":
            total += _count_modern_setting_nodes(settings)
        elif kind == "classic":
            total += len(settings.get("properties", []))
        elif kind == "admx":
            total += len(settings.get("settings", []))
    return total


def _count_modern_setting_nodes(settings: dict[str, Any]) -> int:
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


def build_policy_metric_history(
    report_dir: Path | str,
    anchors: list[ReportSnapshot],
    *,
    cache: PolicySessionCache | None = None,
) -> list[tuple[ReportSnapshot, dict[str, float]]]:
    session = cache or POLICY_SESSION_CACHE
    index = discovery_index(report_dir)
    history: list[tuple[ReportSnapshot, dict[str, float]]] = []
    for anchor in anchors:
        descriptor = index.get(anchor_snapshot_id(anchor))
        if descriptor is None:
            continue
        normalized = session.get_normalized(descriptor)
        modern = sum(1 for p in normalized.policies if p.export_source == "configurationPolicies")
        classic = sum(1 for p in normalized.policies if p.export_source == "deviceConfigurations")
        admx = sum(1 for p in normalized.policies if p.export_source == "groupPolicyConfigurations")
        eligible = sum(1 for p in normalized.policies if p.coverage.semantic_hash_eligible)
        metrics = {
            "Policies": float(len(normalized.policies)),
            "Settings": float(_count_semantic_settings(normalized)),
            "Assignments": float(sum(len(p.assignments) for p in normalized.policies)),
            "Modern policies": float(modern),
            "Classic policies": float(classic),
            "ADMX policies": float(admx),
            "Hash-eligible policies": float(eligible),
        }
        history.append((anchor, metrics))
    return history


def build_policy_movement(
    report_dir: Path | str,
    anchors: list[ReportSnapshot],
    *,
    max_intervals: int = 12,
    cache: PolicySessionCache | None = None,
) -> tuple[list[tuple[str, int, int, int]], ComparisonSummary | None]:
    session = cache or POLICY_SESSION_CACHE
    index = discovery_index(report_dir)
    descriptors = [
        index[anchor_snapshot_id(anchor)]
        for anchor in anchors
        if anchor_snapshot_id(anchor) in index
    ]
    descriptors.sort(key=_snapshot_sort_key)
    movement: list[tuple[str, int, int, int]] = []
    latest_summary: ComparisonSummary | None = None
    pairs = list(zip(descriptors[:-1], descriptors[1:]))[-max_intervals:]
    for baseline, target in pairs:
        try:
            comparison = session.compare(baseline, target)
        except Exception:
            continue
        summary = summarize_policy_comparison(comparison)
        if comparison.comparison_status == "partial":
            continue
        label = _parse_captured_at_utc(target.captured_at_utc)
        label_text = label.strftime("%d %b") if label else target.snapshot_id[-8:]
        movement.append((label_text, summary.added, summary.removed, summary.modified))
        latest_summary = comparison_to_csv_summary(comparison)
    return movement, latest_summary


@dataclass(frozen=True)
class TrustBannerPresentation:
    level: Literal["success", "informational", "warning", "error"]
    headline: str
    detail: str


def classify_trust_banner(
    export_status: str,
    normalized: NormalizedSnapshot,
) -> TrustBannerPresentation:
    if export_status == "integrity_error" or normalized.normalization_status == "error":
        return TrustBannerPresentation(
            "error",
            "Snapshot integrity or normalization error detected.",
            "Review coverage before trusting changes.",
        )
    if export_status == "incomplete":
        return TrustBannerPresentation(
            "warning",
            "Snapshot export is incomplete.",
            "Policies shown were captured where available, but overall trust is limited.",
        )

    trust_limiting: list[str] = []
    informational = 0
    for policy in normalized.policies:
        for warning in policy.coverage.normalization_warnings:
            if warning in TRUST_LIMITING_NORMALIZATION_WARNINGS:
                trust_limiting.append(warning)
            elif warning in INFORMATIONAL_NORMALIZATION_WARNINGS:
                informational += 1
        if not policy.coverage.semantic_hash_eligible:
            trust_limiting.extend(policy.coverage.semantic_hash_blockers)
    for warning in normalized.normalization_warnings:
        if warning in TRUST_LIMITING_NORMALIZATION_WARNINGS:
            trust_limiting.append(warning)
        elif warning in INFORMATIONAL_NORMALIZATION_WARNINGS:
            informational += 1

    if trust_limiting:
        return TrustBannerPresentation(
            "warning",
            "Snapshot or normalization coverage is partial.",
            "Some policy semantics may be incomplete or unavailable.",
        )

    if export_status == "complete" and all(
        policy.coverage.semantic_hash_eligible for policy in normalized.policies
    ):
        if informational:
            return TrustBannerPresentation(
                "informational",
                "Snapshot complete · semantic coverage available",
                f"{informational} informational normalization warning"
                f"{'s' if informational != 1 else ''}",
            )
        return TrustBannerPresentation(
            "success",
            "Snapshot complete · normalization successful",
            "",
        )

    return TrustBannerPresentation(
        "informational",
        "Snapshot complete · semantic coverage available",
        f"{informational} informational normalization warning"
        f"{'s' if informational != 1 else ''}" if informational else "",
    )


def _entra_group_snapshots(report_dir: Path) -> list[ReportSnapshot]:
    from diffasaurus.core.report_history import scan_report_index

    families = scan_report_index(report_dir)
    return list(families.get(ENTRA_GROUPS_FAMILY, []))


def _group_name_map(snapshot: ReportSnapshot) -> dict[str, str]:
    try:
        headers, rows = read_csv_rows(snapshot.path)
    except OSError:
        return {}
    header_map = {header.casefold(): header for header in headers}
    group_id_header = header_map.get(GROUP_ID_HEADER.casefold())
    if not group_id_header:
        return {}
    name_header = next(
        (header_map[candidate.casefold()] for candidate in GROUP_NAME_HEADERS if candidate.casefold() in header_map),
        None,
    )
    if not name_header:
        return {}
    mapping: dict[str, str] = {}
    for row in rows:
        group_id = str(row.get(group_id_header, "")).strip()
        display_name = str(row.get(name_header, "")).strip()
        if group_id and display_name:
            mapping[group_id] = display_name
    return mapping


def resolve_group_display_name(
    report_dir: Path | str,
    group_id: str | None,
    policy_captured_at: datetime,
    *,
    cache: PolicySessionCache | None = None,
) -> str:
    if not group_id:
        return ""
    session = cache or POLICY_SESSION_CACHE
    snapshots = _entra_group_snapshots(Path(report_dir))
    policy_time = _as_naive_utc(policy_captured_at)
    eligible = [
        item for item in snapshots if _as_naive_utc(item.captured_at) <= policy_time
    ]
    if not eligible:
        return group_id
    chosen = eligible[-1]
    cache_key = str(chosen.path)
    if cache_key not in session.group_name_by_snapshot:
        session.group_name_by_snapshot[cache_key] = _group_name_map(chosen)
    return session.group_name_by_snapshot[cache_key].get(group_id, group_id)


def anchor_bundle_status(
    report_dir: Path | str,
    family: str,
    latest_anchor: ReportSnapshot | None,
    *,
    index: dict[str, SnapshotDescriptor] | None = None,
    legacy_count: int = 0,
) -> tuple[bool, str]:
    if not is_configuration_policy_family(family):
        return True, ""
    if latest_anchor is None:
        return False, "Missing"
    descriptor = resolve_bundle_for_anchor(report_dir, latest_anchor, index=index)
    if descriptor is None:
        if legacy_count:
            return False, "⚠ Legacy policy export"
        return False, "⚠ Policy bundle incomplete/unreadable"
    if descriptor.export_status == "complete":
        return True, ""
    if descriptor.export_status == "incomplete":
        return True, "⚠ Incomplete bundle"
    return False, "⚠ Policy bundle incomplete/unreadable"


def legacy_configuration_policy_diagnostics(
    diagnostics: list[DiscoveryDiagnostic],
) -> int:
    return sum(1 for item in diagnostics if item.category == "legacy_configuration_policy_export")


def compact_policy_inventory_rows(
    normalized: NormalizedSnapshot,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for policy in normalized.policies:
        presentation = policy.presentation
        rows.append(
            {
                "policy_key": policy.policy_key,
                "name": str(presentation.get("name") or "Unnamed policy"),
                "platform": str(presentation.get("platform") or ""),
                "policy_type": str(presentation.get("policyType") or ""),
                "source": _EXPORT_SOURCE_LABELS.get(policy.export_source, policy.export_source),
            }
        )
    rows.sort(key=lambda item: (item["platform"], item["name"].casefold(), item["policy_key"]))
    return rows
