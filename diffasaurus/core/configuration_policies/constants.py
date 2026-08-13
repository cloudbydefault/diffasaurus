"""Shared constants for Configuration Policy integration."""

from __future__ import annotations

CONFIGURATION_POLICY_FAMILY = "Intune_ConfigurationPolicies"

INFORMATIONAL_NORMALIZATION_WARNINGS = frozenset(
    {
        "classic_explicitness_unknown",
        "unresolved_choice_display_label",
        "unknown_modern_setting_instance_type",
        "missing_setting_definition_presentation",
    }
)

TRUST_LIMITING_NORMALIZATION_WARNINGS = frozenset(
    {
        "source_export_incomplete",
        "settings_retrieval_error",
        "assignments_retrieval_error",
        "definitions_retrieval_error",
        "presentation_values_retrieval_error",
        "normalization_error",
    }
)
