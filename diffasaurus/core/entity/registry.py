from __future__ import annotations

from diffasaurus.core.entity.adapters import ALL_ADAPTERS, ReportFamilyAdapter
from diffasaurus.core.entity.types import EntityType

ADAPTERS_BY_FAMILY: dict[str, ReportFamilyAdapter] = {
    adapter.family: adapter for adapter in ALL_ADAPTERS
}

ADAPTERS_BY_TYPE: dict[EntityType, tuple[ReportFamilyAdapter, ...]] = {
    "user": tuple(adapter for adapter in ALL_ADAPTERS if adapter.entity_type == "user"),
    "device": tuple(adapter for adapter in ALL_ADAPTERS if adapter.entity_type == "device"),
    "shared_mailbox": tuple(
        adapter for adapter in ALL_ADAPTERS if adapter.entity_type == "shared_mailbox"
    ),
}


def adapter_for_family(family: str) -> ReportFamilyAdapter | None:
    return ADAPTERS_BY_FAMILY.get(family)


def adapters_for_type(entity_type: EntityType) -> tuple[ReportFamilyAdapter, ...]:
    return ADAPTERS_BY_TYPE[entity_type]
