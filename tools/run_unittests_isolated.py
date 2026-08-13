#!/usr/bin/env python3
"""Run each tests/test_*.py module in an isolated Python process."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path


def _format_returncode(returncode: int) -> str:
    if returncode < 0:
        sig = -returncode
        try:
            name = signal.Signals(sig).name
        except ValueError:
            name = "UNKNOWN"
        return f"terminated by signal {sig} ({name})"
    return str(returncode)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    tests_dir = root / "tests"
    modules = sorted(tests_dir.glob("test_*.py"))
    env = dict(**{key: value for key, value in dict(**__import__("os").environ).items()})
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    failures: list[str] = []
    for module in modules:
        name = f"tests.{module.stem}"
        print(f"=== {name} ===", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", name, "-q"],
            cwd=root,
            env=env,
        )
        if completed.returncode != 0:
            print(
                f"FAILED {name}: exit {_format_returncode(completed.returncode)}",
                file=sys.stderr,
                flush=True,
            )
            failures.append(name)
    if failures:
        print("Failed modules:", ", ".join(failures), file=sys.stderr)
        return 1
    print(f"All {len(modules)} test modules passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
