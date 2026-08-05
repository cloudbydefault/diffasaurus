"""Test package initialization.

Routes entity-index SQLite artifacts away from the developer application
config directory unless a test explicitly overrides the environment.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_INDEX_ROOT = Path(
    os.environ.get(
        "DIFFASAURUS_ENTITY_INDEX_ROOT",
        tempfile.mkdtemp(prefix="diffasaurus-entity-index-tests-"),
    )
)
_TEST_INDEX_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DIFFASAURUS_ENTITY_INDEX_ROOT", str(_TEST_INDEX_ROOT))
