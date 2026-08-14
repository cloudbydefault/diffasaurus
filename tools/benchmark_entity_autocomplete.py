#!/usr/bin/env python3
"""Development benchmark for entity autocomplete_prefix() against the local index."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository, _escape_like, _normalize_query
from diffasaurus.core.settings import get_active_reports_dir


def _timed(callable_, repeats: int = 3) -> tuple[float, object]:
    elapsed = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = callable_()
        elapsed.append((time.perf_counter() - start) * 1000)
    return min(elapsed), result


def _explain(connection: sqlite3.Connection, sql: str, params: tuple) -> str:
    rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)


def main() -> None:
    reports_dir = get_active_reports_dir()
    db_path = entity_index_path(reports_dir)
    if not db_path.exists():
        print(f"No entity index at {db_path}")
        return

    repo = EntityIndexRepository.open(reports_dir)
    assert repo is not None
    source_id = repo._source_id  # benchmark helper only

    cases: list[tuple[str, str]] = [
        ("user", "j"),
        ("user", "jo"),
        ("user", "jon"),
        ("user", "jonathan"),
        ("device", "surf"),
        ("device", "sn-"),
        ("shared_mailbox", "fin"),
        ("shared_mailbox", "care"),
    ]

    print(f"Database: {db_path} ({db_path.stat().st_size / (1024**3):.2f} GiB)")
    print()
    print(f"{'type':<16} {'prefix':<12} {'cold_ms':>10} {'warm_ms':>10} {'count':>8}")
    for entity_type, prefix in cases:
        cold_ms, suggestions = _timed(lambda: repo.autocomplete_prefix(prefix, entity_type))
        warm_ms, _ = _timed(lambda: repo.autocomplete_prefix(prefix, entity_type))
        print(f"{entity_type:<16} {prefix:<12} {cold_ms:10.2f} {warm_ms:10.2f} {len(suggestions):8}")

    print()
    print("EXPLAIN QUERY PLAN (entity_aliases autocomplete):")
    needle = _normalize_query("jon")
    pattern = _escape_like(needle) + "%"
    sql = """
        SELECT DISTINCT ea.display_value
        FROM entity_aliases ea
        JOIN entities e ON e.id = ea.entity_id
        WHERE e.source_id=? AND e.entity_type=? AND ea.normalized_value LIKE ? ESCAPE '\\'
        LIMIT ?
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        print(_explain(connection, sql, (source_id, "user", pattern, 50)))

    repo.close()


if __name__ == "__main__":
    main()
