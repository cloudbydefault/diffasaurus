"""Modern Settings Catalog normalization."""

from __future__ import annotations

from typing import Any

from diffasaurus.core.configuration_policies.canonical import semantic_hash
from diffasaurus.core.configuration_policies.models import (
    ModernSettingKind,
    NormalizedSettingNode,
    NormalizedSettingValue,
)
from diffasaurus.core.configuration_policies.modern_walk import (
    iter_child_setting_instances,
    iter_top_level_setting_instances,
    looks_like_setting_instance,
)

_SIMPLE_INSTANCE = "#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance"
_CHOICE_INSTANCE = "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance"
_GROUP_COLLECTION_INSTANCE = (
    "#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance"
)
_SIMPLE_COLLECTION_INSTANCE = (
    "#microsoft.graph.deviceManagementConfigurationSimpleSettingCollectionInstance"
)
_CHOICE_COLLECTION_INSTANCE = (
    "#microsoft.graph.deviceManagementConfigurationChoiceSettingCollectionInstance"
)

_KIND_BY_ODATA: dict[str, ModernSettingKind] = {
    _SIMPLE_INSTANCE: "simple",
    _CHOICE_INSTANCE: "choice",
    _GROUP_COLLECTION_INSTANCE: "group_collection",
    _SIMPLE_COLLECTION_INSTANCE: "simple_collection",
    _CHOICE_COLLECTION_INSTANCE: "choice_collection",
}


def _definition_id(instance: dict[str, Any]) -> str:
    value = instance.get("settingDefinitionId")
    return str(value) if value else ""


def _stable_definition_key(instance: dict[str, Any], *, fallback_index: int) -> str:
    definition_id = _definition_id(instance)
    if definition_id:
        return definition_id
    odata_type = str(instance.get("@odata.type", "unknown"))
    return f"__missing_definition_id__:{odata_type}:{fallback_index}"


def _lookup_definition(
    definition_id: str,
    definitions: dict[str, Any],
) -> dict[str, Any] | None:
    if not definition_id:
        return None
    definition = definitions.get(definition_id)
    return definition if isinstance(definition, dict) else None


def _definition_presentation(definition: dict[str, Any] | None) -> dict[str, Any]:
    if not definition:
        return {}
    presentation: dict[str, Any] = {}
    for key in (
        "@odata.type",
        "displayName",
        "description",
        "helpText",
        "category",
        "riskLevel",
        "uxBehavior",
        "settingUsage",
    ):
        if key in definition and definition[key] is not None:
            presentation[key] = definition[key]
    applicability = definition.get("applicability")
    if isinstance(applicability, dict) and applicability:
        presentation["applicability"] = applicability
    return presentation


def _resolve_choice_display(
    raw_value: Any,
    definition: dict[str, Any] | None,
) -> str | None:
    if raw_value is None or not definition:
        return None
    options = definition.get("options")
    if not isinstance(options, list):
        return None
    raw_text = str(raw_value)
    for option in options:
        if not isinstance(option, dict):
            continue
        for key in ("itemId", "optionValue", "name", "id"):
            candidate = option.get(key)
            if candidate is not None and str(candidate) == raw_text:
                display = option.get("displayName")
                return str(display) if display is not None else None
    return None


def _normalize_child_nodes(
    instances: list[dict[str, Any]],
    definitions: dict[str, Any],
) -> list[NormalizedSettingNode]:
    nodes = [
        normalize_modern_setting_instance(instance, definitions)
        for instance in instances
    ]
    return sorted(nodes, key=lambda node: (node.definition_id, node.kind, node.instance_odata_type))


def _children_from_container(container: Any) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    if isinstance(container, list):
        for item in container:
            children.extend(_children_from_container(item))
    elif isinstance(container, dict):
        if looks_like_setting_instance(container):
            children.append(container)
            return children
        nested = container.get("settingInstance")
        if isinstance(nested, dict):
            children.append(nested)
            return children
        if "children" in container:
            children.extend(_children_from_container(container["children"]))
    return children


def normalize_modern_setting_instance(
    instance: dict[str, Any],
    definitions: dict[str, Any],
    *,
    fallback_index: int = 0,
) -> NormalizedSettingNode:
    odata_type = str(instance.get("@odata.type", ""))
    kind = _KIND_BY_ODATA.get(odata_type, "unknown")
    definition_id = _definition_id(instance)
    stable_key = _stable_definition_key(instance, fallback_index=fallback_index)
    definition = _lookup_definition(definition_id, definitions)
    presentation = _definition_presentation(definition)
    warnings: list[str] = []

    if not definition_id:
        warnings.append("missing_setting_definition_id")
    if kind == "unknown":
        warnings.append("unknown_modern_setting_instance_type")

    values: list[NormalizedSettingValue] = []

    if kind == "simple":
        simple_value = instance.get("simpleSettingValue")
        raw_value = simple_value.get("value") if isinstance(simple_value, dict) else None
        child_instances = iter_child_setting_instances(instance)
        values.append(
            NormalizedSettingValue(
                raw_value=raw_value,
                display_value=None,
                children=_normalize_child_nodes(child_instances, definitions),
            )
        )
    elif kind == "choice":
        choice_value = instance.get("choiceSettingValue")
        raw_value = choice_value.get("value") if isinstance(choice_value, dict) else None
        display_value = _resolve_choice_display(raw_value, definition)
        if raw_value is not None and display_value is None and definition:
            warnings.append("unresolved_choice_option_label")
        child_instances: list[dict[str, Any]] = []
        if isinstance(choice_value, dict):
            child_instances = _children_from_container(choice_value.get("children"))
        values.append(
            NormalizedSettingValue(
                raw_value=raw_value,
                display_value=display_value,
                children=_normalize_child_nodes(child_instances, definitions),
            )
        )
    elif kind == "group_collection":
        entries = instance.get("groupSettingCollectionValue")
        if isinstance(entries, list):
            for entry in entries:
                child_instances = _children_from_container(entry)
                values.append(
                    NormalizedSettingValue(
                        raw_value=None,
                        display_value=None,
                        children=_normalize_child_nodes(child_instances, definitions),
                    )
                )
    elif kind in {"simple_collection", "choice_collection"}:
        collection_key = (
            "simpleSettingCollectionValue"
            if kind == "simple_collection"
            else "choiceSettingCollectionValue"
        )
        entries = instance.get(collection_key)
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw_value = entry.get("value")
                display_value = (
                    _resolve_choice_display(raw_value, definition)
                    if kind == "choice_collection"
                    else None
                )
                child_instances = _children_from_container(entry.get("children"))
                values.append(
                    NormalizedSettingValue(
                        raw_value=raw_value,
                        display_value=display_value,
                        children=_normalize_child_nodes(child_instances, definitions),
                    )
                )
    else:
        values.append(
            NormalizedSettingValue(
                raw_value=_opaque_semantic_payload(instance),
                display_value=None,
                children=[],
            )
        )

    node = NormalizedSettingNode(
        definition_id=stable_key,
        instance_odata_type=odata_type,
        kind=kind,
        presentation=presentation,
        values=values,
        warnings=warnings,
    )
    node.semantic_hash = semantic_hash(node.semantic_dict())
    return node


def _opaque_semantic_payload(instance: dict[str, Any]) -> dict[str, Any]:
    return {key: instance[key] for key in sorted(instance)}


def normalize_modern_settings(
    settings: list[dict[str, Any]] | None,
    definitions: dict[str, Any] | None,
) -> tuple[list[NormalizedSettingNode], list[str]]:
    definition_map = definitions if isinstance(definitions, dict) else {}
    nodes: list[NormalizedSettingNode] = []
    warnings: list[str] = []

    for setting_index, setting in enumerate(settings or []):
        if not isinstance(setting, dict):
            continue
        for instance_index, instance in enumerate(iter_top_level_setting_instances(setting)):
            nodes.append(
                normalize_modern_setting_instance(
                    instance,
                    definition_map,
                    fallback_index=setting_index * 100 + instance_index,
                )
            )

    nodes.sort(key=lambda node: (node.definition_id, node.kind, node.instance_odata_type))
    return nodes, warnings
