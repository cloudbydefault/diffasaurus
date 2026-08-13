"""Configuration Policy Fossil Library acceptance tests (isolated Qt process)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.report_history import scan_report_index
from diffasaurus.ui.navigation_pages import PAGE_CONFIGURATION_POLICIES
from diffasaurus.ui.report_runner import family_display_name
from tests.configuration_policy_acceptance_support import (
    MONDAY_ID,
    isolated_main_window,
    policy_triplet,
    write_anchor,
)
from tests.fixtures.configuration_policy_comparison import build_comparison_bundle


class FossilLibraryPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_one_bundle_one_fossil_and_open_routes_to_config_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            snapshots = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            self.assertEqual(len(snapshots), 1)
            with isolated_main_window(report_dir=root) as window:
                window.families = {CONFIGURATION_POLICY_FAMILY: snapshots}
                window.family_combo.clear()
                window.family_combo.addItem(CONFIGURATION_POLICY_FAMILY)
                window.family_combo.setCurrentText(CONFIGURATION_POLICY_FAMILY)
                window._populate_library(snapshots)
                self.assertEqual(window.library_table.rowCount(), 1)
                type_cell = window.library_table.item(0, 2).text()
                self.assertEqual(type_cell, "Policy bundle")
                self.assertEqual(
                    family_display_name(CONFIGURATION_POLICY_FAMILY),
                    "INTUNE · Configuration policies",
                )
                window.open_library_snapshot(0, 0)
                self.assertEqual(window.stack.currentIndex(), PAGE_CONFIGURATION_POLICIES)

    def test_per_policy_json_not_indexed_as_fossils(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = policy_triplet(MONDAY_ID)
            bundle = build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            families = scan_report_index(root)
            self.assertEqual(len(families[CONFIGURATION_POLICY_FAMILY]), 1)
            json_files = list(bundle.rglob("*.json"))
            self.assertGreater(len(json_files), 1)
            for json_file in json_files:
                self.assertNotIn(json_file.name, families)


if __name__ == "__main__":
    unittest.main()
