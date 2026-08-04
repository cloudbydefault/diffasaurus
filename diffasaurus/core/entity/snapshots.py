from __future__ import annotations

from datetime import datetime

from diffasaurus.core.entity.types import EntityIndexStats
from diffasaurus.core.report_history import ReportSnapshot, read_csv_rows, snapshot_with_headers


_PARSE_CACHE: dict[tuple[str, int, int], tuple[tuple[str, ...], list[dict[str, str]]]] = {}


def _cache_key(snapshot: ReportSnapshot) -> tuple[str, int, int] | None:
    try:
        stat = snapshot.path.stat()
    except OSError:
        return None
    return (str(snapshot.path.resolve()), stat.st_size, stat.st_mtime_ns)


def load_snapshot_rows(
    snapshot: ReportSnapshot,
    stats: EntityIndexStats | None = None,
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    key = _cache_key(snapshot)
    if key is not None and key in _PARSE_CACHE:
        if stats is not None:
            stats.csv_cache_hits += 1
        return _PARSE_CACHE[key]

    hydrated = snapshot_with_headers(snapshot)
    headers, rows = read_csv_rows(hydrated.path)
    result = (tuple(headers), rows)
    if key is not None:
        _PARSE_CACHE[key] = result
    if stats is not None:
        stats.csv_parsed += 1
    return result


def clear_parse_cache() -> None:
    _PARSE_CACHE.clear()


def snapshot_at_or_before(
    snapshots: list[ReportSnapshot],
    target: datetime,
) -> ReportSnapshot | None:
    candidates = [snapshot for snapshot in snapshots if snapshot.captured_at <= target]
    if not candidates:
        return None
    return candidates[-1]
