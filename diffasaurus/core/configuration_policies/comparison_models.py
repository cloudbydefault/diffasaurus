"""Dataclasses for Configuration Policy semantic comparison (Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

COMPARISON_SCHEMA_VERSION = 1

PolicyDiffState = Literal["added", "removed", "modified", "unchanged", "indeterminate"]
FilterDiffState = Literal["added", "removed", "changed", "unchanged", "indeterminate"]
ComparisonStatus = Literal["success", "partial", "error"]


@dataclass
class SnapshotDescriptor:
    path: str
    snapshot_id: str
    captured_at_utc: str
    snapshot_schema_version: int
    policy_export_schema_version: int
    export_status: str
    policy_count: int
    source_coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "snapshotId": self.snapshot_id,
            "capturedAtUtc": self.captured_at_utc,
            "snapshotSchemaVersion": self.snapshot_schema_version,
            "policyExportSchemaVersion": self.policy_export_schema_version,
            "exportStatus": self.export_status,
            "policyCount": self.policy_count,
            "sourceCoverage": self.source_coverage,
        }


@dataclass
class DiscoveryDiagnostic:
    path: str
    category: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "category": self.category,
            "message": self.message,
        }


@dataclass
class DiscoveryResult:
    snapshots: list[SnapshotDescriptor] = field(default_factory=list)
    diagnostics: list[DiscoveryDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshots": [item.to_dict() for item in self.snapshots],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass
class ComparisonSuppression:
    category: str
    scope: str
    reason: str
    policy_key: str | None = None
    export_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "category": self.category,
            "scope": self.scope,
            "reason": self.reason,
        }
        if self.policy_key is not None:
            payload["policyKey"] = self.policy_key
        if self.export_source is not None:
            payload["exportSource"] = self.export_source
        return payload

    def sort_key(self) -> tuple[str, ...]:
        return (
            self.category,
            self.scope,
            self.reason,
            self.policy_key or "",
            self.export_source or "",
        )


@dataclass
class ChangeEvent:
    event_type: str
    component_type: str
    policy_key: str | None = None
    component_key: str | None = None
    before: Any = None
    after: Any = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "eventType": self.event_type,
            "componentType": self.component_type,
        }
        if self.policy_key is not None:
            payload["policyKey"] = self.policy_key
        if self.component_key is not None:
            payload["componentKey"] = self.component_key
        if self.before is not None:
            payload["before"] = self.before
        if self.after is not None:
            payload["after"] = self.after
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload

    def sort_key(self) -> tuple[str, ...]:
        return (
            self.policy_key or "",
            self.component_type,
            self.component_key or "",
            self.event_type,
        )


@dataclass
class AssignmentFilterDiff:
    filter_id: str
    state: FilterDiffState
    before_semantic_hash: str = ""
    after_semantic_hash: str = ""
    changes: list[ChangeEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suppressions: list[ComparisonSuppression] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filterId": self.filter_id,
            "state": self.state,
            "beforeSemanticHash": self.before_semantic_hash,
            "afterSemanticHash": self.after_semantic_hash,
            "changes": [event.to_dict() for event in self.changes],
            "warnings": list(self.warnings),
            "suppressions": [item.to_dict() for item in self.suppressions],
        }


@dataclass
class PolicyDiff:
    policy_key: str
    export_source: str
    state: PolicyDiffState
    before_semantic_hash: str = ""
    after_semantic_hash: str = ""
    semantic_hash_eligible_before: bool = False
    semantic_hash_eligible_after: bool = False
    presentation_before: dict[str, Any] = field(default_factory=dict)
    presentation_after: dict[str, Any] = field(default_factory=dict)
    changes: list[ChangeEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suppressions: list[ComparisonSuppression] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policyKey": self.policy_key,
            "exportSource": self.export_source,
            "state": self.state,
            "beforeSemanticHash": self.before_semantic_hash,
            "afterSemanticHash": self.after_semantic_hash,
            "semanticHashEligibleBefore": self.semantic_hash_eligible_before,
            "semanticHashEligibleAfter": self.semantic_hash_eligible_after,
            "presentationBefore": self.presentation_before,
            "presentationAfter": self.presentation_after,
            "changes": [event.to_dict() for event in self.changes],
            "warnings": list(self.warnings),
            "suppressions": [item.to_dict() for item in self.suppressions],
        }

    def sort_key(self) -> tuple[str, ...]:
        return (self.policy_key,)


@dataclass
class ConfigurationPolicyComparison:
    comparison_schema_version: int = COMPARISON_SCHEMA_VERSION
    baseline_snapshot: SnapshotDescriptor | None = None
    target_snapshot: SnapshotDescriptor | None = None
    comparison_status: ComparisonStatus = "success"
    policy_diffs: list[PolicyDiff] = field(default_factory=list)
    assignment_filter_diffs: list[AssignmentFilterDiff] = field(default_factory=list)
    changes: list[ChangeEvent] = field(default_factory=list)
    suppressions: list[ComparisonSuppression] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    comparison_duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparisonSchemaVersion": self.comparison_schema_version,
            "baselineSnapshot": self.baseline_snapshot.to_dict() if self.baseline_snapshot else None,
            "targetSnapshot": self.target_snapshot.to_dict() if self.target_snapshot else None,
            "comparisonStatus": self.comparison_status,
            "policyDiffs": [item.to_dict() for item in self.policy_diffs],
            "assignmentFilterDiffs": [item.to_dict() for item in self.assignment_filter_diffs],
            "changes": [event.to_dict() for event in self.changes],
            "suppressions": [item.to_dict() for item in self.suppressions],
            "summary": self.summary,
            "comparisonDurationSeconds": self.comparison_duration_seconds,
        }
