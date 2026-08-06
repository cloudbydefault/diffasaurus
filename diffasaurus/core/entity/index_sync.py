from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from diffasaurus.core.entity.adapters import ALL_ADAPTERS, ReportFamilyAdapter
from diffasaurus.core.entity.bindings import AliasBindingIndex
from diffasaurus.core.entity.history import (
    _BINDING_FAMILIES,
    _extract_upn,
    classify_row_entity_key,
    row_entity_key,
)
from diffasaurus.core.entity.index_lock import EntityIndexLockError, acquire_entity_index_lock
from diffasaurus.core.entity.index_projection import (
    projections_need_repair,
    repair_search_projections,
)
from diffasaurus.core.entity.index_paths import (
    cleanup_index_files,
    entity_index_path,
    normalize_reports_path,
    relative_report_path,
    source_key,
)
from diffasaurus.core.entity.index_progress import SyncCompleteEvent, SyncProgressEvent
from diffasaurus.core.entity.index_schema import (
    initialize_schema,
    metadata_value,
    open_connection,
    transaction,
    utc_now_iso,
)
from diffasaurus.core.entity.family_aliases import (
    ENTITY_FAMILY_ALIASES,
    canonical_entity_family,
)
from diffasaurus.core.entity.registry import ADAPTERS_BY_FAMILY
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import read_csv_rows, report_family, report_timestamp

logger = logging.getLogger(__name__)


def compute_adapter_version() -> str:
    payload: list[dict] = []
    for adapter in ALL_ADAPTERS:
        payload.append(
            {
                "family": adapter.family,
                "entity_type": adapter.entity_type,
                "canonical_columns": adapter.canonical_columns,
                "alias_columns": [(item.kind, item.column) for item in adapter.alias_columns],
                "display_name_column": adapter.display_name_column,
                "row_scope_columns": adapter.row_scope_columns,
                "card_columns": adapter.card_columns,
                "authoritative_inventory": adapter.authoritative_inventory,
            }
        )
    payload.append({"entity_family_aliases": ENTITY_FAMILY_ALIASES})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_row_hash(payload: object, aliases: list) -> str:
    body = canonical_json({"payload": payload, "aliases": aliases})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _row_value(row: dict[str, str], column: str) -> str:
    normalized = {header.lower(): header for header in row}
    actual = normalized.get(column.lower())
    if not actual:
        return ""
    return str(row.get(actual, "") or "").strip()


@dataclass
class _OccurrenceDraft:
    key: CanonicalEntityKey
    display_name: str
    scalar_properties: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    aliases: list[dict] = field(default_factory=list)


@dataclass
class SyncStats:
    discovered: int = 0
    total: int = 0
    parsed: int = 0
    reused: int = 0
    failed: int = 0
    unresolved: int = 0


class EntityIndexCancelled(Exception):
    """Raised when entity index synchronization is interrupted."""


def _check_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise EntityIndexCancelled()


def _load_alias_index_from_db(
    connection: sqlite3.Connection,
    source_id: int,
    max_observed_at: datetime,
) -> AliasBindingIndex:
    index = AliasBindingIndex()
    rows = connection.execute(
        """
        SELECT kind, normalized_value, observed_at, immutable_id, source_family
        FROM alias_observations
        WHERE source_id=? AND observed_at <= ?
        ORDER BY observed_at
        """,
        (source_id, max_observed_at.isoformat(timespec="seconds")),
    ).fetchall()
    for row in rows:
        index.record(
            row["kind"],
            row["normalized_value"],
            datetime.fromisoformat(row["observed_at"]),
            row["immutable_id"],
            row["source_family"],
        )
    return index


def _build_occurrence_drafts(
    adapter: ReportFamilyAdapter,
    rows: list[dict[str, str]],
    observed_at: datetime,
    alias_index: AliasBindingIndex,
    record_bindings: bool,
) -> tuple[dict[str, _OccurrenceDraft], int]:
    drafts: dict[str, _OccurrenceDraft] = {}
    unresolved = 0
    for row in rows:
        if record_bindings:
            user_id = adapter.canonical_value(row)
            upn = _extract_upn(adapter, row)
            if user_id and upn:
                alias_index.record(
                    "upn",
                    upn.lower(),
                    observed_at,
                    user_id,
                    adapter.family,
                )
        key, unresolved_reason = classify_row_entity_key(
            adapter, row, observed_at, alias_index
        )
        if key is None:
            if unresolved_reason is not None:
                unresolved += 1
            continue
        label = key.label()
        draft = drafts.setdefault(
            label,
            _OccurrenceDraft(key=key, display_name=adapter.display_name(row)),
        )
        display_name = adapter.display_name(row)
        if display_name and display_name != "Unknown":
            draft.display_name = display_name
        for alias_column in adapter.alias_columns:
            value = _row_value(row, alias_column.column)
            if value:
                draft.aliases.append(
                    {
                        "kind": alias_column.kind,
                        "value": value,
                        "source_family": adapter.family,
                        "observed_at": observed_at.isoformat(timespec="seconds"),
                    }
                )
        if adapter.row_scope_columns:
            props = [
                {
                    "family": adapter.family,
                    "name": name,
                    "value": value,
                    "observed_at": observed_at.isoformat(timespec="seconds"),
                }
                for name, value in adapter.card_properties(row, observed_at)
            ]
            draft.relationships.append(
                {
                    "family": adapter.family,
                    "row_scope": adapter.row_scope(row),
                    "properties": props,
                }
            )
        else:
            for name, value in adapter.card_properties(row, observed_at):
                draft.scalar_properties.append(
                    {
                        "family": adapter.family,
                        "name": name,
                        "value": value,
                        "observed_at": observed_at.isoformat(timespec="seconds"),
                    }
                )
    return drafts, unresolved


def _ensure_source(connection: sqlite3.Connection, reports_dir: Path) -> int:
    key = source_key(reports_dir)
    path_text = str(normalize_reports_path(reports_dir))
    row = connection.execute(
        "SELECT id FROM report_sources WHERE source_key=?", (key,)
    ).fetchone()
    if row is not None:
        connection.execute(
            "UPDATE report_sources SET reports_path=? WHERE id=?",
            (path_text, row["id"]),
        )
        return int(row["id"])
    cursor = connection.execute(
        """
        INSERT INTO report_sources(source_key, reports_path, created_at)
        VALUES (?, ?, ?)
        """,
        (key, path_text, utc_now_iso()),
    )
    return int(cursor.lastrowid)


def _discover_files(reports_dir: Path) -> list[Path]:
    discovered: list[Path] = []
    with os.scandir(reports_dir) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".csv"):
                continue
            discovered.append(Path(entry.path))
    discovered.sort(key=lambda item: item.name.lower())
    return discovered


@dataclass(frozen=True)
class _DiscoveredFile:
    path: Path
    relative_path: str
    family: str
    raw_family: str
    captured_at: datetime
    size_bytes: int
    mtime_ns: int
    supported: bool


def _classify_file(reports_dir: Path, path: Path) -> _DiscoveredFile | None:
    try:
        stat = path.stat()
        captured_at = report_timestamp(path)
    except (OSError, ValueError):
        return None
    raw_family = report_family(path)
    canonical_family = canonical_entity_family(raw_family)
    return _DiscoveredFile(
        path=path,
        relative_path=relative_report_path(reports_dir, path),
        family=canonical_family,
        raw_family=raw_family,
        captured_at=captured_at,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        supported=canonical_family in ADAPTERS_BY_FAMILY,
    )


def _repair_alias_indexed_families(connection: sqlite3.Connection, source_id: int) -> None:
    for alias, canonical in ENTITY_FAMILY_ALIASES.items():
        connection.execute(
            """
            UPDATE indexed_files
            SET family=?, status='pending'
            WHERE source_id=? AND family=? AND status='indexed'
            """,
            (canonical, source_id, alias),
        )


def _get_or_create_file_row(
    connection: sqlite3.Connection,
    source_id: int,
    item: _DiscoveredFile,
    adapter_version: str,
) -> int:
    row = connection.execute(
        """
        SELECT id FROM indexed_files
        WHERE source_id=? AND relative_path=?
        """,
        (source_id, item.relative_path),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = connection.execute(
        """
        INSERT INTO indexed_files(
            source_id, relative_path, family, captured_at,
            size_bytes, mtime_ns, adapter_version, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            source_id,
            item.relative_path,
            item.family,
            item.captured_at.isoformat(timespec="seconds"),
            item.size_bytes,
            item.mtime_ns,
            adapter_version,
        ),
    )
    return int(cursor.lastrowid)


def _needs_reindex(
    connection: sqlite3.Connection,
    file_id: int,
    item: _DiscoveredFile,
    adapter_version: str,
) -> bool:
    row = connection.execute(
        """
        SELECT status, active_size_bytes, active_mtime_ns, adapter_version
        FROM indexed_files WHERE id=?
        """,
        (file_id,),
    ).fetchone()
    if row is None:
        return True
    if row["status"] != "indexed":
        return True
    if row["adapter_version"] != adapter_version:
        return True
    if row["active_size_bytes"] != item.size_bytes:
        return True
    if row["active_mtime_ns"] != item.mtime_ns:
        return True
    return False


def _ensure_entity_id(
    connection: sqlite3.Connection,
    source_id: int,
    key: CanonicalEntityKey,
    display_name: str,
) -> int:
    row = connection.execute(
        """
        SELECT id FROM entities
        WHERE source_id=? AND entity_type=? AND primary_id=?
        """,
        (source_id, key.entity_type, key.primary_id),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = connection.execute(
        """
        INSERT INTO entities(source_id, entity_type, primary_id, display_name)
        VALUES (?, ?, ?, ?)
        """,
        (source_id, key.entity_type, key.primary_id, display_name),
    )
    return int(cursor.lastrowid)


def _index_supported_file(
    connection: sqlite3.Connection,
    source_id: int,
    file_id: int,
    item: _DiscoveredFile,
    adapter_version: str,
    affected_entity_ids: set[int],
    stats: SyncStats,
) -> None:
    adapter = ADAPTERS_BY_FAMILY[item.family]
    assert adapter is not None
    record_bindings = item.family in _BINDING_FAMILIES
    headers, rows = read_csv_rows(item.path)
    if not adapter.headers_supported(headers):
        required = adapter.canonical_columns or tuple(
            alias.column for alias in adapter.alias_columns
        )
        raise ValueError(
            f"Missing required columns {required} for {adapter.family} in {item.relative_path}"
        )
    alias_index = _load_alias_index_from_db(connection, source_id, item.captured_at)
    drafts, unresolved = _build_occurrence_drafts(
        adapter,
        rows,
        item.captured_at,
        alias_index,
        record_bindings,
    )
    stats.unresolved += unresolved
    entity_ids: set[int] = set()
    with transaction(connection):
        connection.execute(
            "DELETE FROM alias_observations WHERE file_id=?", (file_id,)
        )
        connection.execute(
            "DELETE FROM entity_occurrences WHERE file_id=?", (file_id,)
        )
        for draft in drafts.values():
            entity_id = _ensure_entity_id(
                connection, source_id, draft.key, draft.display_name
            )
            entity_ids.add(entity_id)
            scalar_json = canonical_json(draft.scalar_properties)
            relationships_json = canonical_json(draft.relationships)
            aliases_json = canonical_json(draft.aliases)
            row_hash = payload_row_hash(
                {"scalar": draft.scalar_properties, "relationships": draft.relationships},
                draft.aliases,
            )
            connection.execute(
                """
                INSERT INTO entity_occurrences(
                    entity_id, file_id, observed_at, display_name,
                    scalar_properties_json, relationships_json, aliases_json, row_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    file_id,
                    item.captured_at.isoformat(timespec="seconds"),
                    draft.display_name,
                    scalar_json,
                    relationships_json,
                    aliases_json,
                    row_hash,
                ),
            )
        if record_bindings:
            for draft in drafts.values():
                user_id = draft.key.primary_id
                for alias in draft.aliases:
                    if alias["kind"] != "upn":
                        continue
                    kind = alias["kind"]
                    value = alias["value"]
                    connection.execute(
                            """
                            INSERT INTO alias_observations(
                                source_id, file_id, kind, normalized_value,
                                immutable_id, observed_at, source_family
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                source_id,
                                file_id,
                                kind,
                                value.lower(),
                                user_id,
                                item.captured_at.isoformat(timespec="seconds"),
                                adapter.family,
                            ),
                        )
        connection.execute(
            """
            UPDATE indexed_files
            SET status='indexed',
                adapter_version=?,
                size_bytes=?,
                mtime_ns=?,
                active_size_bytes=?,
                active_mtime_ns=?,
                candidate_size_bytes=NULL,
                candidate_mtime_ns=NULL,
                last_indexed_at=?,
                last_error=NULL
            WHERE id=?
            """,
            (
                adapter_version,
                item.size_bytes,
                item.mtime_ns,
                item.size_bytes,
                item.mtime_ns,
                utc_now_iso(),
                file_id,
            ),
        )
        connection.execute(
            """
            DELETE FROM unsupported_files
            WHERE source_id=? AND relative_path=?
            """,
            (source_id, item.relative_path),
        )
    affected_entity_ids.update(entity_ids)


def _record_file_failure(
    connection: sqlite3.Connection,
    run_id: int,
    file_id: int,
    item: _DiscoveredFile,
    message: str,
) -> None:
    with transaction(connection):
        connection.execute(
            """
            UPDATE indexed_files
            SET candidate_size_bytes=?,
                candidate_mtime_ns=?,
                last_error=?
            WHERE id=?
            """,
            (item.size_bytes, item.mtime_ns, message, file_id),
        )
        connection.execute(
            """
            INSERT INTO indexing_errors(run_id, file_id, relative_path, message, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, file_id, item.relative_path, message, utc_now_iso()),
        )


def _finalize_entity(
    connection: sqlite3.Connection,
    source_id: int,
    entity_id: int,
) -> None:
    row = connection.execute(
        """
        SELECT entity_type, primary_id FROM entities WHERE id=?
        """,
        (entity_id,),
    ).fetchone()
    if row is None:
        return
    bounds = connection.execute(
        """
        SELECT MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen
        FROM entity_occurrences WHERE entity_id=?
        """,
        (entity_id,),
    ).fetchone()
    if bounds is None or bounds["first_seen"] is None:
        connection.execute("DELETE FROM entities WHERE id=?", (entity_id,))
        connection.execute("DELETE FROM entity_aliases WHERE entity_id=?", (entity_id,))
        if metadata_value(connection, "fts5_available") == "1":
            connection.execute(
                "DELETE FROM entity_search_fts WHERE entity_id=?", (entity_id,)
            )
        return
    latest_occurrence = connection.execute(
        """
        SELECT display_name FROM entity_occurrences
        WHERE entity_id=? ORDER BY observed_at DESC LIMIT 1
        """,
        (entity_id,),
    ).fetchone()
    display_name = latest_occurrence["display_name"] if latest_occurrence else ""
    present = connection.execute(
        """
        SELECT 1
        FROM entity_occurrences eo
        JOIN family_latest_files flf
          ON flf.file_id = eo.file_id AND flf.source_id = ?
        WHERE eo.entity_id=?
        LIMIT 1
        """,
        (source_id, entity_id),
    ).fetchone()
    connection.execute(
        """
        UPDATE entities
        SET first_seen=?, last_seen=?, display_name=?, present_in_latest=?
        WHERE id=?
        """,
        (
            bounds["first_seen"],
            bounds["last_seen"],
            display_name,
            1 if present else 0,
            entity_id,
        ),
    )
    connection.execute("DELETE FROM entity_aliases WHERE entity_id=?", (entity_id,))
    alias_map: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    observations = connection.execute(
        """
        SELECT kind, normalized_value, observed_at, source_family
        FROM alias_observations
        WHERE source_id=? AND immutable_id=?
        """,
        (source_id, row["primary_id"]),
    ).fetchall()
    for observation in observations:
        key = (
            observation["kind"],
            observation["normalized_value"],
            observation["source_family"],
        )
        seen_at = observation["observed_at"]
        if key not in alias_map:
            alias_map[key] = (seen_at, seen_at, observation["normalized_value"])
        else:
            first, last, display = alias_map[key]
            alias_map[key] = (min(first, seen_at), max(last, seen_at), display)
    occurrence_rows = connection.execute(
        """
        SELECT aliases_json, observed_at
        FROM entity_occurrences WHERE entity_id=?
        """,
        (entity_id,),
    ).fetchall()
    for occurrence in occurrence_rows:
        for alias in json.loads(occurrence["aliases_json"] or "[]"):
            normalized = str(alias.get("value", "")).lower()
            if not normalized:
                continue
            key = (
                alias.get("kind", ""),
                normalized,
                alias.get("source_family", ""),
            )
            seen_at = alias.get("observed_at") or occurrence["observed_at"]
            if key not in alias_map:
                alias_map[key] = (seen_at, seen_at, str(alias.get("value", "")))
            else:
                first, last, display = alias_map[key]
                alias_map[key] = (min(first, seen_at), max(last, seen_at), display)
    for (kind, normalized, family), (first_seen, last_seen, display_value) in alias_map.items():
        connection.execute(
            """
            INSERT INTO entity_aliases(
                entity_id, kind, normalized_value, display_value,
                first_seen, last_seen, source_family
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, kind, normalized, display_value, first_seen, last_seen, family),
        )
    if metadata_value(connection, "fts5_available") == "1":
        alias_values = " ".join(sorted({normalized for (_, normalized, _) in alias_map}))
        connection.execute("DELETE FROM entity_search_fts WHERE entity_id=?", (entity_id,))
        connection.execute(
            """
            INSERT INTO entity_search_fts(
                entity_id, entity_type, primary_id, display_name, alias_values
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                row["entity_type"],
                row["primary_id"],
                display_name,
                alias_values,
            ),
        )


def _update_family_latest_files(
    connection: sqlite3.Connection,
    source_id: int,
    families: set[str],
) -> None:
    for family in families:
        latest = connection.execute(
            """
            SELECT id, captured_at
            FROM indexed_files
            WHERE source_id=? AND family=? AND status='indexed'
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (source_id, family),
        ).fetchone()
        if latest is None:
            connection.execute(
                "DELETE FROM family_latest_files WHERE source_id=? AND family=?",
                (source_id, family),
            )
            continue
        connection.execute(
            """
            INSERT INTO family_latest_files(source_id, family, file_id, captured_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, family) DO UPDATE SET
                file_id=excluded.file_id,
                captured_at=excluded.captured_at
            """,
            (source_id, family, latest["id"], latest["captured_at"]),
        )


def run_sync(
    reports_dir: Path,
    *,
    db_path: Path | None = None,
    generation: int = 0,
    cold: bool = False,
    progress: Callable[[SyncProgressEvent], None] | None = None,
    cancelled: threading.Event | None = None,
) -> SyncCompleteEvent:
    started = time.perf_counter()
    adapter_version = compute_adapter_version()
    reports_dir = normalize_reports_path(reports_dir)
    destination = db_path or entity_index_path(reports_dir)
    working_path = destination.with_suffix(".sqlite3.tmp") if cold else destination
    source_key_value = source_key(reports_dir)
    stats = SyncStats()

    def emit(phase: SyncProgressEvent.phase, label: str = "") -> None:
        if progress is None:
            return
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        eta_ms = None
        if stats.parsed > 0 and stats.total > stats.reused + stats.parsed:
            remaining = stats.total - stats.reused - stats.parsed
            eta_ms = int(elapsed_ms / stats.parsed * remaining)
        progress(
            SyncProgressEvent(
                phase=phase,
                generation=generation,
                discovered=stats.discovered,
                total=stats.total,
                parsed=stats.parsed,
                reused=stats.reused,
                failed=stats.failed,
                unresolved=stats.unresolved,
                elapsed_ms=elapsed_ms,
                eta_ms=eta_ms,
                label=label,
            )
        )

    try:
        with acquire_entity_index_lock(destination, source_key_value, cold=cold):
            if cold:
                cleanup_index_files(working_path)
                cleanup_index_files(destination)

            connection: sqlite3.Connection | None = open_connection(
                working_path,
                readonly=False,
                adapter_version=adapter_version,
                journal_mode="DELETE" if cold else "wal",
            )
            affected_entity_ids: set[int] = set()
            affected_families: set[str] = set()
            run_id: int | None = None
            indexed_successfully = 0

            try:
                source_id = _ensure_source(connection, reports_dir)
                _repair_alias_indexed_families(connection, source_id)

                if projections_need_repair(connection):
                    emit("repairing_projections", "Repairing entity search index…")
                    repair_search_projections(connection, source_id)

                stored_adapter_version = metadata_value(connection, "adapter_version")
                if stored_adapter_version and stored_adapter_version != adapter_version:
                    connection.execute(
                        """
                        UPDATE indexed_files
                        SET status='pending'
                        WHERE source_id=? AND status='indexed'
                        """,
                        (source_id,),
                    )
                    connection.execute(
                        "UPDATE metadata SET value=? WHERE key='adapter_version'",
                        (adapter_version,),
                    )
                    connection.execute(
                        """
                        DELETE FROM unsupported_files
                        WHERE source_id=? AND adapter_version_at_discovery != ?
                        """,
                        (source_id, adapter_version),
                    )

                cursor = connection.execute(
                    """
                    INSERT INTO indexing_runs(
                        source_id, generation, status, started_at
                    ) VALUES (?, ?, 'running', ?)
                    """,
                    (source_id, generation, utc_now_iso()),
                )
                run_id = int(cursor.lastrowid)

                emit("discovering", "Discovering CSV snapshots")
                discovered_paths = _discover_files(reports_dir)
                classified: list[_DiscoveredFile] = []
                for path in discovered_paths:
                    _check_cancelled(cancelled)
                    item = _classify_file(reports_dir, path)
                    if item is not None:
                        classified.append(item)
                stats.discovered = len(classified)
                stats.total = len(classified)

                emit("checking", "Checking indexed files")
                to_index: list[tuple[int, _DiscoveredFile]] = []
                seen_relative: set[str] = set()
                for item in classified:
                    _check_cancelled(cancelled)
                    seen_relative.add(item.relative_path)
                    if not item.supported:
                        connection.execute(
                            """
                            INSERT INTO unsupported_files(
                                source_id, relative_path, family, captured_at,
                                size_bytes, mtime_ns, adapter_version_at_discovery
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(source_id, relative_path) DO UPDATE SET
                                family=excluded.family,
                                captured_at=excluded.captured_at,
                                size_bytes=excluded.size_bytes,
                                mtime_ns=excluded.mtime_ns,
                                adapter_version_at_discovery=excluded.adapter_version_at_discovery
                            """,
                            (
                                source_id,
                                item.relative_path,
                                item.family,
                                item.captured_at.isoformat(timespec="seconds"),
                                item.size_bytes,
                                item.mtime_ns,
                                adapter_version,
                            ),
                        )
                        continue
                    file_id = _get_or_create_file_row(
                        connection, source_id, item, adapter_version
                    )
                    if _needs_reindex(connection, file_id, item, adapter_version):
                        to_index.append((file_id, item))
                    else:
                        stats.reused += 1

                binding_batch = [
                    pair for pair in to_index if pair[1].family in _BINDING_FAMILIES
                ]
                other_batch = [
                    pair for pair in to_index if pair[1].family not in _BINDING_FAMILIES
                ]
                ordered = binding_batch + other_batch

                emit("indexing", "Indexing changed files")
                for file_id, item in ordered:
                    _check_cancelled(cancelled)
                    try:
                        previous_entities = {
                            int(row["entity_id"])
                            for row in connection.execute(
                                "SELECT entity_id FROM entity_occurrences WHERE file_id=?",
                                (file_id,),
                            ).fetchall()
                        }
                        _index_supported_file(
                            connection,
                            source_id,
                            file_id,
                            item,
                            adapter_version,
                            affected_entity_ids,
                            stats,
                        )
                        stats.parsed += 1
                        indexed_successfully += 1
                        affected_families.add(item.family)
                        affected_entity_ids.update(previous_entities)
                    except Exception as exc:
                        stats.failed += 1
                        logger.exception("Failed to index %s", item.relative_path)
                        _record_file_failure(connection, run_id, file_id, item, str(exc))
                    emit("indexing", item.path.name)

                emit("resolving_identities", "Resolving dependent identities")
                deleted_rows = connection.execute(
                    """
                    SELECT id, family, relative_path
                    FROM indexed_files
                    WHERE source_id=? AND status='indexed'
                    """,
                    (source_id,),
                ).fetchall()
                for row in deleted_rows:
                    if row["relative_path"] not in seen_relative:
                        entity_ids = {
                            int(item["entity_id"])
                            for item in connection.execute(
                                "SELECT entity_id FROM entity_occurrences WHERE file_id=?",
                                (row["id"],),
                            ).fetchall()
                        }
                        with transaction(connection):
                            connection.execute(
                                "DELETE FROM alias_observations WHERE file_id=?",
                                (row["id"],),
                            )
                            connection.execute(
                                "DELETE FROM entity_occurrences WHERE file_id=?",
                                (row["id"],),
                            )
                            connection.execute(
                                """
                                UPDATE indexed_files SET status='deleted' WHERE id=?
                                """,
                                (row["id"],),
                            )
                        affected_entity_ids.update(entity_ids)
                        affected_families.add(row["family"])

                _update_family_latest_files(connection, source_id, affected_families)
                emit("recomputing_entities", "Recomputing affected entities")
                for entity_id in affected_entity_ids:
                    _finalize_entity(connection, source_id, entity_id)
                connection.commit()

                status = "completed_with_errors" if stats.failed else "complete"
                connection.execute(
                    """
                    UPDATE indexing_runs
                    SET status=?, completed_at=?, files_discovered=?, files_parsed=?,
                        files_reused=?, files_failed=?
                    WHERE id=?
                    """,
                    (
                        status,
                        utc_now_iso(),
                        stats.discovered,
                        stats.parsed,
                        stats.reused,
                        stats.failed,
                        run_id,
                    ),
                )
                connection.execute(
                    "UPDATE report_sources SET last_sync_at=? WHERE id=?",
                    (utc_now_iso(), source_id),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES('published_at', ?)",
                    (utc_now_iso(),),
                )
                connection.commit()

                emit("checkpointing", "Checkpointing database")
                connection.close()
                connection = None

                if cold:
                    if indexed_successfully > 0 or stats.reused > 0:
                        emit("publishing", "Publishing entity index")
                        if working_path != destination:
                            if destination.exists():
                                destination.unlink()
                            working_path.replace(destination)
                        cleanup_index_files(working_path)
                        published = open_connection(destination, journal_mode="wal")
                        published.execute("PRAGMA journal_mode=WAL")
                        published.close()
                    else:
                        cleanup_index_files(working_path)
                        raise RuntimeError("Cold build produced no indexed files")

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                final_status = "completed_with_errors" if stats.failed else "complete"
                emit(final_status, "Entity index complete")
                return SyncCompleteEvent(
                    generation=generation,
                    status=final_status,
                    discovered=stats.discovered,
                    parsed=stats.parsed,
                    reused=stats.reused,
                    failed=stats.failed,
                    unresolved=stats.unresolved,
                    elapsed_ms=elapsed_ms,
                )
            except EntityIndexCancelled:
                if run_id is not None and connection is not None:
                    connection.execute(
                        """
                        UPDATE indexing_runs
                        SET status='interrupted', completed_at=?
                        WHERE id=?
                        """,
                        (utc_now_iso(), run_id),
                    )
                    connection.commit()
                raise
            except Exception as exc:
                if run_id is not None and connection is not None:
                    connection.execute(
                        """
                        UPDATE indexing_runs
                        SET status='failed', completed_at=?
                        WHERE id=?
                        """,
                        (utc_now_iso(), run_id),
                    )
                    connection.commit()
                emit("failed", str(exc))
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return SyncCompleteEvent(
                    generation=generation,
                    status="failed",
                    discovered=stats.discovered,
                    parsed=stats.parsed,
                    reused=stats.reused,
                    failed=stats.failed,
                    unresolved=stats.unresolved,
                    elapsed_ms=elapsed_ms,
                    message=str(exc),
                )
            finally:
                if connection is not None:
                    connection.close()
    except EntityIndexLockError as exc:
        emit("failed", str(exc))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return SyncCompleteEvent(
            generation=generation,
            status="failed",
            elapsed_ms=elapsed_ms,
            message=str(exc),
        )
    except EntityIndexCancelled:
        emit("failed", "Entity index synchronization interrupted")
        raise
