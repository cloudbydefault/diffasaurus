from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from diffasaurus.core.entity.pit_field_registry import (
    AUTHORITY_ORDER,
    RELATIONSHIP_COLLECTIONS,
    SECTION_ORDER,
    SECTION_TITLES,
    is_scalar_excluded,
    lookup_property_binding,
    parse_row_scope,
    resolve_collection_coverage,
)
from diffasaurus.core.entity.types import (
    EntityPresenceStatus,
    EntityState,
    EntityType,
    FamilyCoverage,
    ScopedRelationship,
    SourcedProperty,
)


@dataclass(frozen=True)
class ProvenanceObservation:
    family: str
    observed_at: datetime | None
    snapshot_at: datetime | None
    requested_at: datetime
    gap: timedelta | None


@dataclass(frozen=True)
class SourceProvenance:
    observations: tuple[ProvenanceObservation, ...]


def provenance_observation_identity(obs: ProvenanceObservation) -> tuple:
    return (obs.family, obs.observed_at, obs.snapshot_at, obs.requested_at, obs.gap)


def provenance_observation_sort_key(obs: ProvenanceObservation) -> tuple[str, str, str, str, str]:
    return (
        obs.family.casefold(),
        obs.observed_at.isoformat(timespec="seconds") if obs.observed_at else "",
        obs.snapshot_at.isoformat(timespec="seconds") if obs.snapshot_at else "",
        obs.requested_at.isoformat(timespec="seconds"),
        str(int(obs.gap.total_seconds())) if obs.gap is not None else "",
    )


def merge_provenance(*parts: SourceProvenance) -> SourceProvenance:
    seen: set[tuple] = set()
    merged: list[ProvenanceObservation] = []
    for part in parts:
        for obs in part.observations:
            identity = provenance_observation_identity(obs)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(obs)
    merged.sort(key=provenance_observation_sort_key)
    return SourceProvenance(observations=tuple(merged))


def single_provenance(observation: ProvenanceObservation) -> SourceProvenance:
    return SourceProvenance(observations=(observation,))


@dataclass(frozen=True)
class FieldAlternate:
    value: str
    observation: ProvenanceObservation


@dataclass(frozen=True)
class FieldConflict:
    alternates: tuple[FieldAlternate, ...]


@dataclass(frozen=True)
class CardField:
    normalized_key: str
    label: str
    display_value: str
    provenance: SourceProvenance
    conflict: FieldConflict | None


CollectionCoverageStatus = Literal[
    "populated", "known_empty", "no_coverage", "unknown", "not_applicable"
]


@dataclass(frozen=True)
class CardCollectionItem:
    primary_label: str
    secondary_label: str
    detail: str
    provenance: SourceProvenance
    sort_key: str


@dataclass(frozen=True)
class CardCollection:
    collection_id: str
    title: str
    coverage: CollectionCoverageStatus
    items: tuple[CardCollectionItem, ...]
    source_family: str


@dataclass(frozen=True)
class CardSection:
    section_id: str
    title: str
    fields: tuple[CardField, ...]
    collections: tuple[CardCollection, ...]


@dataclass(frozen=True)
class PointInTimeSourceDetails:
    coverage: tuple[FamilyCoverage, ...]
    scalar_properties_by_family: dict[str, tuple[SourcedProperty, ...]]
    relationships_by_family: dict[str, tuple[ScopedRelationship, ...]]
    family_coverage_labels: dict[str, str]


@dataclass(frozen=True)
class PointInTimeCardModel:
    entity_type: EntityType
    display_name: str
    canonical_id: str
    requested_at: datetime
    presence: EntityPresenceStatus
    history_range: tuple[datetime | None, datetime | None]
    coverage_summary: str
    sections: tuple[CardSection, ...]
    source_details: PointInTimeSourceDetails


def _coverage_by_family(state: EntityState) -> dict[str, FamilyCoverage]:
    return {item.family: item for item in state.coverage}


def _family_coverage_labels(state: EntityState) -> dict[str, str]:
    return dict(state.family_coverage)


def _build_coverage_summary(state: EntityState) -> str:
    contributing = sum(1 for item in state.coverage if item.status == "snapshot_used")
    without = sum(
        1 for item in state.coverage if item.status in ("no_snapshot", "entity_absent")
    )
    return f"{contributing} contributing · {without} without usable coverage"


def _observation_for_property(
    prop: SourcedProperty,
    coverage: FamilyCoverage | None,
    requested_at: datetime,
) -> ProvenanceObservation:
    return ProvenanceObservation(
        family=prop.family,
        observed_at=prop.observed_at,
        snapshot_at=coverage.snapshot_at if coverage else None,
        requested_at=requested_at,
        gap=coverage.gap if coverage else None,
    )


def _observation_for_relationship(
    relationship: ScopedRelationship,
    coverage: FamilyCoverage | None,
    requested_at: datetime,
) -> ProvenanceObservation:
    return ProvenanceObservation(
        family=relationship.family,
        observed_at=relationship.observed_at,
        snapshot_at=coverage.snapshot_at if coverage else None,
        requested_at=requested_at,
        gap=coverage.gap if coverage else None,
    )


def _property_value(relationship: ScopedRelationship, name: str) -> str:
    for prop in relationship.properties:
        if prop.name == name:
            return prop.value.strip()
    return ""


def _authority_rank(entity_type: EntityType, normalized_key: str, family: str) -> int:
    order = AUTHORITY_ORDER.get((entity_type, normalized_key), ())
    if family in order:
        return order.index(family)
    return len(order)


def _pick_authoritative_value(
    entity_type: EntityType,
    normalized_key: str,
    candidates: list[tuple[str, str, ProvenanceObservation]],
) -> tuple[str, list[tuple[str, ProvenanceObservation]]]:
    """Return (authoritative_value, alternates as (value, observation))."""
    by_value: dict[str, list[ProvenanceObservation]] = {}
    for value, _family, obs in candidates:
        by_value.setdefault(value, []).append(obs)

    unique_values = list(by_value.keys())
    if len(unique_values) == 1:
        value = unique_values[0]
        provenance = merge_provenance(
            *[SourceProvenance(observations=(obs,)) for obs in by_value[value]]
        )
        return value, []

    def best_for_value(value: str) -> tuple[int, ProvenanceObservation]:
        observations = by_value[value]
        best_obs = min(
            observations,
            key=lambda obs: _authority_rank(entity_type, normalized_key, obs.family),
        )
        rank = _authority_rank(entity_type, normalized_key, best_obs.family)
        return rank, best_obs

    ranked = sorted(unique_values, key=lambda value: best_for_value(value))
    authoritative = ranked[0]
    alternates: list[tuple[str, ProvenanceObservation]] = []
    for value in ranked[1:]:
        _, obs = best_for_value(value)
        alternates.append((value, obs))
    return authoritative, alternates


def _build_scalar_fields(
    state: EntityState,
    coverage_map: dict[str, FamilyCoverage],
) -> dict[str, list[CardField]]:
    entity_type = state.key.entity_type
    buckets: dict[str, dict[tuple[str, str], list[tuple[str, ProvenanceObservation]]]] = {}
    labels: dict[str, str] = {}
    sections: dict[str, str] = {}

    for family, properties in state.scalar_properties_by_family.items():
        coverage = coverage_map.get(family)
        for prop in properties:
            value = prop.value.strip()
            if not value:
                continue
            spec = lookup_property_binding(entity_type, family, prop.name)
            if spec is None:
                continue
            obs = _observation_for_property(prop, coverage, state.as_of)
            key = spec.key
            labels[key] = spec.label
            sections[key] = spec.section_id
            value_bucket = buckets.setdefault(key, {})
            value_bucket.setdefault((key, value), []).append((family, obs))

    fields_by_section: dict[str, list[CardField]] = {}
    for normalized_key, value_groups in buckets.items():
        candidates: list[tuple[str, str, ProvenanceObservation]] = []
        for (_key, value), entries in value_groups.items():
            for family, obs in entries:
                candidates.append((value, family, obs))

        authoritative, conflict_alternates = _pick_authoritative_value(
            entity_type, normalized_key, candidates
        )

        all_observations: list[ProvenanceObservation] = []
        for (_key, value), entries in value_groups.items():
            if value == authoritative:
                for _family, obs in entries:
                    all_observations.append(obs)

        provenance = merge_provenance(
            *[SourceProvenance(observations=(obs,)) for obs in all_observations]
        )

        conflict: FieldConflict | None = None
        if conflict_alternates:
            conflict = FieldConflict(
                alternates=tuple(
                    FieldAlternate(value=value, observation=obs)
                    for value, obs in conflict_alternates
                )
            )

        field = CardField(
            normalized_key=normalized_key,
            label=labels[normalized_key],
            display_value=authoritative,
            provenance=provenance,
            conflict=conflict,
        )
        section_id = sections[normalized_key]
        fields_by_section.setdefault(section_id, []).append(field)

    return fields_by_section


def _humanize_property_label(name: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return spaced.replace("_", " ").strip()


def _build_fallback_fields(
    state: EntityState,
    coverage_map: dict[str, FamilyCoverage],
) -> list[CardField]:
    entity_type = state.key.entity_type
    fields: list[CardField] = []
    for family, properties in state.scalar_properties_by_family.items():
        coverage = coverage_map.get(family)
        for prop in properties:
            value = prop.value.strip()
            if not value:
                continue
            if lookup_property_binding(entity_type, family, prop.name) is not None:
                continue
            if is_scalar_excluded(entity_type, family, prop.name):
                continue
            obs = _observation_for_property(prop, coverage, state.as_of)
            fields.append(
                CardField(
                    normalized_key=f"additional:{family}:{prop.name}",
                    label=_humanize_property_label(prop.name),
                    display_value=value,
                    provenance=single_provenance(obs),
                    conflict=None,
                )
            )
    fields.sort(key=lambda field: (field.label.casefold(), field.normalized_key))
    return fields


def _dedup_relationships(
    spec,
    relationships: tuple[ScopedRelationship, ...],
) -> list[ScopedRelationship]:
    seen: set[str] = set()
    result: list[ScopedRelationship] = []
    for relationship in relationships:
        scope = parse_row_scope(relationship.row_scope)
        if spec.dedup_key == "GroupId":
            detail = _property_value(relationship, "GroupId") or scope.get("GroupId", "")
            if not detail:
                detail = _property_value(relationship, spec.primary_property).casefold()
            dedup = detail.casefold()
        elif spec.dedup_key:
            detail = _property_value(relationship, spec.dedup_key) or scope.get(spec.dedup_key, "")
            if detail:
                dedup = detail.casefold()
            else:
                dedup = _property_value(relationship, spec.primary_property).casefold()
        else:
            dedup = _property_value(relationship, spec.primary_property).casefold()
        if not dedup or dedup in seen:
            continue
        seen.add(dedup)
        result.append(relationship)
    return result


def _build_collection(
    spec,
    state: EntityState,
    coverage_map: dict[str, FamilyCoverage],
) -> CardCollection | None:
    entity_type = state.key.entity_type
    relationships = state.relationships_by_family.get(spec.source_family, ())
    coverage_status: CollectionCoverageStatus = resolve_collection_coverage(
        spec,
        entity_type,
        coverage_map,
        relationships,
    )
    if coverage_status == "not_applicable":
        return None
    if coverage_status in ("no_coverage", "unknown"):
        return CardCollection(
            collection_id=spec.collection_id,
            title=spec.title,
            coverage=coverage_status,
            items=(),
            source_family=spec.source_family,
        )

    coverage = coverage_map.get(spec.source_family)
    items: list[CardCollectionItem] = []
    for relationship in _dedup_relationships(spec, relationships):
        scope = parse_row_scope(relationship.row_scope)
        primary = _property_value(relationship, spec.primary_property) or scope.get(
            spec.primary_property, ""
        )
        secondary = _property_value(relationship, spec.secondary_property)
        detail = ""
        if spec.detail_property:
            detail = _property_value(relationship, spec.detail_property) or scope.get(
                spec.detail_scope_key, ""
            )
        obs = _observation_for_relationship(relationship, coverage, state.as_of)
        items.append(
            CardCollectionItem(
                primary_label=primary,
                secondary_label=secondary,
                detail=detail,
                provenance=single_provenance(obs),
                sort_key=primary.casefold(),
            )
        )

    items.sort(key=lambda item: item.sort_key)
    if coverage_status == "known_empty":
        items = []

    return CardCollection(
        collection_id=spec.collection_id,
        title=spec.title,
        coverage=coverage_status if coverage_status == "known_empty" else "populated",
        items=tuple(items),
        source_family=spec.source_family,
    )


def build_point_in_time_card(
    state: EntityState,
    *,
    display_name: str = "",
    history_range: tuple[datetime | None, datetime | None] = (None, None),
) -> PointInTimeCardModel:
    coverage_map = _coverage_by_family(state)
    fields_by_section = _build_scalar_fields(state, coverage_map)
    fallback_fields = _build_fallback_fields(state, coverage_map)
    if fallback_fields:
        fields_by_section["additional_details"] = fallback_fields

    collections_by_section: dict[str, list[CardCollection]] = {}
    for spec in RELATIONSHIP_COLLECTIONS:
        collection = _build_collection(spec, state, coverage_map)
        if collection is None:
            continue
        if collection.coverage in ("no_coverage", "unknown"):
            continue
        collections_by_section.setdefault(spec.section_id, []).append(collection)

    sections: list[CardSection] = []
    for section_id in SECTION_ORDER.get(state.key.entity_type, ()):
        fields = tuple(fields_by_section.get(section_id, ()))
        collections = tuple(collections_by_section.get(section_id, ()))
        if not fields and not collections:
            continue
        sections.append(
            CardSection(
                section_id=section_id,
                title=SECTION_TITLES.get(section_id, section_id.replace("_", " ").title()),
                fields=fields,
                collections=collections,
            )
        )

    source_details = PointInTimeSourceDetails(
        coverage=state.coverage,
        scalar_properties_by_family=dict(state.scalar_properties_by_family),
        relationships_by_family=dict(state.relationships_by_family),
        family_coverage_labels=_family_coverage_labels(state),
    )

    return PointInTimeCardModel(
        entity_type=state.key.entity_type,
        display_name=display_name,
        canonical_id=state.key.primary_id,
        requested_at=state.as_of,
        presence=state.presence,
        history_range=history_range,
        coverage_summary=_build_coverage_summary(state),
        sections=tuple(sections),
        source_details=source_details,
    )
