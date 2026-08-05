from __future__ import annotations

import os


def persistent_entity_index_enabled() -> bool:
    value = os.environ.get("DIFFASAURUS_ENTITY_INDEX", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}
