from __future__ import annotations

from datetime import datetime, timedelta

from diffasaurus.core.entity.adapters import ReportFamilyAdapter
from diffasaurus.core.entity.bindings import AliasBindingIndex
from diffasaurus.core.entity.registry import ADAPTERS_BY_FAMILY, adapters_for_type
from diffasaurus.core.entity.snapshots import load_snapshot_rows, snapshot_at_or_before
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    EntityChangeEvent,
    EntityPeriodChanges,
    EntityPresenceStatus,
    EntityState,
    EntityStateDiff,
    FamilyCoverage,
    FamilyCoverageStatus,
    ScopedRelationship,
    SourcedProperty,
)
from diffasaurus.core.report_history import (
    ReportSnapshot,
    period_window,
    resolve_period_pair,
)

_BINDING_FAMILIES = {"Entra_Users_Properties", "Entra_Users_Activity"}


def _row_value(row: dict[str, str], column: str) -> str:
    normalized = {header.lower(): header for header in row}
    actual = normalized.get(column.lower())
    if not actual:
        return ""
    return str(row.get(actual, "") or "").strip()


def _extract_upn(adapter: ReportFamilyAdapter, row: dict[str, str]) -> str:
    for alias in adapter.alias_columns:
        if alias.kind == "upn":
            value = _row_value(row, alias.column)
            if value:
                return value
    return ""


def build_alias_binding_index(
    families: dict[str, list[ReportSnapshot]],
    max_observed_at: datetime | None = None,
) -> AliasBindingIndex:
    index = AliasBindingIndex()
    for family in _BINDING_FAMILIES:
        adapter = ADAPTERS_BY_FAMILY.get(family)
        snapshots = families.get(family, [])
        if not adapter:
            continue
        for snapshot in snapshots:
            if max_observed_at is not None and snapshot.captured_at > max_observed_at:
                continue
            _, rows = load_snapshot_rows(snapshot)
            for row in rows:
                user_id = adapter.canonical_value(row)
                upn = _extract_upn(adapter, row)
                if user_id and upn:
                    index.record(
                        "upn",
                        upn.lower(),
                        snapshot.captured_at,
                        user_id,
                        adapter.family,
                    )
    return index


def row_entity_key(
    adapter: ReportFamilyAdapter,
    row: dict[str, str],
    observed_at: datetime,
    alias_index: AliasBindingIndex,
) -> CanonicalEntityKey | None:
    key, _reason = classify_row_entity_key(adapter, row, observed_at, alias_index)
    return key


def classify_row_entity_key(
    adapter: ReportFamilyAdapter,
    row: dict[str, str],
    observed_at: datetime,
    alias_index: AliasBindingIndex,
) -> tuple[CanonicalEntityKey | None, str | None]:
    key = adapter.build_key(row)
    if key is None:
        return None, None

    if key.entity_type == "user" and key.primary_id.startswith("upn_only:"):
        upn = _extract_upn(adapter, row)
        if not upn:
            return None, "missing_upn"
        resolved = alias_index.resolve("upn", upn.lower(), observed_at)
        if resolved.status == "bound":
            return CanonicalEntityKey("user", resolved.immutable_id), None
        if resolved.status == "ambiguous":
            return None, "ambiguous_upn"
        return None, "unbound_upn"

    return key, None


def row_matches_entity(
    adapter: ReportFamilyAdapter,
    row: dict[str, str],
    entity_key: CanonicalEntityKey,
    observed_at: datetime,
    alias_index: AliasBindingIndex,
) -> bool:
    key = row_entity_key(adapter, row, observed_at, alias_index)
    return key is not None and key.primary_id == entity_key.primary_id and key.entity_type == entity_key.entity_type


def entity_rows_at(
    entity_key: CanonicalEntityKey,
    adapter: ReportFamilyAdapter,
    snapshots: list[ReportSnapshot],
    target: datetime,
    alias_index: AliasBindingIndex,
) -> tuple[ReportSnapshot | None, list[dict[str, str]], FamilyCoverageStatus]:
    snapshot = snapshot_at_or_before(snapshots, target)
    if snapshot is None:
        return None, [], "no_snapshot"
    rows = [
        row
        for row in load_snapshot_rows(snapshot)[1]
        if row_matches_entity(adapter, row, entity_key, snapshot.captured_at, alias_index)
    ]
    if not rows:
        return snapshot, [], "entity_absent"
    return snapshot, rows, "snapshot_used"


def _rows_for_entity(
    snapshot: ReportSnapshot,
    adapter: ReportFamilyAdapter,
    entity_key: CanonicalEntityKey,
    alias_index: AliasBindingIndex,
) -> list[dict[str, str]]:
    return [
        row
        for row in load_snapshot_rows(snapshot)[1]
        if row_matches_entity(adapter, row, entity_key, snapshot.captured_at, alias_index)
    ]


def _scoped_row_key(row: dict[str, str], adapter: ReportFamilyAdapter) -> str:
    scope = adapter.row_scope(row)
    if scope:
        return scope
    return adapter.canonical_value(row) or _extract_upn(adapter, row) or "__row__"


def _diff_rows(
    baseline_rows: list[dict[str, str]],
    latest_rows: list[dict[str, str]],
    adapter: ReportFamilyAdapter,
    family: str,
    baseline_at: datetime,
    latest_at: datetime,
) -> list[EntityChangeEvent]:
    events: list[EntityChangeEvent] = []
    baseline_map = {_scoped_row_key(row, adapter): row for row in baseline_rows}
    latest_map = {_scoped_row_key(row, adapter): row for row in latest_rows}

    for scope, row in latest_map.items():
        if scope not in baseline_map:
            events.append(
                EntityChangeEvent(
                    change_type="added",
                    family=family,
                    property="",
                    before="",
                    after="New row",
                    baseline_at=baseline_at,
                    latest_at=latest_at,
                    row_scope=scope if scope != "__row__" else adapter.row_scope(row),
                )
            )

    for scope, row in baseline_map.items():
        if scope not in latest_map:
            events.append(
                EntityChangeEvent(
                    change_type="removed",
                    family=family,
                    property="",
                    before="Existing row",
                    after="",
                    baseline_at=baseline_at,
                    latest_at=latest_at,
                    row_scope=scope if scope != "__row__" else adapter.row_scope(row),
                )
            )

    for scope in sorted(set(baseline_map) & set(latest_map)):
        before_row = baseline_map[scope]
        after_row = latest_map[scope]
        columns = set(before_row) | set(after_row)
        for column in sorted(columns):
            before_value = _row_value(before_row, column)
            after_value = _row_value(after_row, column)
            if before_value != after_value:
                events.append(
                    EntityChangeEvent(
                        change_type="modified",
                        family=family,
                        property=column,
                        before=before_value,
                        after=after_value,
                        baseline_at=baseline_at,
                        latest_at=latest_at,
                        row_scope=scope if scope != "__row__" else adapter.row_scope(after_row),
                    )
                )
    return events


def _family_coverage_label(status: FamilyCoverageStatus, snapshot: ReportSnapshot | None) -> str:
    if status == "snapshot_used" and snapshot is not None:
        return snapshot.label
    if status == "entity_absent":
        return "Entity not present in snapshot"
    if status == "no_snapshot":
        return "No snapshot at or before target"
    return "Not applicable"


def _build_family_state(
    adapter: ReportFamilyAdapter,
    rows: list[dict[str, str]],
    snapshot: ReportSnapshot,
) -> tuple[tuple[SourcedProperty, ...], tuple[ScopedRelationship, ...]]:
    if adapter.row_scope_columns:
        relationships: list[ScopedRelationship] = []
        for row in rows:
            props = tuple(
                SourcedProperty(
                    family=adapter.family,
                    name=name,
                    value=value,
                    observed_at=snapshot.captured_at,
                )
                for name, value in adapter.card_properties(row, snapshot.captured_at)
            )
            relationships.append(
                ScopedRelationship(
                    family=adapter.family,
                    row_scope=adapter.row_scope(row),
                    properties=props,
                    observed_at=snapshot.captured_at,
                )
            )
        return (), tuple(relationships)

    scalar: list[SourcedProperty] = []
    for row in rows:
        for name, value in adapter.card_properties(row, snapshot.captured_at):
            scalar.append(
                SourcedProperty(
                    family=adapter.family,
                    name=name,
                    value=value,
                    observed_at=snapshot.captured_at,
                )
            )
    return tuple(scalar), ()


def present_at_target(state: EntityState) -> EntityPresenceStatus:
    if any(item.status == "snapshot_used" for item in state.coverage):
        return "present"

    has_snapshot = any(
        item.status in ("snapshot_used", "entity_absent") for item in state.coverage
    )
    if not has_snapshot:
        return "unknown"

    authoritative_families = {
        adapter.family
        for adapter in adapters_for_type(state.key.entity_type)
        if adapter.authoritative_inventory
    }
    authoritative = [
        item for item in state.coverage if item.family in authoritative_families
    ]
    if any(item.status == "entity_absent" for item in authoritative):
        return "absent"

    return "partial"


def compare_entity_states(before: EntityState, after: EntityState) -> EntityStateDiff:
    added_props: list[tuple[str, str, str]] = []
    removed_props: list[tuple[str, str, str]] = []
    modified_props: list[tuple[str, str, str, str, str]] = []

    all_families = set(before.scalar_properties_by_family) | set(after.scalar_properties_by_family)
    for family in sorted(all_families):
        before_map = {
            prop.name: prop.value
            for prop in before.scalar_properties_by_family.get(family, ())
        }
        after_map = {
            prop.name: prop.value
            for prop in after.scalar_properties_by_family.get(family, ())
        }
        for name in sorted(set(before_map) | set(after_map)):
            before_value = before_map.get(name)
            after_value = after_map.get(name)
            if before_value is None and after_value is not None:
                added_props.append((family, name, after_value))
            elif before_value is not None and after_value is None:
                removed_props.append((family, name, before_value))
            elif before_value != after_value:
                modified_props.append((family, name, before_value or "", after_value or ""))

    added_rels: list[tuple[str, str]] = []
    removed_rels: list[tuple[str, str]] = []
    modified_rels: list[tuple[str, str, str, str]] = []

    rel_families = set(before.relationships_by_family) | set(after.relationships_by_family)
    for family in sorted(rel_families):
        before_scopes = {
            rel.row_scope: rel
            for rel in before.relationships_by_family.get(family, ())
        }
        after_scopes = {
            rel.row_scope: rel
            for rel in after.relationships_by_family.get(family, ())
        }
        for scope in sorted(set(before_scopes) | set(after_scopes)):
            before_rel = before_scopes.get(scope)
            after_rel = after_scopes.get(scope)
            if before_rel is None and after_rel is not None:
                added_rels.append((family, scope))
            elif before_rel is not None and after_rel is None:
                removed_rels.append((family, scope))
            elif before_rel is not None and after_rel is not None:
                before_text = "|".join(f"{p.name}={p.value}" for p in before_rel.properties)
                after_text = "|".join(f"{p.name}={p.value}" for p in after_rel.properties)
                if before_text != after_text:
                    modified_rels.append((family, scope, before_text, after_text))

    return EntityStateDiff(
        added_properties=tuple(added_props),
        removed_properties=tuple(removed_props),
        modified_properties=tuple(modified_props),
        added_relationships=tuple(added_rels),
        removed_relationships=tuple(removed_rels),
        modified_relationships=tuple(modified_rels),
    )


def build_entity_period_changes(
    entity_key: CanonicalEntityKey,
    families: dict[str, list[ReportSnapshot]],
    period: timedelta,
    reference: datetime | None = None,
) -> EntityPeriodChanges:
    reference_at, cutoff = period_window(period, reference)
    alias_index = build_alias_binding_index(families)

    events: list[EntityChangeEvent] = []
    notes: list[tuple[str, str]] = []

    for adapter in adapters_for_type(entity_key.entity_type):
        snapshots = families.get(adapter.family, [])
        if not snapshots:
            continue
        pairing = resolve_period_pair(snapshots, period, reference_at)
        if pairing.reason:
            notes.append((adapter.family, pairing.reason))
            continue
        baseline = pairing.baseline
        latest = pairing.latest
        assert baseline is not None and latest is not None

        baseline_rows = _rows_for_entity(baseline, adapter, entity_key, alias_index)
        latest_rows = _rows_for_entity(latest, adapter, entity_key, alias_index)
        events.extend(
            _diff_rows(
                baseline_rows,
                latest_rows,
                adapter,
                adapter.family,
                baseline.captured_at,
                latest.captured_at,
            )
        )

    events.sort(key=lambda item: (item.latest_at, item.family, item.property, item.row_scope))
    return EntityPeriodChanges(
        events=tuple(events),
        family_notes=tuple(notes),
        covered_from=cutoff,
        covered_to=reference_at,
    )


def reconstruct_entity_state(
    entity_key: CanonicalEntityKey,
    families: dict[str, list[ReportSnapshot]],
    target: datetime,
) -> EntityState:
    alias_index = build_alias_binding_index(families, max_observed_at=target)

    coverage_items: list[FamilyCoverage] = []
    family_coverage: dict[str, str] = {}
    scalar_properties_by_family: dict[str, tuple[SourcedProperty, ...]] = {}
    relationships_by_family: dict[str, tuple[ScopedRelationship, ...]] = {}
    properties_by_family: dict[str, tuple[SourcedProperty, ...]] = {}

    for adapter in adapters_for_type(entity_key.entity_type):
        snapshots = families.get(adapter.family, [])
        snapshot, rows, status = entity_rows_at(
            entity_key,
            adapter,
            snapshots,
            target,
            alias_index,
        )
        gap = None
        if snapshot is not None:
            gap = target - snapshot.captured_at
        coverage_items.append(
            FamilyCoverage(
                family=adapter.family,
                status=status,
                requested_at=target,
                snapshot_at=snapshot.captured_at if snapshot is not None else None,
                gap=gap,
                entity_present=status == "snapshot_used",
            )
        )
        family_coverage[adapter.family] = _family_coverage_label(status, snapshot)

        if status != "snapshot_used" or snapshot is None:
            continue

        scalar, relationships = _build_family_state(adapter, rows, snapshot)
        if scalar:
            scalar_properties_by_family[adapter.family] = scalar
            properties_by_family[adapter.family] = scalar
        if relationships:
            relationships_by_family[adapter.family] = relationships
            flat_props: list[SourcedProperty] = []
            for rel in relationships:
                flat_props.extend(rel.properties)
            properties_by_family[adapter.family] = tuple(flat_props)

    state = EntityState(
        as_of=target,
        key=entity_key,
        properties_by_family=properties_by_family,
        family_coverage=family_coverage,
        coverage=tuple(coverage_items),
        scalar_properties_by_family=scalar_properties_by_family,
        relationships_by_family=relationships_by_family,
    )
    return EntityState(
        as_of=target,
        key=entity_key,
        properties_by_family=properties_by_family,
        family_coverage=family_coverage,
        coverage=tuple(coverage_items),
        presence=present_at_target(state),
        scalar_properties_by_family=scalar_properties_by_family,
        relationships_by_family=relationships_by_family,
    )
