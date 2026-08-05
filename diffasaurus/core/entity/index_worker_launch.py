from __future__ import annotations

import sys
from pathlib import Path

from diffasaurus.core.paths import project_root


def worker_program() -> str:
    return sys.executable


def worker_script_argument() -> str | None:
    if getattr(sys, "frozen", False):
        return None
    return str(project_root() / "run.py")


def worker_dispatch_flag() -> str:
    return "--entity-index-worker"
