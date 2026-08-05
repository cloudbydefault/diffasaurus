from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, replace
from datetime import datetime

from diffasaurus.core.entity.adapters import ReportFamilyAdapter
from diffasaurus.core.entity.bindings import AliasBindingIndex
from diffasaurus.core.entity.history import (
    _BINDING_FAMILIES,
    _extract_upn,
    row_entity_key,
)
from diffasaurus.core.entity.registry import ADAPTERS_BY_FAMILY
from diffasaurus.core.entity.snapshots import load_snapshot_rows
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    EntityIndexStats,
    EntityRecord,
    EntityType,
    SourcedProperty,
    TimedAlias,
)
from diffasaurus.core.report_history import ReportSnapshot

logger = logging.getLogger(__name__)


class EntityIndexCancelled(Exception):
    """Raised when entity indexing is interrupted before completion."""


def _row_value(row: dict[str, str], column: str) -> str:
    normalized = {header.lower(): header for header in row}
    actual = normalized.get(column.lower())
    if not actual:
        return ""
    return str(row.get(actual, "") or "").strip()


@dataclass
class SearchResult:
    matches: tuple[EntityRecord, ...]
    ambiguous: bool


class EntityResolver:
    def __init__(self) -> None:
        self._records: dict[str, EntityRecord] = {}

    @property
    def records(self) -> tuple[EntityRecord, ...]:
        return tuple(self._records.values())

    def get(self, key: CanonicalEntityKey) -> EntityRecord | None:
        return self._records.get(key.label())

    def build_index(
        self,
        families: dict[str, list[ReportSnapshot]],
        cancelled: threading.Event | None = None,
        stats: EntityIndexStats | None = None,
        progress=None,
    ) -> None:
        started = time.perf_counter()
        binding_started = time.perf_counter()
        self._records.clear()
        alias_index = AliasBindingIndex()
        latest_keys: dict[str, set[str]] = {}
        total_snapshots = sum(len(snapshots) for snapshots in families.values())
        progress_interval = max(1, total_snapshots // 100) if total_snapshots else 1

        def _check_cancelled() -> None:
            if cancelled is not None and cancelled.is_set():
                raise EntityIndexCancelled()

        def _load_rows(snapshot: ReportSnapshot) -> tuple[tuple[str, ...], list[dict[str, str]]]:
            if stats is not None:
                stats.snapshots_scanned += 1
                if progress is not None and (
                    stats.snapshots_scanned == 1
                    or stats.snapshots_scanned == total_snapshots
                    or stats.snapshots_scanned % progress_interval == 0
                ):
                    progress(
                        stats.snapshots_scanned,
                        total_snapshots,
                        snapshot.path.name,
                    )
            return load_snapshot_rows(snapshot, stats)

        def _process_snapshot(
            family: str,
            adapter: ReportFamilyAdapter,
            snapshot: ReportSnapshot,
            latest_snapshot: ReportSnapshot,
            record_bindings: bool,
        ) -> None:
            _check_cancelled()
            _, rows = _load_rows(snapshot)
            for row in rows:
                if record_bindings:
                    user_id = adapter.canonical_value(row)
                    upn = _extract_upn(adapter, row)
                    if user_id and upn:
                        alias_index.record(
                            "upn",
                            upn.lower(),
                            snapshot.captured_at,
                            user_id,
                            adapter.family,
                        )
                key = row_entity_key(adapter, row, snapshot.captured_at, alias_index)
                if key is None:
                    continue
                record = self._records.setdefault(
                    key.label(),
                    EntityRecord(key=key, display_name=adapter.display_name(row)),
                )
                self._touch_record(record, adapter, row, snapshot.captured_at)
                if snapshot.path == latest_snapshot.path:
                    latest_keys[family].add(key.label())

        for family in _BINDING_FAMILIES:
            adapter = ADAPTERS_BY_FAMILY.get(family)
            snapshots = families.get(family, [])
            if not adapter or not snapshots:
                continue
            latest_keys[family] = set()
            latest_snapshot = snapshots[-1]
            for snapshot in snapshots:
                _process_snapshot(family, adapter, snapshot, latest_snapshot, record_bindings=True)

        if stats is not None:
            stats.binding_seconds = time.perf_counter() - binding_started

        for family, snapshots in families.items():
            if family in _BINDING_FAMILIES:
                continue
            adapter = ADAPTERS_BY_FAMILY.get(family)
            if not adapter or not snapshots:
                continue
            latest_keys[family] = set()
            latest_snapshot = snapshots[-1]
            for snapshot in snapshots:
                _process_snapshot(family, adapter, snapshot, latest_snapshot, record_bindings=False)

        for record in self._records.values():
            record.present_in_latest = any(
                record.key.label() in latest_keys.get(family, set())
                for family in record.source_families
            )

        if stats is not None:
            stats.entity_count = len(self._records)
            stats.total_seconds = time.perf_counter() - started

    def search(self, query: str, entity_type: EntityType) -> SearchResult:
        needle = query.strip().lower()
        if not needle:
            return SearchResult((), False)

        matches: list[EntityRecord] = []
        for record in self._records.values():
            if record.key.entity_type != entity_type:
                continue
            if self._record_matches(record, needle):
                matches.append(record)

        unique = {record.key.label(): record for record in matches}
        ordered = sorted(unique.values(), key=lambda item: item.display_name.lower())
        ambiguous = self._is_ambiguous_search(needle, ordered)
        return SearchResult(tuple(ordered), ambiguous)

    def _record_matches(self, record: EntityRecord, needle: str) -> bool:
        if needle in record.key.primary_id.lower():
            return True
        if needle in record.display_name.lower():
            return True
        return any(needle in alias.value.lower() for alias in record.aliases)

    def _is_ambiguous_search(self, needle: str, records: list[EntityRecord]) -> bool:
        if len(records) < 2:
            return False
        keys = {record.key.primary_id for record in records}
        if len(keys) == 1:
            return False
        alias_hits = sum(
            1
            for record in records
            if any(needle in alias.value.lower() for alias in record.aliases)
        )
        return alias_hits > 1

    def _touch_record(
        self,
        record: EntityRecord,
        adapter: ReportFamilyAdapter,
        row: dict[str, str],
        observed_at: datetime,
    ) -> None:
        record.source_families.add(adapter.family)
        display_name = adapter.display_name(row)
        if display_name and display_name != "Unknown":
            record.display_name = display_name

        if record.first_seen is None or observed_at < record.first_seen:
            record.first_seen = observed_at
        if record.last_seen is None or observed_at > record.last_seen:
            record.last_seen = observed_at

        for alias_column in adapter.alias_columns:
            value = _row_value(row, alias_column.column)
            if not value:
                continue
            self._add_alias(record, alias_column.kind, value, observed_at, adapter.family)

        existing = record.properties_by_family.get(adapter.family)
        existing_time = existing[0].observed_at if existing else None
        if existing_time is None or observed_at >= existing_time:
            record.properties_by_family[adapter.family] = [
                SourcedProperty(
                    family=adapter.family,
                    name=name,
                    value=value,
                    observed_at=observed_at,
                )
                for name, value in adapter.card_properties(row, observed_at)
            ]

    def _add_alias(
        self,
        record: EntityRecord,
        kind: str,
        value: str,
        observed_at: datetime,
        source_family: str,
    ) -> None:
        normalized = value.lower()
        for index, alias in enumerate(record.aliases):
            if alias.kind == kind and alias.value.lower() == normalized and alias.source_family == source_family:
                record.aliases[index] = replace(
                    alias,
                    first_seen=min(alias.first_seen, observed_at),
                    last_seen=max(alias.last_seen, observed_at),
                )
                return
        record.aliases.append(
            TimedAlias(
                kind=kind,
                value=value,
                first_seen=observed_at,
                last_seen=observed_at,
                source_family=source_family,
            )
        )


def build_entity_resolver(
    families: dict[str, list[ReportSnapshot]],
    cancelled: threading.Event | None = None,
    stats: EntityIndexStats | None = None,
    progress=None,
) -> EntityResolver:
    resolver = EntityResolver()
    resolver.build_index(families, cancelled=cancelled, stats=stats, progress=progress)
    if stats is not None:
        logger.info(
            "Entity index: %d snapshots, %d parsed, %d cache hits, "
            "%d entities, %.1fs total (binding %.1fs)",
            stats.snapshots_scanned,
            stats.csv_parsed,
            stats.csv_cache_hits,
            stats.entity_count,
            stats.total_seconds,
            stats.binding_seconds,
        )
    return resolver
