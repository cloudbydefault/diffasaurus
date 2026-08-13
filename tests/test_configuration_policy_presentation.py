"""Unit tests for Configuration Policy presentation helpers."""

from __future__ import annotations

import unittest

from diffasaurus.core.configuration_policies.comparison_models import (
    ChangeEvent,
    ConfigurationPolicyComparison,
    PolicyDiff,
)
from diffasaurus.core.configuration_policies.models import (
    NormalizedAssignment,
    NormalizedPolicy,
    NormalizedPolicyCoverage,
    NormalizedSnapshot,
)
from diffasaurus.ui.configuration_policy_presentation import (
    PolicyInventoryRow,
    assignment_target_label,
    build_inventory_rows,
    build_modern_setting_tree,
    coverage_label,
    event_type_label,
    filter_inventory_rows,
    format_value,
    humanize_property_path,
    policy_events,
)


class ConfigurationPolicyPresentationFormattingTests(unittest.TestCase):
    def test_format_value_none_and_boolean(self):
        self.assertEqual(format_value(None), "—")
        self.assertEqual(format_value(True), "True")
        self.assertEqual(format_value(False), "False")

    def test_format_value_list_and_dict(self):
        self.assertEqual(format_value([1, 2]), "1, 2")
        self.assertEqual(format_value({"a": 1}), "a: 1")

    def test_humanize_property_path(self):
        self.assertEqual(humanize_property_path("cameraBlocked"), "Camera blocked")

    def test_event_type_labels(self):
        self.assertEqual(event_type_label("setting_changed"), "Setting changed")
        self.assertEqual(event_type_label("classic_property_changed"), "Observed property changed")

    def test_coverage_labels(self):
        self.assertEqual(coverage_label("success"), "Available")
        self.assertEqual(coverage_label("partial"), "Partial")
        self.assertEqual(coverage_label("not_applicable"), "Not applicable")

    def test_assignment_target_labels(self):
        self.assertEqual(assignment_target_label("all_devices"), "All devices")
        self.assertEqual(assignment_target_label("include_group"), "Included group")


class ModernSettingTreeTests(unittest.TestCase):
    def test_simple_setting(self):
        row = build_modern_setting_tree(
            {
                "definitionId": "def-1",
                "kind": "simple",
                "presentation": {"displayName": "Simple Label"},
                "values": [{"rawValue": True, "displayValue": None, "children": []}],
            }
        )
        self.assertEqual(row.label, "Simple Label")
        self.assertEqual(row.value, "True")

    def test_choice_with_display_value(self):
        row = build_modern_setting_tree(
            {
                "definitionId": "def-choice",
                "kind": "choice",
                "presentation": {"displayName": "Choice Label"},
                "values": [
                    {
                        "rawValue": "option-a",
                        "displayValue": "Option A",
                        "children": [],
                    }
                ],
            }
        )
        self.assertEqual(row.value, "Option A")
        self.assertEqual(row.tooltip, "option-a")

    def test_choice_fallback_without_display_value(self):
        row = build_modern_setting_tree(
            {
                "definitionId": "def-choice",
                "kind": "choice",
                "presentation": {},
                "values": [{"rawValue": "option-b", "displayValue": None, "children": []}],
            }
        )
        self.assertEqual(row.value, "option-b")

    def test_group_collection_not_flattened(self):
        row = build_modern_setting_tree(
            {
                "definitionId": "def-group",
                "kind": "group_collection",
                "presentation": {"displayName": "Group Parent"},
                "values": [
                    {
                        "rawValue": None,
                        "displayValue": None,
                        "children": [
                            {
                                "definitionId": "child-1",
                                "kind": "simple",
                                "presentation": {"displayName": "Child Setting"},
                                "values": [{"rawValue": "x", "displayValue": None, "children": []}],
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(row.kind, "group_collection")
        self.assertEqual(len(row.children), 1)
        self.assertEqual(row.children[0].label, "Entry 1")
        self.assertEqual(row.children[0].children[0].label, "Child Setting")

    def test_unknown_setting_type_warning(self):
        row = build_modern_setting_tree(
            {
                "definitionId": "def-unknown",
                "kind": "unknown",
                "presentation": {},
                "values": [{"rawValue": {"nested": 1}, "displayValue": None, "children": []}],
            }
        )
        self.assertIn("Unsupported setting shape", row.warning)


class InventoryFilterTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            PolicyInventoryRow(
                policy_key="modern:policy-1",
                name="Alpha",
                platform="Windows",
                policy_type="Settings catalog",
                source_label="Modern",
                assignment_count=1,
                change_state="modified",
                change_label="Modified",
                search_text="alpha windows modern settings catalog",
            ),
            PolicyInventoryRow(
                policy_key="classic:policy-2",
                name="Beta",
                platform="macOS",
                policy_type="Device restrictions",
                source_label="Classic",
                assignment_count=0,
                change_state="unchanged",
                change_label="Unchanged",
                search_text="beta macos classic device restrictions",
            ),
        ]

    def test_search_filter(self):
        filtered = filter_inventory_rows(self.rows, search="alpha")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].policy_key, "modern:policy-1")

    def test_platform_filter(self):
        filtered = filter_inventory_rows(self.rows, platform="macOS")
        self.assertEqual(filtered[0].policy_key, "classic:policy-2")

    def test_source_filter(self):
        filtered = filter_inventory_rows(self.rows, source="Classic")
        self.assertEqual(len(filtered), 1)

    def test_change_filter(self):
        filtered = filter_inventory_rows(self.rows, change="Modified")
        self.assertEqual(filtered[0].policy_key, "modern:policy-1")


class PolicyEventsTests(unittest.TestCase):
    def test_policy_events_from_comparison(self):
        comparison = ConfigurationPolicyComparison(
            policy_diffs=[
                PolicyDiff(
                    policy_key="modern:policy-1",
                    export_source="configurationPolicies",
                    state="modified",
                    changes=[
                        ChangeEvent(
                            event_type="setting_changed",
                            component_type="modern_setting",
                            policy_key="modern:policy-1",
                            before="a",
                            after="b",
                        )
                    ],
                )
            ],
            changes=[
                ChangeEvent(
                    event_type="setting_changed",
                    component_type="modern_setting",
                    policy_key="modern:policy-1",
                    before="a",
                    after="b",
                )
            ],
        )
        events = policy_events("modern:policy-1", comparison)
        self.assertEqual(len(events), 1)
        self.assertEqual(event_type_label(events[0].event_type), "Setting changed")


class BuildInventoryRowsTests(unittest.TestCase):
    def test_build_inventory_uses_policy_key(self):
        snapshot = NormalizedSnapshot(
            policies=[
                NormalizedPolicy(
                    policy_key="configurationPolicies:abc",
                    policy_id="abc",
                    export_source="configurationPolicies",
                    presentation={"name": "Policy A", "platform": "Windows", "policyType": "Catalog"},
                )
            ]
        )
        rows = build_inventory_rows(snapshot, {}, has_baseline=False)
        self.assertEqual(rows[0].policy_key, "configurationPolicies:abc")
        self.assertEqual(rows[0].change_label, "No baseline")


if __name__ == "__main__":
    unittest.main()
