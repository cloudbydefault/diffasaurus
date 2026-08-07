from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    reports_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_sync_at TEXT
);

CREATE TABLE IF NOT EXISTS indexed_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES report_sources(id),
    relative_path TEXT NOT NULL,
    family TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    adapter_version TEXT NOT NULL DEFAULT '',
    content_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    active_size_bytes INTEGER,
    active_mtime_ns INTEGER,
    candidate_size_bytes INTEGER,
    candidate_mtime_ns INTEGER,
    last_indexed_at TEXT,
    last_error TEXT,
    UNIQUE(source_id, relative_path)
);

CREATE TABLE IF NOT EXISTS unsupported_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES report_sources(id),
    relative_path TEXT NOT NULL,
    family TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    adapter_version_at_discovery TEXT NOT NULL DEFAULT '',
    UNIQUE(source_id, relative_path)
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES report_sources(id),
    entity_type TEXT NOT NULL,
    primary_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    first_seen TEXT,
    last_seen TEXT,
    present_in_latest INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, entity_type, primary_id)
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    display_value TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source_family TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alias_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES report_sources(id),
    file_id INTEGER NOT NULL REFERENCES indexed_files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    immutable_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_family TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES indexed_files(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    scalar_properties_json TEXT NOT NULL DEFAULT '[]',
    relationships_json TEXT NOT NULL DEFAULT '[]',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    row_hash TEXT NOT NULL DEFAULT '',
    UNIQUE(entity_id, file_id)
);

CREATE TABLE IF NOT EXISTS family_latest_files (
    source_id INTEGER NOT NULL REFERENCES report_sources(id),
    family TEXT NOT NULL,
    file_id INTEGER NOT NULL REFERENCES indexed_files(id),
    captured_at TEXT NOT NULL,
    PRIMARY KEY (source_id, family)
);

CREATE TABLE IF NOT EXISTS indexing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES report_sources(id),
    generation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    files_discovered INTEGER NOT NULL DEFAULT 0,
    files_parsed INTEGER NOT NULL DEFAULT 0,
    files_reused INTEGER NOT NULL DEFAULT 0,
    files_failed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS indexing_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES indexing_runs(id),
    file_id INTEGER REFERENCES indexed_files(id),
    relative_path TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_indexed_files_source_status
    ON indexed_files(source_id, status);
CREATE INDEX IF NOT EXISTS idx_indexed_files_source_family_captured
    ON indexed_files(source_id, family, captured_at);
CREATE INDEX IF NOT EXISTS idx_entities_source_type_id
    ON entities(source_id, entity_type, primary_id);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_lookup
    ON entity_aliases(normalized_value, kind);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity
    ON entity_aliases(entity_id);
CREATE INDEX IF NOT EXISTS idx_alias_obs_lookup
    ON alias_observations(kind, normalized_value, observed_at);
CREATE INDEX IF NOT EXISTS idx_occurrences_entity_observed
    ON entity_occurrences(entity_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_occurrences_file
    ON entity_occurrences(file_id);
"""

_PERFORMANCE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_alias_obs_source_observed
    ON alias_observations(source_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_alias_obs_source_immutable_observed
    ON alias_observations(source_id, immutable_id, observed_at);
"""


def ensure_performance_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(_PERFORMANCE_INDEX_DDL)

_USER_DEVICE_LINK_DDL = """
CREATE TABLE IF NOT EXISTS user_device_link_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES report_sources(id),
    file_id INTEGER NOT NULL REFERENCES indexed_files(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    device_entity_id INTEGER NOT NULL REFERENCES entities(id),
    device_dedup_key TEXT NOT NULL,
    link_kind TEXT NOT NULL,
    normalized_link_value TEXT NOT NULL DEFAULT '',
    resolution_status TEXT NOT NULL,
    resolved_user_immutable_id TEXT,
    candidate_user_ids_json TEXT NOT NULL DEFAULT '[]',
    diagnostic TEXT NOT NULL DEFAULT '',
    raw_link_data_json TEXT NOT NULL DEFAULT '',
    UNIQUE(file_id, device_dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_udlo_resolved_user
    ON user_device_link_observations(
        source_id, file_id, resolved_user_immutable_id
    )
    WHERE resolution_status = 'resolved';

CREATE INDEX IF NOT EXISTS idx_udlo_file_status
    ON user_device_link_observations(source_id, file_id, resolution_status);

CREATE INDEX IF NOT EXISTS idx_udlo_file
    ON user_device_link_observations(file_id);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def probe_fts5(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(content)"
        )
        connection.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _create_fts5(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS entity_search_fts USING fts5(
            entity_id UNINDEXED,
            entity_type UNINDEXED,
            primary_id,
            display_name,
            alias_values,
            tokenize='unicode61'
        )
        """
    )


def ensure_user_device_link_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_USER_DEVICE_LINK_DDL)


def initialize_schema(connection: sqlite3.Connection, adapter_version: str) -> bool:
    connection.executescript(_DDL)
    ensure_performance_indexes(connection)
    ensure_user_device_link_schema(connection)
    fts5_available = probe_fts5(connection)
    if fts5_available:
        _create_fts5(connection)
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('adapter_version', ?)",
        (adapter_version,),
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('fts5_available', ?)",
        ("1" if fts5_available else "0",),
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('created_at', ?)",
        (utc_now_iso(),),
    )
    return fts5_available


def migrate_schema_if_needed(connection: sqlite3.Connection, adapter_version: str) -> None:
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        initialize_schema(connection, adapter_version)
        connection.commit()
        return
    stored_version = int(row["value"] if isinstance(row, sqlite3.Row) else row[0])
    if stored_version == SCHEMA_VERSION:
        ensure_performance_indexes(connection)
        ensure_user_device_link_schema(connection)
        return
    if stored_version == 1 and SCHEMA_VERSION == 2:
        ensure_performance_indexes(connection)
        ensure_user_device_link_schema(connection)
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
        return
    raise RuntimeError(f"Unsupported entity index schema version {stored_version}")


def open_connection(
    db_path: Path,
    *,
    readonly: bool = False,
    adapter_version: str | None = None,
    journal_mode: str = "wal",
    cold_build: bool = False,
) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and db_path.is_file():
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15)
    else:
        connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        if cold_build:
            connection.execute("PRAGMA journal_mode=MEMORY")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
        else:
            connection.execute(f"PRAGMA journal_mode={journal_mode}")
            connection.execute("PRAGMA synchronous=NORMAL")
        if adapter_version is not None:
            migrate_schema_if_needed(connection, adapter_version)
    return connection


def metadata_value(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key=?", (key,)
    ).fetchone()
    return None if row is None else str(row["value"])


def fts5_enabled(connection: sqlite3.Connection) -> bool:
    return metadata_value(connection, "fts5_available") == "1"


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
