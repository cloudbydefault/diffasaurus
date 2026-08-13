"""Structural walk helpers for Modern Settings Catalog trees."""

from __future__ import annotations

from typing import Any, Iterator

_CHILD_CONTAINER_KEYS: tuple[str, ...] = (
    "children",
    "groupSettingCollectionValue",
    "choiceSettingCollectionValue",
    "simpleSettingCollectionValue",
)


def looks_like_setting_instance(node: dict[str, Any]) -> bool:
    odata_type = str(node.get("@odata.type", ""))
    return "deviceManagementConfiguration" in odata_type and odata_type.endswith("Instance")


def iter_child_setting_instances(instance: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add_instance(candidate: dict[str, Any]) -> None:
        marker = id(candidate)
        if marker in seen:
            return
        if looks_like_setting_instance(candidate):
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
            elif looks_like_setting_instance(container):
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


def iter_top_level_setting_instances(setting: dict[str, Any]) -> Iterator[dict[str, Any]]:
    top_instance = setting.get("settingInstance")
    if isinstance(top_instance, dict):
        yield top_instance
    elif looks_like_setting_instance(setting):
        yield setting


def walk_modern_setting_instances(
    setting: dict[str, Any],
    *,
    depth: int = 0,
) -> list[tuple[dict[str, Any], int]]:
    found: list[tuple[dict[str, Any], int]] = []

    def visit_instance(instance: dict[str, Any], current_depth: int) -> None:
        found.append((instance, current_depth))
        for child_instance in iter_child_setting_instances(instance):
            visit_instance(child_instance, current_depth + 1)

    for instance in iter_top_level_setting_instances(setting):
        visit_instance(instance, depth)

    return found
