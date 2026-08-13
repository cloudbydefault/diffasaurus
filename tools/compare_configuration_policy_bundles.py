#!/usr/bin/env python3
"""Developer CLI for Configuration Policy semantic comparison (Phase 2)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from diffasaurus.core.configuration_policies.diff import (  # noqa: E402
    compare_latest_pair,
    compare_policy_bundles,
    summarize_comparison,
)
from diffasaurus.core.configuration_policies.history import discover_policy_snapshots  # noqa: E402


def _print_sanitized_summary(comparison_summary: dict[str, object], *, discovered_count: int | None = None) -> None:
    print("Configuration Policy Semantic Comparison")
    print("========================================")
    if discovered_count is not None:
        print(f"Discovered snapshots: {discovered_count}")
    print(f"Comparison status: {comparison_summary.get('comparisonStatus', 'unknown')}")
    print()

    policies = comparison_summary.get("policies", {})
    if isinstance(policies, dict):
        print("Policies")
        for key in ("added", "removed", "modified", "unchanged", "indeterminate"):
            print(f"  {key}: {policies.get(key, 0)}")
        print()

    events = comparison_summary.get("eventsByType", {})
    if isinstance(events, dict) and events:
        print("Semantic events")
        for event_type, count in sorted(events.items()):
            print(f"  {event_type}: {count}")
        print()

    filters = comparison_summary.get("assignmentFilters", {})
    if isinstance(filters, dict):
        print("Assignment filters")
        for key in ("added", "removed", "changed", "unchanged", "indeterminate"):
            print(f"  {key}: {filters.get(key, 0)}")
        print()

    print("Suppressions")
    print(f"  count: {comparison_summary.get('suppressionCount', 0)}")
    print()
    duration = comparison_summary.get("comparisonDurationSeconds")
    if duration is not None:
        print(f"Duration: {duration}s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Configuration Policy snapshot bundles.")
    parser.add_argument("baseline", nargs="?", type=Path, help="Baseline bundle path")
    parser.add_argument("target", nargs="?", type=Path, help="Target bundle path")
    parser.add_argument("--root", type=Path, default=None, help="Discover snapshots under this root")
    parser.add_argument("--latest", action="store_true", help="Compare latest chronological pair under --root")
    parser.add_argument("--output", type=Path, default=None, help="Write full comparison JSON to this path")
    args = parser.parse_args(argv)

    discovered_count: int | None = None
    if args.latest:
        if args.root is None:
            parser.error("--latest requires --root")
        discovery = discover_policy_snapshots(args.root)
        discovered_count = len(discovery.snapshots)
        comparison = compare_latest_pair(args.root)
        if comparison is None:
            print("No comparable snapshot pair found.")
            return 1
    else:
        if args.baseline is None or args.target is None:
            parser.error("baseline and target bundle paths are required unless --latest is used")
        comparison = compare_policy_bundles(args.baseline, args.target)

    summary = summarize_comparison(comparison)
    _print_sanitized_summary(summary, discovered_count=discovered_count)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(comparison.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
