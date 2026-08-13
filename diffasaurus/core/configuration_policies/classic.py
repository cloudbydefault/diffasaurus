"""Classic device configuration normalization."""

from __future__ import annotations

from typing import Any

from diffasaurus.core.configuration_policies.models import NormalizedClassicProperty

CLASSIC_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "@odata.type",
        "@odata.context",
        "id",
        "displayName",
        "description",
        "createdDateTime",
        "lastModifiedDateTime",
        "version",
        "roleScopeTagIds",
        "supportsScopeTags",
    }
)

CLASSIC_APPLICABILITY_PREFIX = "deviceManagementApplicabilityRule"


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


def _is_absent(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict) and not value:
        return True
    if isinstance(value, list) and not value:
        return True
    return False


def extract_classic_semantic_metadata(policy: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": policy.get("displayName") or policy.get("name") or "",
        "description": policy.get("description") or "",
    }
    role_scope_tag_ids = policy.get("roleScopeTagIds")
    if isinstance(role_scope_tag_ids, list):
        metadata["roleScopeTagIds"] = sorted(str(item) for item in role_scope_tag_ids)

    applicability: dict[str, Any] = {}
    for key, value in policy.items():
        if key.startswith(CLASSIC_APPLICABILITY_PREFIX) and not _is_absent(value):
            applicability[key] = value
    if applicability:
        metadata["applicability"] = applicability
    return metadata


def normalize_classic_properties(policy: dict[str, Any]) -> list[NormalizedClassicProperty]:
    properties: list[NormalizedClassicProperty] = []

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key in sorted(node):
                if prefix == "" and key in CLASSIC_METADATA_KEYS:
                    continue
                if prefix == "" and key.startswith(CLASSIC_APPLICABILITY_PREFIX):
                    continue
                child = node[key]
                path = f"{prefix}.{key}" if prefix else key
                if _is_absent(child):
                    continue
                if isinstance(child, dict):
                    walk(child, path)
                else:
                    properties.append(
                        NormalizedClassicProperty(
                            property_path=path,
                            raw_value=child,
                            value_type=_property_type_name(child),
                        )
                    )
        elif not _is_absent(node):
            properties.append(
                NormalizedClassicProperty(
                    property_path=prefix or "value",
                    raw_value=node,
                    value_type=_property_type_name(node),
                )
            )

    walk(policy, "")
    properties.sort(key=lambda item: item.property_path)
    return properties
