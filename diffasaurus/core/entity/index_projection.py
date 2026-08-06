from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

from diffasaurus.core.entity.index_paths import (
    entity_index_path,
    normalize_reports_path,
    source_key,
)
from diffasaurus.core.entity.index_progress import SyncProgressEvent
from diffasaurus.core.entity.index_schema import (
    metadata_value,
    open_connection,
    transaction,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

# Version 1: initial derived projections (file_id-scoped alias_observations join).
# Version 2: immutable_id-scoped alias_observations join.
ALIAS_PROJECTION_VERSION = 2
SEARCH_PROJECTION_VERSION = 2


@dataclass(frozen=True)
class ProjectionRepairStats:
    entities_processed: int
    aliases_before: int
    aliases_after: int
    fts_rows_rebuilt: int
    duration_ms: int
    alias_projection_version: int
    search_projection_version: int


def alias_projection_version(connection: sqlite3.Connection) -> int:
    value = metadata_value(connection, "alias_projection_version")
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def search_projection_version(connection: sqlite3.Connection) -> int:
    value = metadata_value(connection, "search_projection_version")
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def projections_need_repair(connection: sqlite3.Connection) -> bool:
    return (
        alias_projection_version(connection) < ALIAS_PROJECTION_VERSION
        or search_projection_version(connection) < SEARCH_PROJECTION_VERSION
    )


def _count_aliases(connection: sqlite3.Connection, source_id: int) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM entity_aliases ea
        JOIN entities e ON e.id = ea.entity_id
        WHERE e.source_id=?
        """,
        (source_id,),
    ).fetchone()
    return int(row["count"]) if row is not None else 0


def _count_fts_rows(connection: sqlite3.Connection, source_id: int) -> int:
    if metadata_value(connection, "fts5_available") != "1":
        return 0
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM entity_search_fts fts
        JOIN entities e ON e.id = fts.entity_id
        WHERE e.source_id=?
        """,
        (source_id,),
    ).fetchone()
    return int(row["count"]) if row is not None else 0


def repair_search_projections(
    connection: sqlite3.Connection,
    source_id: int,
) -> ProjectionRepairStats:
    """Rebuild entity_aliases and FTS from indexed source tables.

    Must run inside an open write connection. The caller should hold the entity-index
    lock. All changes are committed in a single transaction.
    """
    from diffasaurus.core.entity.index_sync import _finalize_entity

    started = time.perf_counter()
    aliases_before = _count_aliases(connection, source_id)
    fts_enabled = metadata_value(connection, "fts5_available") == "1"

    entity_ids = [
        int(row["id"])
        for row in connection.execute(
            "SELECT id FROM entities WHERE source_id=? ORDER BY id",
            (source_id,),
        )
    ]

    with transaction(connection):
        fts_rows_rebuilt = 0
        for entity_id in entity_ids:
            _finalize_entity(connection, source_id, entity_id)
            if fts_enabled:
                fts_rows_rebuilt += 1

        aliases_after = _count_aliases(connection, source_id)
        repaired_at = utc_now_iso()
        connection.execute(
            """
            INSERT OR REPLACE INTO metadata(key, value)
            VALUES('alias_projection_version', ?)
            """,
            (str(ALIAS_PROJECTION_VERSION),),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO metadata(key, value)
            VALUES('search_projection_version', ?)
            """,
            (str(SEARCH_PROJECTION_VERSION),),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO metadata(key, value)
            VALUES('projection_repaired_at', ?)
            """,
            (repaired_at,),
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    stats = ProjectionRepairStats(
        entities_processed=len(entity_ids),
        aliases_before=aliases_before,
        aliases_after=aliases_after,
        fts_rows_rebuilt=fts_rows_rebuilt if fts_enabled else 0,
        duration_ms=duration_ms,
        alias_projection_version=ALIAS_PROJECTION_VERSION,
        search_projection_version=SEARCH_PROJECTION_VERSION,
    )
    logger.info(
        "Entity search projection repair complete: entities=%d aliases_before=%d "
        "aliases_after=%d fts_rows_rebuilt=%d duration_ms=%d "
        "alias_projection_version=%d search_projection_version=%d",
        stats.entities_processed,
        stats.aliases_before,
        stats.aliases_after,
        stats.fts_rows_rebuilt,
        stats.duration_ms,
        stats.alias_projection_version,
        stats.search_projection_version,
    )
    return stats


def ensure_search_projections(
    reports_dir,
    *,
    db_path=None,
    generation: int = 0,
    progress: Callable[[SyncProgressEvent], None] | None = None,
) -> ProjectionRepairStats | None:
    """Repair derived search projections on an existing database if needed."""
    from pathlib import Path

    from diffasaurus.core.entity.index_lock import acquire_entity_index_lock

    normalized = normalize_reports_path(reports_dir)
    destination = Path(db_path) if db_path is not None else entity_index_path(normalized)
    if not destination.is_file():
        return None

    with acquire_entity_index_lock(destination, source_key(normalized), cold=False):
        connection = open_connection(destination, readonly=False)
        try:
            row = connection.execute(
                "SELECT id FROM report_sources ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            source_id = int(row["id"])
            if not projections_need_repair(connection):
                return None
            if progress is not None:
                progress(
                    SyncProgressEvent(
                        phase="repairing_projections",
                        generation=generation,
                        label="Repairing entity search index…",
                    )
                )
            return repair_search_projections(connection, source_id)
        finally:
            connection.close()


# Version 1: ambiguity-aware user↔managed-device link observations.
USER_DEVICE_LINK_PROJECTION_VERSION = 1


@dataclass(frozen=True)
class UserDeviceLinkProjectionStats:
    files_processed: int
    observations_written: int
    duration_ms: int
    projection_version: int


def user_device_link_projection_version(connection: sqlite3.Connection) -> int:
    value = metadata_value(connection, "user_device_link_projection_version")
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def user_device_links_need_build(connection: sqlite3.Connection) -> bool:
    return user_device_link_projection_version(connection) < USER_DEVICE_LINK_PROJECTION_VERSION


def user_device_links_need_build_at_path(db_path) -> bool:
    """Readonly check used by open_existing to decide whether to queue a worker sync."""
    from pathlib import Path

    path = Path(db_path)
    if not path.is_file():
        return False
    connection = open_connection(path, readonly=True)
    try:
        try:
            return user_device_links_need_build(connection)
        except sqlite3.OperationalError:
            return True
    finally:
        connection.close()


def replace_file_user_device_link_observations(
    connection: sqlite3.Connection,
    observations: list,
) -> None:
    """Replace projection rows for the files represented in observations."""
    import json

    if not observations:
        return
    file_ids = {int(item.file_id) for item in observations}
    for file_id in file_ids:
        connection.execute(
            "DELETE FROM user_device_link_observations WHERE file_id=?",
            (file_id,),
        )
    for item in observations:
        connection.execute(
            """
            INSERT INTO user_device_link_observations(
                source_id, file_id, observed_at, device_entity_id, device_dedup_key,
                link_kind, normalized_link_value, resolution_status,
                resolved_user_immutable_id, candidate_user_ids_json, diagnostic,
                raw_link_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.source_id,
                item.file_id,
                item.observed_at.isoformat(timespec="seconds"),
                item.device_entity_id,
                item.device_dedup_key,
                item.link_kind,
                item.normalized_link_value,
                item.resolution_status,
                item.resolved_user_immutable_id,
                json.dumps(sorted(item.candidate_user_ids), separators=(",", ":")),
                item.diagnostic,
                item.raw_link_data_json,
            ),
        )


def build_user_device_link_projection(
    connection: sqlite3.Connection,
    source_id: int,
    reports_dir,
    *,
    progress: Callable[[SyncProgressEvent], None] | None = None,
    generation: int = 0,
) -> UserDeviceLinkProjectionStats:
    """Rebuild user_device_link_observations from indexed managed-device CSVs.

    Must run under the entity-index lock. On failure the caller must not bump the
    projection version; this function commits only after a full successful rebuild.
    """
    from pathlib import Path

    from diffasaurus.core.entity.index_schema import ensure_user_device_link_schema
    from diffasaurus.core.entity.index_sync import (
        _ensure_entity_id,
        _load_alias_index_from_db,
    )
    from diffasaurus.core.entity.pit_enrichment import MANAGED_DEVICES_FAMILY
    from diffasaurus.core.entity.user_device_links import (
        build_observation_records,
        group_managed_device_rows,
    )
    from diffasaurus.core.report_history import read_csv_rows

    ensure_user_device_link_schema(connection)
    started = time.perf_counter()
    reports_root = Path(reports_dir)
    if progress is not None:
        progress(
            SyncProgressEvent(
                phase="building_user_device_links",
                generation=generation,
                label="Building historical user-device links…",
            )
        )

    files = connection.execute(
        """
        SELECT id, relative_path, captured_at
        FROM indexed_files
        WHERE source_id=? AND family=? AND status='indexed'
        ORDER BY captured_at
        """,
        (source_id, MANAGED_DEVICES_FAMILY),
    ).fetchall()

    all_observations = []
    for file_row in files:
        file_id = int(file_row["id"])
        captured_at = datetime_from_iso(file_row["captured_at"])
        path = reports_root / file_row["relative_path"]
        if not path.is_file():
            continue
        _, rows = read_csv_rows(path)
        alias_index = _load_alias_index_from_db(connection, source_id, captured_at)
        grouped = group_managed_device_rows(rows, captured_at, alias_index)

        def _entity_id_for_key(key, _source_id=source_id):
            return _ensure_entity_id(connection, _source_id, key, key.primary_id)

        observations = build_observation_records(
            source_id=source_id,
            file_id=file_id,
            observed_at=captured_at,
            grouped=grouped,
            device_entity_id_for_key=_entity_id_for_key,
        )
        all_observations.extend(observations)

    with transaction(connection):
        connection.execute(
            "DELETE FROM user_device_link_observations WHERE source_id=?",
            (source_id,),
        )
        if all_observations:
            replace_file_user_device_link_observations(connection, all_observations)
        repaired_at = utc_now_iso()
        connection.execute(
            """
            INSERT OR REPLACE INTO metadata(key, value)
            VALUES('user_device_link_projection_version', ?)
            """,
            (str(USER_DEVICE_LINK_PROJECTION_VERSION),),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO metadata(key, value)
            VALUES('user_device_link_projection_repaired_at', ?)
            """,
            (repaired_at,),
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    stats = UserDeviceLinkProjectionStats(
        files_processed=len(files),
        observations_written=len(all_observations),
        duration_ms=duration_ms,
        projection_version=USER_DEVICE_LINK_PROJECTION_VERSION,
    )
    logger.info(
        "User-device link projection complete: files=%d observations=%d duration_ms=%d "
        "projection_version=%d",
        stats.files_processed,
        stats.observations_written,
        stats.duration_ms,
        stats.projection_version,
    )
    return stats


def datetime_from_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
