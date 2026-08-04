from __future__ import annotations

from datetime import datetime, timedelta

from diffasaurus.core.entity.adapters import ReportFamilyAdapter
from diffasaurus.core.entity.registry import ADAPTERS_BY_FAMILY, adapters_for_type
from diffasaurus.core.entity.snapshots import load_snapshot_rows, snapshot_at_or_before
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    EntityChangeEvent,
    EntityPeriodChanges,
    EntityState,
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


def row_entity_key(
    adapter: ReportFamilyAdapter,
    row: dict[str, str],
    observed_at: datetime,
    upn_bindings: dict[tuple[str, datetime], str],
) -> CanonicalEntityKey | None:
    key = adapter.build_key(row)
    if key is None:
        return None

    if key.entity_type == "user" and key.primary_id.startswith("upn_only:"):
        upn = _extract_upn(adapter, row)
        if upn:
            bound = upn_bindings.get((upn.lower(), observed_at))
            if bound:
                return CanonicalEntityKey("user", bound)

    return key


def _extract_upn(adapter: ReportFamilyAdapter, row: dict[str, str]) -> str:
    for alias in adapter.alias_columns:
        if alias.kind == "upn":
            value = _row_value(row, alias.column)
            if value:
                return value
    return ""


def _record_upn_binding(
    row: dict[str, str],
    adapter: ReportFamilyAdapter,
    observed_at: datetime,
    upn_bindings: dict[tuple[str, datetime], str],
) -> None:
    user_id = adapter.canonical_value(row)
    upn = _extract_upn(adapter, row)
    if user_id and upn:
        upn_bindings[(upn.lower(), observed_at)] = user_id


def row_matches_entity(
    adapter: ReportFamilyAdapter,
    row: dict[str, str],
    entity_key: CanonicalEntityKey,
    observed_at: datetime,
    upn_bindings: dict[tuple[str, datetime], str],
) -> bool:
    key = row_entity_key(adapter, row, observed_at, upn_bindings)
    return key is not None and key.primary_id == entity_key.primary_id and key.entity_type == entity_key.entity_type


def _rows_for_entity(
    snapshot: ReportSnapshot,
    adapter: ReportFamilyAdapter,
    entity_key: CanonicalEntityKey,
    upn_bindings: dict[tuple[str, datetime], str],
) -> list[dict[str, str]]:
    _, rows = load_snapshot_rows(snapshot)
    return [
        row
        for row in rows
        if row_matches_entity(adapter, row, entity_key, snapshot.captured_at, upn_bindings)
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


def build_entity_period_changes(
    entity_key: CanonicalEntityKey,
    families: dict[str, list[ReportSnapshot]],
    period: timedelta,
    reference: datetime | None = None,
) -> EntityPeriodChanges:
    reference_at, cutoff = period_window(period, reference)
    upn_bindings: dict[tuple[str, datetime], str] = {}
    for family in _BINDING_FAMILIES:
        adapter = ADAPTERS_BY_FAMILY.get(family)
        snapshots = families.get(family, [])
        if not adapter:
            continue
        for snapshot in snapshots:
            _, rows = load_snapshot_rows(snapshot)
            for row in rows:
                _record_upn_binding(row, adapter, snapshot.captured_at, upn_bindings)

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

        baseline_rows = _rows_for_entity(baseline, adapter, entity_key, upn_bindings)
        latest_rows = _rows_for_entity(latest, adapter, entity_key, upn_bindings)
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
    upn_bindings: dict[tuple[str, datetime], str] = {}
    for family in _BINDING_FAMILIES:
        adapter = ADAPTERS_BY_FAMILY.get(family)
        snapshots = families.get(family, [])
        if not adapter:
            continue
        for snapshot in snapshots:
            if snapshot.captured_at > target:
                continue
            _, rows = load_snapshot_rows(snapshot)
            for row in rows:
                _record_upn_binding(row, adapter, snapshot.captured_at, upn_bindings)

    properties_by_family: dict[str, tuple[SourcedProperty, ...]] = {}
    coverage: dict[str, str] = {}

    for adapter in adapters_for_type(entity_key.entity_type):
        snapshots = families.get(adapter.family, [])
        if not snapshots:
            continue
        snapshot = snapshot_at_or_before(snapshots, target)
        if snapshot is None:
            coverage[adapter.family] = "No snapshot at or before target"
            continue
        rows = _rows_for_entity(snapshot, adapter, entity_key, upn_bindings)
        if not rows:
            coverage[adapter.family] = "Entity not present in snapshot"
            continue
        coverage[adapter.family] = snapshot.label
        props: list[SourcedProperty] = []
        for row in rows:
            for name, value in adapter.card_properties(row, snapshot.captured_at):
                props.append(
                    SourcedProperty(
                        family=adapter.family,
                        name=name,
                        value=value,
                        observed_at=snapshot.captured_at,
                    )
                )
        properties_by_family[adapter.family] = tuple(props)

    return EntityState(
        as_of=target,
        key=entity_key,
        properties_by_family=properties_by_family,
        family_coverage=coverage,
    )
