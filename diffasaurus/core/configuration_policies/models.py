"""Dataclasses and constants for Configuration Policy normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NORMALIZATION_SCHEMA_VERSION = 1

ModernSettingKind = Literal[
    "simple",
    "choice",
    "group_collection",
    "simple_collection",
    "choice_collection",
    "unknown",
]

AssignmentTargetKind = Literal[
    "all_devices",
    "all_users",
    "include_group",
    "exclude_group",
    "unknown",
]

ClassicExplicitness = Literal["unknown"]

RetrievalCoverageStatus = Literal[
    "success",
    "partial",
    "error",
    "not_applicable",
    "unknown",
]

NormalizationStatus = Literal["success", "partial", "error"]


@dataclass
class NormalizedSettingValue:
    raw_value: Any = None
    display_value: str | None = None
    children: list[NormalizedSettingNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rawValue": self.raw_value,
            "displayValue": self.display_value,
        }
        if self.children:
            payload["children"] = [child.to_dict() for child in self.children]
        return payload

    def semantic_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.raw_value is not None:
            payload["rawValue"] = self.raw_value
        if self.children:
            payload["children"] = [child.semantic_dict() for child in self.children]
        return payload


@dataclass
class NormalizedSettingNode:
    definition_id: str
    instance_odata_type: str
    kind: ModernSettingKind
    presentation: dict[str, Any] = field(default_factory=dict)
    values: list[NormalizedSettingValue] = field(default_factory=list)
    semantic_hash: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "definitionId": self.definition_id,
            "instanceODataType": self.instance_odata_type,
            "kind": self.kind,
            "presentation": self.presentation,
            "values": [value.to_dict() for value in self.values],
            "semanticHash": self.semantic_hash,
            "warnings": list(self.warnings),
        }

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "definitionId": self.definition_id,
            "kind": self.kind,
            "values": [value.semantic_dict() for value in self.values],
        }


@dataclass
class NormalizedClassicProperty:
    property_path: str
    raw_value: Any
    value_type: str
    confidence: ClassicExplicitness = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "propertyPath": self.property_path,
            "rawValue": self.raw_value,
            "valueType": self.value_type,
            "confidence": self.confidence,
        }

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "propertyPath": self.property_path,
            "rawValue": self.raw_value,
            "valueType": self.value_type,
        }


@dataclass
class NormalizedAdmxSetting:
    definition_id: str
    definition_value_id: str
    enabled: bool | None
    presentation_values: list[dict[str, Any]] = field(default_factory=list)
    presentation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "definitionId": self.definition_id,
            "definitionValueId": self.definition_value_id,
            "enabled": self.enabled,
            "presentationValues": self.presentation_values,
            "presentation": self.presentation,
            "warnings": list(self.warnings),
        }

    def semantic_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "definitionId": self.definition_id,
            "definitionValueId": self.definition_value_id,
        }
        if self.enabled is not None:
            payload["enabled"] = self.enabled
        if self.presentation_values:
            payload["presentationValues"] = self.presentation_values
        return payload


@dataclass
class NormalizedAssignment:
    assignment_key: str
    target_kind: AssignmentTargetKind
    group_id: str | None = None
    filter_id: str | None = None
    filter_type: str | None = None
    presentation: dict[str, Any] = field(default_factory=dict)
    semantic_hash: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignmentKey": self.assignment_key,
            "targetKind": self.target_kind,
            "groupId": self.group_id,
            "filterId": self.filter_id,
            "filterType": self.filter_type,
            "presentation": self.presentation,
            "semanticHash": self.semantic_hash,
            "warnings": list(self.warnings),
        }

    def semantic_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"targetKind": self.target_kind}
        if self.group_id:
            payload["groupId"] = self.group_id
        if self.filter_id:
            payload["filterId"] = self.filter_id
        if self.filter_type:
            payload["filterType"] = self.filter_type
        return payload


@dataclass
class NormalizedAssignmentFilter:
    filter_id: str
    presentation: dict[str, Any] = field(default_factory=dict)
    semantic: dict[str, Any] = field(default_factory=dict)
    semantic_hash: str = ""
    warnings: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filterId": self.filter_id,
            "presentation": self.presentation,
            "semantic": self.semantic,
            "semanticHash": self.semantic_hash,
            "warnings": list(self.warnings),
            "coverage": self.coverage,
        }


@dataclass
class NormalizedPolicyCoverage:
    policy_detail: RetrievalCoverageStatus = "unknown"
    settings: RetrievalCoverageStatus = "unknown"
    assignments: RetrievalCoverageStatus = "unknown"
    definitions: RetrievalCoverageStatus = "unknown"
    presentation_values: RetrievalCoverageStatus = "not_applicable"
    normalization_warnings: list[str] = field(default_factory=list)
    normalization_errors: list[str] = field(default_factory=list)
    semantic_hash_eligible: bool = False
    semantic_hash_blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policyDetail": self.policy_detail,
            "settings": self.settings,
            "assignments": self.assignments,
            "definitions": self.definitions,
            "presentationValues": self.presentation_values,
            "normalizationWarnings": list(self.normalization_warnings),
            "normalizationErrors": list(self.normalization_errors),
            "semanticHashEligible": self.semantic_hash_eligible,
            "semanticHashBlockers": list(self.semantic_hash_blockers),
        }


@dataclass
class NormalizedPolicy:
    policy_key: str
    policy_id: str
    export_source: str
    presentation: dict[str, Any] = field(default_factory=dict)
    semantic_metadata: dict[str, Any] = field(default_factory=dict)
    observational_metadata: dict[str, Any] = field(default_factory=dict)
    coverage: NormalizedPolicyCoverage = field(default_factory=NormalizedPolicyCoverage)
    settings: dict[str, Any] = field(default_factory=dict)
    assignments: list[NormalizedAssignment] = field(default_factory=list)
    semantic_hash: str = ""
    semantic_payload_version: int = 1
    classic_explicitness: ClassicExplicitness | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "policyKey": self.policy_key,
            "policyId": self.policy_id,
            "exportSource": self.export_source,
            "presentation": self.presentation,
            "semanticMetadata": self.semantic_metadata,
            "observationalMetadata": self.observational_metadata,
            "coverage": self.coverage.to_dict(),
            "settings": self.settings,
            "assignments": [assignment.to_dict() for assignment in self.assignments],
            "semanticHash": self.semantic_hash,
            "semanticPayloadVersion": self.semantic_payload_version,
        }
        if self.classic_explicitness is not None:
            payload["classicExplicitness"] = self.classic_explicitness
        return payload


@dataclass
class NormalizedSnapshot:
    normalization_schema_version: int = NORMALIZATION_SCHEMA_VERSION
    source_snapshot_id: str = ""
    source_policy_export_schema_version: int = 0
    captured_at_utc: str = ""
    source_export_status: str = ""
    normalization_status: NormalizationStatus = "success"
    normalization_warnings: list[str] = field(default_factory=list)
    normalization_errors: list[str] = field(default_factory=list)
    assignment_filters: list[NormalizedAssignmentFilter] = field(default_factory=list)
    policies: list[NormalizedPolicy] = field(default_factory=list)
    normalization_duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalizationSchemaVersion": self.normalization_schema_version,
            "sourceSnapshotId": self.source_snapshot_id,
            "sourcePolicyExportSchemaVersion": self.source_policy_export_schema_version,
            "capturedAtUtc": self.captured_at_utc,
            "sourceExportStatus": self.source_export_status,
            "normalizationStatus": self.normalization_status,
            "normalizationWarnings": list(self.normalization_warnings),
            "normalizationErrors": list(self.normalization_errors),
            "assignmentFilters": [item.to_dict() for item in self.assignment_filters],
            "policies": [policy.to_dict() for policy in self.policies],
            "normalizationDurationSeconds": self.normalization_duration_seconds,
        }
