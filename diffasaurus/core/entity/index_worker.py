from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_path, normalize_reports_path, source_key
from diffasaurus.core.entity.index_progress import SyncCompleteEvent, SyncProgressEvent
from diffasaurus.core.entity.index_sync import EntityIndexCancelled, run_sync

logger = logging.getLogger(__name__)


def _configure_worker_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _emit_complete(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def main(argv: list[str] | None = None) -> int:
    _configure_worker_logging()
    parser = argparse.ArgumentParser(description="Diffasaurus entity index worker")
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--source-key", type=str, default=None)
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--task-id", default="entity_sync")
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args(argv)

    reports_dir = normalize_reports_path(args.reports_dir)
    expected_source_key = source_key(reports_dir)
    db_path = args.db_path or entity_index_path(reports_dir)
    if args.source_key is not None and args.source_key != expected_source_key:
        _emit_complete(
            {
                "type": "complete",
                "generation": args.generation,
                "task_id": args.task_id,
                "status": "failed",
                "message": (
                    f"source key mismatch: expected {expected_source_key}, "
                    f"received {args.source_key}"
                ),
            }
        )
        return 2

    def emit_progress(event: SyncProgressEvent) -> None:
        payload = {"type": "progress", **asdict(event)}
        payload["task_id"] = args.task_id
        print(json.dumps(payload), flush=True)

    try:
        result = run_sync(
            reports_dir,
            db_path=db_path,
            generation=args.generation,
            cold=args.cold,
            progress=emit_progress,
        )
    except EntityIndexCancelled:
        _emit_complete(
            {
                "type": "complete",
                "generation": args.generation,
                "task_id": args.task_id,
                "status": "interrupted",
            }
        )
        return 1
    except Exception as exc:
        logger.error("Entity index worker failed: %s", exc)
        traceback.print_exc(file=sys.stderr)
        _emit_complete(
            {
                "type": "complete",
                "generation": args.generation,
                "task_id": args.task_id,
                "status": "failed",
                "message": str(exc),
            }
        )
        return 1

    payload = {"type": "complete", **asdict(result), "task_id": args.task_id}
    _emit_complete(payload)
    return 0 if result.status in ("complete", "completed_with_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
