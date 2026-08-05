from __future__ import annotations

import argparse
import os
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List or remove entity-index artifacts under an explicit root.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Entity index root to inspect (defaults to DIFFASAURUS_ENTITY_INDEX_ROOT)",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove files under --root only (never touches the live app config root)",
    )
    args = parser.parse_args(argv)

    configured_root = os.environ.get("DIFFASAURUS_ENTITY_INDEX_ROOT")
    app_root = entity_index_dir()
    target = args.root
    if target is None:
        if configured_root:
            target = Path(configured_root)
        else:
            print("No --root provided and DIFFASAURUS_ENTITY_INDEX_ROOT is unset.")
            print(f"Live application entity index directory: {app_root}")
            return 1

    target = target.expanduser().resolve()
    if args.remove and target == app_root and not configured_root:
        print(f"Refusing to remove files from live application index root: {app_root}")
        return 2

    if not target.is_dir():
        print(f"Target directory does not exist: {target}")
        return 0

    paths = sorted(target.glob("*"))
    print(f"Entity index artifacts under {target}: {len(paths)}")
    for path in paths:
        print(path)

    if args.remove:
        removed = 0
        for path in paths:
            if path.is_file():
                path.unlink()
                removed += 1
        print(f"Removed {removed} files from {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
