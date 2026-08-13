"""Intune Configuration Policy semantic normalization and comparison."""

from __future__ import annotations

from diffasaurus.core.configuration_policies.canonical import (
    SEMANTIC_PAYLOAD_VERSION,
    canonical_json,
    semantic_hash,
)
from diffasaurus.core.configuration_policies.comparison_models import (
    COMPARISON_SCHEMA_VERSION,
    ConfigurationPolicyComparison,
)
from diffasaurus.core.configuration_policies.diff import (
    compare_latest_pair,
    compare_normalized_snapshots,
    compare_policy_bundles,
    comparison_canonical_json,
    summarize_comparison,
)
from diffasaurus.core.configuration_policies.history import (
    discover_policy_snapshots,
    select_latest_pair,
    select_latest_snapshot,
    select_previous_snapshot,
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
    "COMPARISON_SCHEMA_VERSION",
    "NORMALIZATION_SCHEMA_VERSION",
    "SEMANTIC_PAYLOAD_VERSION",
    "ConfigurationPolicyComparison",
    "NormalizedSnapshot",
    "canonical_json",
    "compare_latest_pair",
    "compare_normalized_snapshots",
    "compare_policy_bundles",
    "comparison_canonical_json",
    "discover_policy_snapshots",
    "normalize_bundle",
    "normalize_policy_document",
    "select_latest_pair",
    "select_latest_snapshot",
    "select_previous_snapshot",
    "semantic_hash",
    "summarize_comparison",
    "summarize_normalized_snapshot",
]
