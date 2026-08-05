from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

EntityType = Literal["user", "device", "shared_mailbox"]


@dataclass
class EntityIndexStats:
    snapshots_scanned: int = 0
    csv_parsed: int = 0
    csv_cache_hits: int = 0
    binding_seconds: float = 0.0
    total_seconds: float = 0.0
    entity_count: int = 0
    files_indexed: int = 0
    files_reused: int = 0


@dataclass(frozen=True)
class SearchCapabilities:
    exact_id: bool = True
    exact_alias: bool = True
    prefix_autocomplete: bool = True
    substring_search: bool = False
    fts5_enabled: bool = False


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


FamilyCoverageStatus = Literal[
    "snapshot_used",
    "entity_absent",
    "no_snapshot",
    "not_applicable",
]

EntityPresenceStatus = Literal["present", "absent", "unknown", "partial"]


@dataclass(frozen=True)
class FamilyCoverage:
    family: str
    status: FamilyCoverageStatus
    requested_at: datetime
    snapshot_at: datetime | None
    gap: timedelta | None
    entity_present: bool


@dataclass(frozen=True)
class ScopedRelationship:
    family: str
    row_scope: str
    properties: tuple[SourcedProperty, ...]
    observed_at: datetime


@dataclass(frozen=True)
class EntityStateDiff:
    added_properties: tuple[tuple[str, str, str], ...] = ()
    removed_properties: tuple[tuple[str, str, str], ...] = ()
    modified_properties: tuple[tuple[str, str, str, str, str], ...] = ()
    added_relationships: tuple[tuple[str, str], ...] = ()
    removed_relationships: tuple[tuple[str, str], ...] = ()
    modified_relationships: tuple[tuple[str, str, str, str], ...] = ()


@dataclass(frozen=True)
class EntityState:
    as_of: datetime
    key: CanonicalEntityKey
    properties_by_family: dict[str, tuple[SourcedProperty, ...]]
    family_coverage: dict[str, str]
    coverage: tuple[FamilyCoverage, ...] = ()
    presence: EntityPresenceStatus = "unknown"
    scalar_properties_by_family: dict[str, tuple[SourcedProperty, ...]] = field(default_factory=dict)
    relationships_by_family: dict[str, tuple[ScopedRelationship, ...]] = field(default_factory=dict)
