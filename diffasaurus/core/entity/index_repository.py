from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import IntEnum
from pathlib import Path

from diffasaurus.core.entity.bindings import AliasBindingIndex, ResolvedAlias
from diffasaurus.core.entity.history import (
    _diff_rows,
    _family_coverage_label,
    present_at_target,
    resolve_period_pair,
)
from diffasaurus.core.entity.family_aliases import entity_family_names_for_adapter
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_schema import fts5_enabled, open_connection
from diffasaurus.core.entity.registry import adapters_for_type
from diffasaurus.core.report_history import report_family
from diffasaurus.core.entity.resolution import SearchResult
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    EntityChangeEvent,
    EntityPeriodChanges,
    EntityRecord,
    EntityState,
    EntityType,
    FamilyCoverage,
    ScopedRelationship,
    SearchCapabilities,
    SourcedProperty,
    TimedAlias,
)
from diffasaurus.core.report_history import ReportSnapshot, period_window

_TOKEN_SPLIT = re.compile(r"[@._\-\s]+")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _normalize_query(query: str) -> str:
    return query.strip().casefold()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tokenize_value(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_SPLIT.split(value) if token}


@contextmanager
def _connect(db_path: Path, *, readonly: bool = True):
    connection = open_connection(db_path, readonly=readonly)
    try:
        yield connection
    finally:
        connection.close()


class _LRUCache:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def set(self, key: str, value: object) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class _SearchRank(IntEnum):
    EXACT_ID = 0
    EXACT_ALIAS = 1
    EXACT_DISPLAY = 2
    PREFIX_ID = 3
    PREFIX_ALIAS = 4
    PREFIX_DISPLAY = 5
    TOKEN_PREFIX = 6
    FTS = 7


def _rank_record(record: EntityRecord, normalized: str) -> _SearchRank | None:
    key = record.key.primary_id.casefold()
    if key == normalized:
        return _SearchRank.EXACT_ID
    display = record.display_name.casefold()
    if display == normalized:
        return _SearchRank.EXACT_DISPLAY
    for alias in record.aliases:
        if alias.value.casefold() == normalized:
            return _SearchRank.EXACT_ALIAS
    if key.startswith(normalized):
        return _SearchRank.PREFIX_ID
    for alias in record.aliases:
        if alias.value.casefold().startswith(normalized):
            return _SearchRank.PREFIX_ALIAS
    if display.startswith(normalized):
        return _SearchRank.PREFIX_DISPLAY
    for token in _search_tokens(record):
        if token.startswith(normalized):
            return _SearchRank.TOKEN_PREFIX
    return None


def _search_tokens(record: EntityRecord) -> set[str]:
    tokens: set[str] = set()
    tokens.update(_tokenize_value(record.key.primary_id))
    tokens.update(_tokenize_value(record.display_name))
    for alias in record.aliases:
        tokens.update(_tokenize_value(alias.value))
    return tokens


def _record_matches_query(record: EntityRecord, normalized: str) -> bool:
    if _rank_record(record, normalized) is not None:
        return True
    key = record.key.primary_id.casefold()
    display = record.display_name.casefold()
    if normalized in key or normalized in display:
        return True
    return any(normalized in alias.value.casefold() for alias in record.aliases)


def _is_ambiguous_search(normalized: str, records: list[EntityRecord]) -> bool:
    if len(records) < 2:
        return False
    keys = {record.key.primary_id for record in records}
    if len(keys) == 1:
        return False
    alias_hits = sum(
        1
        for record in records
        if any(normalized in alias.value.casefold() for alias in record.aliases)
    )
    return alias_hits > 1


class EntityIndexRepository:
    def __init__(
        self,
        db_path: Path,
        source_id: int,
        *,
        readonly: bool = True,
        fts5_enabled_flag: bool = False,
    ) -> None:
        self._db_path = db_path
        self._source_id = source_id
        self._readonly = readonly
        self._fts5_enabled = fts5_enabled_flag
        self._generation = self._read_db_generation()
        self._entity_cache = _LRUCache(256)
        self._state_cache = _LRUCache(128)
        self._search_cache = _LRUCache(128)
        self._capabilities = SearchCapabilities(
            substring_search=fts5_enabled_flag,
            fts5_enabled=fts5_enabled_flag,
        )

    @classmethod
    def open(
        cls,
        reports_dir: Path,
        *,
        db_path: Path | None = None,
        readonly: bool = True,
    ) -> EntityIndexRepository | None:
        path = db_path or entity_index_path(reports_dir)
        if not path.is_file():
            return None
        with _connect(path, readonly=True) as connection:
            row = connection.execute(
                "SELECT id FROM report_sources ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            source_id = int(row["id"])
            fts = fts5_enabled(connection)
        return cls(path, source_id, readonly=readonly, fts5_enabled_flag=fts)

    @property
    def generation(self) -> int:
        return self._generation

    def _read_db_generation(self) -> int:
        parts: list[str] = []
        try:
            parts.append(str(self._db_path.stat().st_mtime_ns))
        except OSError:
            parts.append("0")
        try:
            with _connect(self._db_path, readonly=True) as connection:
                from diffasaurus.core.entity.index_schema import metadata_value

                for key in (
                    "projection_repaired_at",
                    "alias_projection_version",
                    "search_projection_version",
                ):
                    value = metadata_value(connection, key)
                    if value:
                        parts.append(f"{key}={value}")
        except OSError:
            pass
        return abs(hash("|".join(parts)))

    def invalidate_caches(self) -> None:
        self._generation = self._read_db_generation()
        self._entity_cache.clear()
        self._state_cache.clear()
        self._search_cache.clear()

    def close(self) -> None:
        return

    def search_capabilities(self) -> SearchCapabilities:
        return self._capabilities

    def _aliases_for_entity(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        primary_id: str,
    ) -> list[TimedAlias]:
        alias_map: dict[tuple[str, str, str], TimedAlias] = {}
        for observation in connection.execute(
            """
            SELECT kind, normalized_value, observed_at, source_family
            FROM alias_observations
            WHERE source_id=? AND immutable_id=?
            ORDER BY observed_at
            """,
            (self._source_id, primary_id),
        ):
            seen_at = _parse_iso(observation["observed_at"]) or datetime.min
            key = (
                observation["kind"],
                observation["normalized_value"],
                observation["source_family"],
            )
            existing = alias_map.get(key)
            if existing is None:
                alias_map[key] = TimedAlias(
                    kind=observation["kind"],
                    value=observation["normalized_value"],
                    first_seen=seen_at,
                    last_seen=seen_at,
                    source_family=observation["source_family"],
                )
            else:
                alias_map[key] = TimedAlias(
                    kind=existing.kind,
                    value=existing.value,
                    first_seen=min(existing.first_seen, seen_at),
                    last_seen=max(existing.last_seen, seen_at),
                    source_family=existing.source_family,
                )
        for occurrence in connection.execute(
            """
            SELECT aliases_json, observed_at
            FROM entity_occurrences WHERE entity_id=?
            """,
            (entity_id,),
        ):
            seen_at = _parse_iso(occurrence["observed_at"]) or datetime.min
            for alias in json.loads(occurrence["aliases_json"] or "[]"):
                normalized = str(alias.get("value", "")).casefold()
                if not normalized:
                    continue
                key = (
                    alias.get("kind", ""),
                    normalized,
                    alias.get("source_family", ""),
                )
                display_value = str(alias.get("value", ""))
                existing = alias_map.get(key)
                if existing is None:
                    alias_map[key] = TimedAlias(
                        kind=key[0],
                        value=display_value,
                        first_seen=seen_at,
                        last_seen=seen_at,
                        source_family=key[2],
                    )
                else:
                    alias_map[key] = TimedAlias(
                        kind=existing.kind,
                        value=existing.value,
                        first_seen=min(existing.first_seen, seen_at),
                        last_seen=max(existing.last_seen, seen_at),
                        source_family=existing.source_family,
                    )
        return list(alias_map.values())

    def _row_to_record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> EntityRecord:
        key = CanonicalEntityKey(row["entity_type"], row["primary_id"])
        aliases = self._aliases_for_entity(connection, int(row["id"]), row["primary_id"])
        families = {
            item["source_family"]
            for item in connection.execute(
                """
                SELECT DISTINCT f.family AS source_family
                FROM entity_occurrences eo
                JOIN indexed_files f ON f.id = eo.file_id
                WHERE eo.entity_id=?
                """,
                (row["id"],),
            ).fetchall()
        }
        return EntityRecord(
            key=key,
            display_name=row["display_name"],
            aliases=aliases,
            source_families=set(families),
            first_seen=_parse_iso(row["first_seen"]),
            last_seen=_parse_iso(row["last_seen"]),
            present_in_latest=bool(row["present_in_latest"]),
        )

    def _entity_row(
        self,
        connection: sqlite3.Connection,
        key: CanonicalEntityKey,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM entities
            WHERE source_id=? AND entity_type=? AND primary_id=?
            """,
            (self._source_id, key.entity_type, key.primary_id),
        ).fetchone()

    def get_entity(self, key: CanonicalEntityKey) -> EntityRecord | None:
        cache_key = key.label()
        cached = self._entity_cache.get(cache_key)
        if isinstance(cached, EntityRecord):
            return cached
        with _connect(self._db_path, readonly=self._readonly) as connection:
            row = self._entity_row(connection, key)
            if row is None:
                return None
            record = self._row_to_record(connection, row)
        self._entity_cache.set(cache_key, record)
        return record

    def autocomplete_prefix(
        self,
        prefix: str,
        entity_type: EntityType,
        *,
        limit: int = 50,
    ) -> list[str]:
        needle = _normalize_query(prefix)
        if not needle:
            return []
        pattern = _escape_like(needle) + "%"
        suggestions: set[str] = set()
        with _connect(self._db_path, readonly=self._readonly) as connection:
            for row in connection.execute(
                """
                SELECT DISTINCT ao.normalized_value AS display_value
                FROM alias_observations ao
                JOIN entities e ON e.source_id = ao.source_id AND e.primary_id = ao.immutable_id
                WHERE e.source_id=? AND e.entity_type=? AND ao.normalized_value LIKE ? ESCAPE '\\'
                LIMIT ?
                """,
                (self._source_id, entity_type, pattern, limit),
            ):
                suggestions.add(row["display_value"])
            remaining = limit - len(suggestions)
            if remaining > 0:
                for row in connection.execute(
                    """
                    SELECT DISTINCT ea.display_value
                    FROM entity_aliases ea
                    JOIN entities e ON e.id = ea.entity_id
                    WHERE e.source_id=? AND e.entity_type=? AND ea.normalized_value LIKE ? ESCAPE '\\'
                    LIMIT ?
                    """,
                    (self._source_id, entity_type, pattern, remaining),
                ):
                    suggestions.add(row["display_value"])
        return sorted(suggestions, key=str.casefold)[:limit]

    def _load_entity_by_id(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        entity_type: EntityType,
    ) -> EntityRecord | None:
        row = connection.execute(
            """
            SELECT * FROM entities
            WHERE id=? AND source_id=? AND entity_type=?
            """,
            (entity_id, self._source_id, entity_type),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(connection, row)

    def _note_candidate(
        self,
        ranked: dict[str, tuple[_SearchRank, str]],
        label: str,
        rank: _SearchRank,
        sort_key: str,
    ) -> None:
        current = ranked.get(label)
        if current is None or rank < current[0]:
            ranked[label] = (rank, sort_key)

    def search(
        self,
        query: str,
        entity_type: EntityType,
        *,
        limit: int = 50,
    ) -> SearchResult:
        normalized = _normalize_query(query)
        if not normalized:
            return SearchResult((), False)
        cache_key = f"{self._generation}:{entity_type}:{normalized}:{limit}"
        cached = self._search_cache.get(cache_key)
        if isinstance(cached, SearchResult):
            return cached

        ranked: dict[str, tuple[_SearchRank, str]] = {}
        like_prefix = _escape_like(normalized) + "%"

        with _connect(self._db_path, readonly=self._readonly) as connection:
            exact_id = self._entity_row(
                connection,
                CanonicalEntityKey(entity_type, query.strip()),
            )
            if exact_id is None:
                exact_id = connection.execute(
                    """
                    SELECT * FROM entities
                    WHERE source_id=? AND entity_type=? AND lower(primary_id)=?
                    """,
                    (self._source_id, entity_type, normalized),
                ).fetchone()
            if exact_id is not None:
                label = f"{entity_type}:{exact_id['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.EXACT_ID,
                    exact_id["display_name"].casefold(),
                )

            for row in connection.execute(
                """
                SELECT e.id, e.primary_id, e.display_name
                FROM entity_aliases ea
                JOIN entities e ON e.id = ea.entity_id
                WHERE e.source_id=? AND e.entity_type=? AND ea.normalized_value=?
                """,
                (self._source_id, entity_type, normalized),
            ):
                label = f"{entity_type}:{row['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.EXACT_ALIAS,
                    row["display_name"].casefold(),
                )
            for row in connection.execute(
                """
                SELECT e.id, e.primary_id, e.display_name
                FROM alias_observations ao
                JOIN entities e ON e.source_id = ao.source_id AND e.primary_id = ao.immutable_id
                WHERE e.source_id=? AND e.entity_type=? AND ao.normalized_value=?
                """,
                (self._source_id, entity_type, normalized),
            ):
                label = f"{entity_type}:{row['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.EXACT_ALIAS,
                    row["display_name"].casefold(),
                )

            for row in connection.execute(
                """
                SELECT primary_id, display_name
                FROM entities
                WHERE source_id=? AND entity_type=? AND lower(display_name)=?
                """,
                (self._source_id, entity_type, normalized),
            ):
                label = f"{entity_type}:{row['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.EXACT_DISPLAY,
                    row["display_name"].casefold(),
                )

            for row in connection.execute(
                """
                SELECT primary_id, display_name
                FROM entities
                WHERE source_id=? AND entity_type=? AND lower(primary_id) LIKE ? ESCAPE '\\'
                """,
                (self._source_id, entity_type, like_prefix),
            ):
                label = f"{entity_type}:{row['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.PREFIX_ID,
                    row["display_name"].casefold(),
                )

            for row in connection.execute(
                """
                SELECT e.primary_id, e.display_name
                FROM entity_aliases ea
                JOIN entities e ON e.id = ea.entity_id
                WHERE e.source_id=? AND e.entity_type=? AND ea.normalized_value LIKE ? ESCAPE '\\'
                """,
                (self._source_id, entity_type, like_prefix),
            ):
                label = f"{entity_type}:{row['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.PREFIX_ALIAS,
                    row["display_name"].casefold(),
                )
            for row in connection.execute(
                """
                SELECT e.primary_id, e.display_name
                FROM alias_observations ao
                JOIN entities e ON e.source_id = ao.source_id AND e.primary_id = ao.immutable_id
                WHERE e.source_id=? AND e.entity_type=? AND ao.normalized_value LIKE ? ESCAPE '\\'
                """,
                (self._source_id, entity_type, like_prefix),
            ):
                label = f"{entity_type}:{row['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.PREFIX_ALIAS,
                    row["display_name"].casefold(),
                )

            for row in connection.execute(
                """
                SELECT primary_id, display_name
                FROM entities
                WHERE source_id=? AND entity_type=? AND lower(display_name) LIKE ? ESCAPE '\\'
                """,
                (self._source_id, entity_type, like_prefix),
            ):
                label = f"{entity_type}:{row['primary_id']}"
                self._note_candidate(
                    ranked,
                    label,
                    _SearchRank.PREFIX_DISPLAY,
                    row["display_name"].casefold(),
                )

            if self._fts5_enabled:
                fts_term = normalized.replace('"', '""')
                fts_query = f'"{fts_term}"*'
                try:
                    for row in connection.execute(
                        """
                        SELECT entity_id FROM entity_search_fts
                        WHERE entity_search_fts MATCH ? AND entity_type=?
                        LIMIT ?
                        """,
                        (fts_query, entity_type, limit * 4),
                    ):
                        entity_row = connection.execute(
                            "SELECT primary_id, display_name FROM entities WHERE id=?",
                            (row["entity_id"],),
                        ).fetchone()
                        if entity_row is None:
                            continue
                        label = f"{entity_type}:{entity_row['primary_id']}"
                        self._note_candidate(
                            ranked,
                            label,
                            _SearchRank.FTS,
                            entity_row["display_name"].casefold(),
                        )
                except sqlite3.OperationalError:
                    pass

            records: list[EntityRecord] = []
            ordered_labels = sorted(
                ranked,
                key=lambda label: (ranked[label][0], ranked[label][1], label),
            )
            for label in ordered_labels:
                if len(records) >= limit:
                    break
                entity_type_name, primary_id = label.split(":", 1)
                row = self._entity_row(
                    connection,
                    CanonicalEntityKey(entity_type_name, primary_id),  # type: ignore[arg-type]
                )
                if row is None:
                    continue
                record = self._row_to_record(connection, row)
                if not _record_matches_query(record, normalized):
                    continue
                records.append(record)

        ambiguous = _is_ambiguous_search(normalized, records)
        result = SearchResult(tuple(records), ambiguous)
        self._search_cache.set(cache_key, result)
        return result

    def resolve_alias(self, kind: str, value: str, as_of: datetime) -> ResolvedAlias:
        index = AliasBindingIndex()
        with _connect(self._db_path, readonly=self._readonly) as connection:
            for row in connection.execute(
                """
                SELECT kind, normalized_value, observed_at, immutable_id, source_family
                FROM alias_observations
                WHERE source_id=? AND kind=? AND normalized_value=? AND observed_at <= ?
                ORDER BY observed_at
                """,
                (
                    self._source_id,
                    kind,
                    value.lower(),
                    as_of.isoformat(timespec="seconds"),
                ),
            ):
                index.record(
                    row["kind"],
                    row["normalized_value"],
                    datetime.fromisoformat(row["observed_at"]),
                    row["immutable_id"],
                    row["source_family"],
                )
        return index.resolve(kind, value.lower(), as_of)

    def _occurrence_at_or_before(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        family: str,
        target: datetime,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT eo.*, f.captured_at AS snapshot_captured_at
            FROM entity_occurrences eo
            JOIN indexed_files f ON f.id = eo.file_id
            WHERE eo.entity_id=? AND f.family=? AND f.status='indexed'
              AND f.captured_at <= ?
            ORDER BY f.captured_at DESC
            LIMIT 1
            """,
            (entity_id, family, target.isoformat(timespec="seconds")),
        ).fetchone()

    def _latest_indexed_file_row(
        self,
        connection: sqlite3.Connection,
        canonical_family: str,
        target: datetime,
    ) -> sqlite3.Row | None:
        families = entity_family_names_for_adapter(canonical_family)
        placeholders = ",".join("?" * len(families))
        return connection.execute(
            f"""
            SELECT id, captured_at, family, relative_path
            FROM indexed_files
            WHERE source_id=? AND family IN ({placeholders}) AND status='indexed'
              AND captured_at <= ?
            ORDER BY captured_at DESC LIMIT 1
            """,
            (
                self._source_id,
                *families,
                target.isoformat(timespec="seconds"),
            ),
        ).fetchone()

    def _occurrence_for_file(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        file_id: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT eo.*, f.captured_at AS snapshot_captured_at
            FROM entity_occurrences eo
            JOIN indexed_files f ON f.id = eo.file_id
            WHERE eo.entity_id=? AND eo.file_id=?
            """,
            (entity_id, file_id),
        ).fetchone()

    def _deserialize_state(
        self,
        entity_key: CanonicalEntityKey,
        target: datetime,
        adapter_family: str,
        occurrence: sqlite3.Row | None,
        status: str,
        snapshot_at: datetime | None,
        *,
        source_relative_path: str = "",
        source_report_family: str = "",
    ) -> tuple[FamilyCoverage, tuple[SourcedProperty, ...], tuple[ScopedRelationship, ...]]:
        gap = None
        if snapshot_at is not None:
            gap = target - snapshot_at
        coverage = FamilyCoverage(
            family=adapter_family,
            status=status,  # type: ignore[arg-type]
            requested_at=target,
            snapshot_at=snapshot_at,
            gap=gap,
            entity_present=status == "snapshot_used",
            source_relative_path=source_relative_path,
            source_report_family=source_report_family,
        )
        if occurrence is None or status != "snapshot_used":
            return coverage, (), ()
        observed_at = datetime.fromisoformat(occurrence["observed_at"])
        scalar_props = [
            SourcedProperty(
                family=item["family"],
                name=item["name"],
                value=item["value"],
                observed_at=datetime.fromisoformat(item["observed_at"]),
            )
            for item in json.loads(occurrence["scalar_properties_json"] or "[]")
        ]
        relationships = [
            ScopedRelationship(
                family=item["family"],
                row_scope=item["row_scope"],
                properties=tuple(
                    SourcedProperty(
                        family=prop["family"],
                        name=prop["name"],
                        value=prop["value"],
                        observed_at=datetime.fromisoformat(prop["observed_at"]),
                    )
                    for prop in item.get("properties", [])
                ),
                observed_at=observed_at,
            )
            for item in json.loads(occurrence["relationships_json"] or "[]")
        ]
        return coverage, tuple(scalar_props), tuple(relationships)

    def reconstruct_state(
        self,
        entity_key: CanonicalEntityKey,
        target: datetime,
    ) -> EntityState:
        cache_key = f"{self._generation}:{entity_key.label()}@{target.isoformat(timespec='seconds')}"
        cached = self._state_cache.get(cache_key)
        if isinstance(cached, EntityState):
            return cached
        entity = self.get_entity(entity_key)
        if entity is None:
            raise ValueError(f"Unknown entity {entity_key.label()}")

        coverage_items: list[FamilyCoverage] = []
        family_coverage: dict[str, str] = {}
        scalar_properties_by_family: dict[str, tuple[SourcedProperty, ...]] = {}
        relationships_by_family: dict[str, tuple[ScopedRelationship, ...]] = {}
        properties_by_family: dict[str, tuple[SourcedProperty, ...]] = {}

        with _connect(self._db_path, readonly=self._readonly) as connection:
            entity_id = connection.execute(
                "SELECT id FROM entities WHERE source_id=? AND entity_type=? AND primary_id=?",
                (self._source_id, entity_key.entity_type, entity_key.primary_id),
            ).fetchone()["id"]

            for adapter in adapters_for_type(entity_key.entity_type):
                file_row = self._latest_indexed_file_row(
                    connection,
                    adapter.family,
                    target,
                )
                if file_row is None:
                    coverage, _, _ = self._deserialize_state(
                        entity_key, target, adapter.family, None, "no_snapshot", None
                    )
                    coverage_items.append(coverage)
                    family_coverage[adapter.family] = _family_coverage_label("no_snapshot", None)
                    continue
                snapshot_at = datetime.fromisoformat(file_row["captured_at"])
                source_relative_path = str(file_row["relative_path"] or "")
                source_report_family = report_family(source_relative_path)
                occurrence = self._occurrence_for_file(
                    connection,
                    entity_id,
                    int(file_row["id"]),
                )
                if occurrence is None:
                    coverage, _, _ = self._deserialize_state(
                        entity_key,
                        target,
                        adapter.family,
                        None,
                        "entity_absent",
                        snapshot_at,
                        source_relative_path=source_relative_path,
                        source_report_family=source_report_family,
                    )
                    coverage_items.append(coverage)
                    family_coverage[adapter.family] = _family_coverage_label(
                        "entity_absent",
                        None,
                    )
                    continue
                coverage, scalar, relationships = self._deserialize_state(
                    entity_key,
                    target,
                    adapter.family,
                    occurrence,
                    "snapshot_used",
                    snapshot_at,
                    source_relative_path=source_relative_path,
                    source_report_family=source_report_family,
                )
                coverage_items.append(coverage)
                family_coverage[adapter.family] = _family_coverage_label(
                    "snapshot_used",
                    None,
                )
                if scalar:
                    scalar_properties_by_family[adapter.family] = scalar
                    properties_by_family[adapter.family] = scalar
                if relationships:
                    relationships_by_family[adapter.family] = relationships
                    flat: list[SourcedProperty] = []
                    for rel in relationships:
                        flat.extend(rel.properties)
                    properties_by_family[adapter.family] = tuple(flat)

        draft = EntityState(
            as_of=target,
            key=entity_key,
            properties_by_family=properties_by_family,
            family_coverage=family_coverage,
            coverage=tuple(coverage_items),
            scalar_properties_by_family=scalar_properties_by_family,
            relationships_by_family=relationships_by_family,
        )
        state = EntityState(
            as_of=target,
            key=entity_key,
            properties_by_family=properties_by_family,
            family_coverage=family_coverage,
            coverage=tuple(coverage_items),
            presence=present_at_target(draft),
            scalar_properties_by_family=scalar_properties_by_family,
            relationships_by_family=relationships_by_family,
        )
        self._state_cache.set(cache_key, state)
        return state

    def _occurrence_rows_for_diff(
        self,
        occurrence: sqlite3.Row | None,
        adapter,
    ) -> list[dict[str, str]]:
        if occurrence is None:
            return []
        if adapter.row_scope_columns:
            rows: list[dict[str, str]] = []
            for item in json.loads(occurrence["relationships_json"] or "[]"):
                row = {prop["name"]: prop["value"] for prop in item.get("properties", [])}
                rows.append(row)
            return rows
        row: dict[str, str] = {}
        for item in json.loads(occurrence["scalar_properties_json"] or "[]"):
            row[item["name"]] = item["value"]
        return [row] if row else []

    def period_changes(
        self,
        entity_key: CanonicalEntityKey,
        period: timedelta,
        reference: datetime | None = None,
    ) -> EntityPeriodChanges:
        reference_at, cutoff = period_window(period, reference)
        entity = self.get_entity(entity_key)
        if entity is None:
            raise ValueError(f"Unknown entity {entity_key.label()}")
        events: list[EntityChangeEvent] = []
        notes: list[tuple[str, str]] = []

        with _connect(self._db_path, readonly=self._readonly) as connection:
            entity_id = connection.execute(
                "SELECT id FROM entities WHERE source_id=? AND entity_type=? AND primary_id=?",
                (self._source_id, entity_key.entity_type, entity_key.primary_id),
            ).fetchone()["id"]

            for adapter in adapters_for_type(entity_key.entity_type):
                snapshots = [
                    ReportSnapshot(
                        path=Path(row["relative_path"]),
                        family=adapter.family,
                        captured_at=datetime.fromisoformat(row["captured_at"]),
                        row_count=0,
                        headers=(),
                    )
                    for row in connection.execute(
                        """
                        SELECT captured_at, relative_path FROM indexed_files
                        WHERE source_id=? AND family=? AND status='indexed'
                        ORDER BY captured_at
                        """,
                        (self._source_id, adapter.family),
                    )
                ]
                if not snapshots:
                    continue
                pairing = resolve_period_pair(snapshots, period, reference_at)
                if pairing.reason:
                    notes.append((adapter.family, pairing.reason))
                    continue
                baseline = pairing.baseline
                latest = pairing.latest
                assert baseline is not None and latest is not None
                baseline_occurrence = connection.execute(
                    """
                    SELECT eo.*
                    FROM entity_occurrences eo
                    JOIN indexed_files f ON f.id = eo.file_id
                    WHERE eo.entity_id=? AND f.family=? AND f.captured_at=?
                    """,
                    (
                        entity_id,
                        adapter.family,
                        baseline.captured_at.isoformat(timespec="seconds"),
                    ),
                ).fetchone()
                latest_occurrence = connection.execute(
                    """
                    SELECT eo.*
                    FROM entity_occurrences eo
                    JOIN indexed_files f ON f.id = eo.file_id
                    WHERE eo.entity_id=? AND f.family=? AND f.captured_at=?
                    """,
                    (
                        entity_id,
                        adapter.family,
                        latest.captured_at.isoformat(timespec="seconds"),
                    ),
                ).fetchone()
                baseline_rows = self._occurrence_rows_for_diff(baseline_occurrence, adapter)
                latest_rows = self._occurrence_rows_for_diff(latest_occurrence, adapter)
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

    def stats(self) -> dict[str, int]:
        with _connect(self._db_path, readonly=self._readonly) as connection:
            entity_count = connection.execute(
                "SELECT COUNT(*) AS count FROM entities WHERE source_id=?",
                (self._source_id,),
            ).fetchone()["count"]
            file_count = connection.execute(
                "SELECT COUNT(*) AS count FROM indexed_files WHERE source_id=? AND status='indexed'",
                (self._source_id,),
            ).fetchone()["count"]
        return {"entity_count": int(entity_count), "files_indexed": int(file_count)}
