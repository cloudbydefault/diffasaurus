from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.types import CanonicalEntityKey
from tests.fixtures.entity_index_generator import generate_user_properties_history, write_report


def _peak_rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = usage.ru_maxrss
    if sys.platform == "darwin":
        return int(rss)
    return int(rss * 1024)


def _run_benchmark(
    *,
    files: int,
    rows_per_file: int,
    families: int,
    output: Path | None,
) -> dict:
    del families  # reserved for future multi-family generation
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db_path = root / "benchmark.sqlite3"
        os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
        generate_user_properties_history(
            root,
            file_count=files,
            rows_per_file=rows_per_file,
        )

        started = time.perf_counter()
        cold = run_sync(root, cold=True, db_path=db_path)
        results["cold_build_seconds"] = time.perf_counter() - started
        results["cold_parsed"] = cold.parsed
        results["cold_failed"] = cold.failed

        started = time.perf_counter()
        repo = EntityIndexRepository.open(root, db_path=db_path)
        results["warm_open_seconds"] = time.perf_counter() - started
        assert repo is not None

        started = time.perf_counter()
        _ = repo.search("user1@example.com", "user")
        results["search_seconds"] = time.perf_counter() - started

        started = time.perf_counter()
        target = datetime(2026, 6, 15, 12, 0, 0)
        _ = repo.reconstruct_state(CanonicalEntityKey("user", "user-1"), target)
        results["pit_seconds"] = time.perf_counter() - started
        repo.close()

        started = time.perf_counter()
        unchanged = run_sync(root, cold=False, db_path=db_path)
        results["unchanged_sync_seconds"] = time.perf_counter() - started
        results["unchanged_parsed"] = unchanged.parsed
        results["unchanged_reused"] = unchanged.reused

        extra = root / f"Entra_Users_Properties_{(datetime(2026, 12, 1)+timedelta(days=files)):%Y%m%d-%H%M%S}.csv"
        write_report(
            extra,
            [{"Id": "user-new", "UPN": "new@example.com", "DisplayName": "New User"}],
        )
        started = time.perf_counter()
        incremental = run_sync(root, cold=False, db_path=db_path)
        results["incremental_sync_seconds"] = time.perf_counter() - started
        results["incremental_parsed"] = incremental.parsed

        elapsed = max(results["cold_build_seconds"], 0.001)
        total_rows = files * rows_per_file
        results["files_per_second"] = files / elapsed
        results["rows_per_second"] = total_rows / elapsed
        results["sqlite_size_bytes"] = db_path.stat().st_size if db_path.is_file() else 0
        results["peak_rss_bytes"] = _peak_rss_bytes()
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)

    results["acceptance"] = {
        "warm_open_under_1s": results["warm_open_seconds"] <= 1.0,
        "search_under_150ms": results["search_seconds"] <= 0.15,
        "pit_under_500ms": results["pit_seconds"] <= 0.5,
        "unchanged_sync_under_10s": results["unchanged_sync_seconds"] <= 10.0,
        "unchanged_parsed_zero": results["unchanged_parsed"] == 0,
    }
    if output is not None:
        output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the persistent entity index.")
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--rows-per-file", type=int, default=100)
    parser.add_argument("--families", type=int, default=10)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    results = _run_benchmark(
        files=args.files,
        rows_per_file=args.rows_per_file,
        families=args.families,
        output=args.output,
    )
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
