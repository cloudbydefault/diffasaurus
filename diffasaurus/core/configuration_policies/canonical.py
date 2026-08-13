"""Canonical JSON serialization and semantic hashing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SEMANTIC_PAYLOAD_VERSION = 1


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def semantic_hash(payload: Any) -> str:
    encoded = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "semantic_dict"):
        return value.semantic_dict()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
