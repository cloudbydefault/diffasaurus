"""Assignment and assignment-filter normalization."""

from __future__ import annotations

from typing import Any

from diffasaurus.core.configuration_policies.canonical import semantic_hash
from diffasaurus.core.configuration_policies.models import (
    AssignmentTargetKind,
    NormalizedAssignment,
    NormalizedAssignmentFilter,
)

_TARGET_KIND_BY_ODATA: dict[str, AssignmentTargetKind] = {
    "#microsoft.graph.allDevicesAssignmentTarget": "all_devices",
    "#microsoft.graph.allLicensedUsersAssignmentTarget": "all_users",
    "#microsoft.graph.groupAssignmentTarget": "include_group",
    "#microsoft.graph.exclusionGroupAssignmentTarget": "exclude_group",
}


def build_assignment_key(
    *,
    target_kind: AssignmentTargetKind,
    group_id: str | None,
    filter_id: str | None,
    filter_type: str | None,
) -> str:
    return "|".join(
        [
            target_kind,
            group_id or "",
            filter_id or "",
            filter_type or "",
        ]
    )


def normalize_assignment(assignment: dict[str, Any]) -> NormalizedAssignment:
    target = assignment.get("target")
    warnings: list[str] = []
    presentation: dict[str, Any] = {}

    if not isinstance(target, dict):
        normalized = NormalizedAssignment(
            assignment_key=build_assignment_key(
                target_kind="unknown",
                group_id=None,
                filter_id=None,
                filter_type=None,
            ),
            target_kind="unknown",
            warnings=["unknown_assignment_target"],
        )
        normalized.semantic_hash = semantic_hash(normalized.semantic_dict())
        return normalized

    odata_type = str(target.get("@odata.type", ""))
    target_kind = _TARGET_KIND_BY_ODATA.get(odata_type, "unknown")
    if target_kind == "unknown":
        warnings.append("unknown_assignment_target")

    group_id = target.get("groupId")
    group_id_text = str(group_id) if group_id else None

    filter_id = target.get("deviceAndAppManagementAssignmentFilterId")
    filter_id_text = str(filter_id) if filter_id else None
    filter_type = target.get("deviceAndAppManagementAssignmentFilterType")
    filter_type_text = str(filter_type) if filter_type else None

    for key in ("displayName", "deviceAndAppManagementAssignmentFilterDisplayName"):
        if key in target:
            presentation[key] = target[key]

    assignment_key = build_assignment_key(
        target_kind=target_kind,
        group_id=group_id_text,
        filter_id=filter_id_text,
        filter_type=filter_type_text,
    )
    normalized = NormalizedAssignment(
        assignment_key=assignment_key,
        target_kind=target_kind,
        group_id=group_id_text,
        filter_id=filter_id_text,
        filter_type=filter_type_text,
        presentation=presentation,
        warnings=warnings,
    )
    normalized.semantic_hash = semantic_hash(normalized.semantic_dict())
    return normalized


def normalize_assignments(assignments: list[dict[str, Any]] | None) -> list[NormalizedAssignment]:
    normalized = [normalize_assignment(item) for item in assignments or [] if isinstance(item, dict)]
    normalized.sort(key=lambda item: item.assignment_key)
    return normalized


def normalize_assignment_filters(
    filters_document: dict[str, Any] | None,
) -> tuple[list[NormalizedAssignmentFilter], list[str]]:
    if not isinstance(filters_document, dict):
        return [], ["assignment_filters_document_missing"]

    warnings: list[str] = []
    retrieval = filters_document.get("retrieval")
    retrieval_status = ""
    if isinstance(retrieval, dict):
        retrieval_status = str(retrieval.get("status", ""))
    if retrieval_status == "error":
        warnings.append("assignment_filters_retrieval_error")

    normalized_filters: list[NormalizedAssignmentFilter] = []
    for item in filters_document.get("assignmentFilters") or []:
        if not isinstance(item, dict):
            continue
        filter_id = str(item.get("id", ""))
        if not filter_id:
            warnings.append("assignment_filter_missing_id")
            continue

        presentation = {
            key: item[key]
            for key in ("displayName", "description")
            if key in item and item[key] is not None
        }
        semantic = {
            "filterId": filter_id,
            "platform": item.get("platform"),
            "rule": item.get("rule"),
            "assignmentFilterManagementType": item.get("assignmentFilterManagementType"),
        }
        semantic = {key: value for key, value in semantic.items() if value is not None}

        normalized = NormalizedAssignmentFilter(
            filter_id=filter_id,
            presentation=presentation,
            semantic=semantic,
            coverage={"retrievalStatus": retrieval_status or "unknown"},
        )
        normalized.semantic_hash = semantic_hash(semantic)
        normalized_filters.append(normalized)

    normalized_filters.sort(key=lambda item: item.filter_id)
    return normalized_filters, warnings
