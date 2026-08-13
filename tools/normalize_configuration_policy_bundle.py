#!/usr/bin/env python3
"""Developer CLI for Phase 1 Configuration Policy semantic normalization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from diffasaurus.core.configuration_policies.normalizer import (  # noqa: E402
    normalize_bundle,
    summarize_normalized_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize a Phase 0 Intune Configuration Policy Snapshot Bundle."
    )
    parser.add_argument(
        "bundle_path",
        type=Path,
        help="Path to a Phase 0 snapshot bundle directory or snapshot_manifest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the full normalized JSON snapshot",
    )
    args = parser.parse_args(argv)

    snapshot = normalize_bundle(args.bundle_path)
    summary = summarize_normalized_snapshot(snapshot)

    print(json.dumps(summary, indent=2, sort_keys=True))

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(snapshot.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
