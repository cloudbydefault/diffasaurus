from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

EntityType = Literal["user", "device", "shared_mailbox"]


@dataclass(frozen=True)
class CanonicalEntityKey:
    entity_type: EntityType
    primary_id: str

    def label(self) -> str:
        return f"{self.entity_type}:{self.primary_id}"


@dataclass(frozen=True)
class TimedAlias:
    kind: str
    value: str
    first_seen: datetime
    last_seen: datetime
    source_family: str


@dataclass(frozen=True)
class SourcedProperty:
    family: str
    name: str
    value: str
    observed_at: datetime


@dataclass
class EntityRecord:
    key: CanonicalEntityKey
    display_name: str
    aliases: list[TimedAlias] = field(default_factory=list)
    source_families: set[str] = field(default_factory=set)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    present_in_latest: bool = False
    properties_by_family: dict[str, list[SourcedProperty]] = field(default_factory=dict)

    def alias_values(self) -> set[str]:
        return {alias.value.lower() for alias in self.aliases}


@dataclass(frozen=True)
class EntityChangeEvent:
    change_type: Literal["added", "removed", "modified"]
    family: str
    property: str
    before: str
    after: str
    baseline_at: datetime
    latest_at: datetime
    row_scope: str = ""


@dataclass(frozen=True)
class EntityPeriodChanges:
    events: tuple[EntityChangeEvent, ...]
    family_notes: tuple[tuple[str, str], ...]
    covered_from: datetime
    covered_to: datetime


@dataclass(frozen=True)
class EntityState:
    as_of: datetime
    key: CanonicalEntityKey
    properties_by_family: dict[str, tuple[SourcedProperty, ...]]
    family_coverage: dict[str, str]
