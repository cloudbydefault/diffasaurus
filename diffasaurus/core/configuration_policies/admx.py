"""ADMX / Administrative Templates normalization."""

from __future__ import annotations

from typing import Any

from diffasaurus.core.configuration_policies.models import NormalizedAdmxSetting

_PRESENTATION_VALUE_KEYS: tuple[str, ...] = (
    "value",
    "values",
    "decimalValue",
    "stringValue",
    "booleanValue",
    "integerValue",
)


def _extract_presentation_semantic(presentation_value: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "@odata.type": presentation_value.get("@odata.type"),
    }
    for key in _PRESENTATION_VALUE_KEYS:
        if key in presentation_value:
            payload[key] = presentation_value[key]
    if len(payload) == 1 and "@odata.type" in payload:
        payload["opaque"] = {
            key: value
            for key, value in presentation_value.items()
            if key not in {"@odata.type", "id", "displayName", "description", "name"}
        }
    return payload


def _definition_identity(definition_value: dict[str, Any]) -> tuple[str, str]:
    definition = definition_value.get("definition")
    if isinstance(definition, dict) and definition.get("id"):
        return str(definition["id"]), str(definition_value.get("id", ""))
    return str(definition_value.get("id", "")), str(definition_value.get("id", ""))


def normalize_admx_settings(
    definition_values: list[dict[str, Any]] | None,
) -> tuple[list[NormalizedAdmxSetting], list[str], list[str]]:
    normalized: list[NormalizedAdmxSetting] = []
    warnings: list[str] = []
    blockers: list[str] = []

    for definition_value in definition_values or []:
        if not isinstance(definition_value, dict):
            continue

        definition_id, definition_value_id = _definition_identity(definition_value)
        enabled = definition_value.get("enabled")
        enabled_bool = enabled if isinstance(enabled, bool) else None

        presentation_values_raw = definition_value.get("presentationValues")
        presentation_values: list[dict[str, Any]] = []
        if isinstance(presentation_values_raw, list):
            for item in presentation_values_raw:
                if isinstance(item, dict):
                    presentation_values.append(_extract_presentation_semantic(item))

        presentation: dict[str, Any] = {}
        definition = definition_value.get("definition")
        if isinstance(definition, dict):
            for key in ("displayName", "description", "@odata.type"):
                if key in definition:
                    presentation[key] = definition[key]

        retrieval = definition_value.get("presentationRetrieval")
        retrieval_status = ""
        if isinstance(retrieval, dict):
            retrieval_status = str(retrieval.get("status", ""))

        item_warnings: list[str] = []
        if retrieval_status in {"error", "partial"} and not presentation_values:
            blockers.append("admx_presentation_values_unavailable")
            item_warnings.append("admx_presentation_retrieval_incomplete")

        normalized.append(
            NormalizedAdmxSetting(
                definition_id=definition_id,
                definition_value_id=definition_value_id,
                enabled=enabled_bool,
                presentation_values=presentation_values,
                presentation=presentation,
                warnings=item_warnings,
            )
        )

    normalized.sort(key=lambda item: (item.definition_id, item.definition_value_id))
    return normalized, warnings, blockers
