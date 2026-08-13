"""Intune Configuration Policy semantic normalization (Phase 1)."""

from __future__ import annotations

from diffasaurus.core.configuration_policies.canonical import (
    SEMANTIC_PAYLOAD_VERSION,
    canonical_json,
    semantic_hash,
)
from diffasaurus.core.configuration_policies.models import (
    NORMALIZATION_SCHEMA_VERSION,
    NormalizedSnapshot,
)
from diffasaurus.core.configuration_policies.normalizer import (
    normalize_bundle,
    normalize_policy_document,
    summarize_normalized_snapshot,
)

__all__ = [
    "NORMALIZATION_SCHEMA_VERSION",
    "SEMANTIC_PAYLOAD_VERSION",
    "NormalizedSnapshot",
    "canonical_json",
    "normalize_bundle",
    "normalize_policy_document",
    "semantic_hash",
    "summarize_normalized_snapshot",
]
