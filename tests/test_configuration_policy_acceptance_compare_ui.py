"""Configuration Policy Compare UI acceptance tests (isolated Qt process)."""

from __future__ import annotations

import copy
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.configuration_policies import compare_policy_bundles
from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.ui.configuration_policy_presentation import build_semantic_detail_rows
from tests.configuration_policy_acceptance_support import (
    MONDAY_ID,
    TUESDAY_ID,
    build_two_snapshot_root,
    isolated_main_window,
    policy_triplet,
    wait_for_compare,
)
from tests.fixtures.configuration_policy_comparison import (
    DEFAULT_SOURCE_COVERAGE,
    build_comparison_bundle,
)


class CompareSemanticModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_policy_family_hides_key_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = build_two_snapshot_root(root)
            with isolated_main_window(report_dir=root) as window:
                window.families = {CONFIGURATION_POLICY_FAMILY: snapshots}
                window.family_combo.setCurrentText(CONFIGURATION_POLICY_FAMILY)
                window._populate_snapshot_combos(snapshots)
                self.assertFalse(window.key_selector_group.isVisible())

    def test_compare_uses_semantic_path_not_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = build_two_snapshot_root(root)
            with (
                patch(
                    "diffasaurus.ui.main_window.compare_snapshots",
                    side_effect=AssertionError("generic CSV compare must not run"),
                ),
                isolated_main_window(report_dir=root) as window,
            ):
                window.families = {CONFIGURATION_POLICY_FAMILY: snapshots}
                window.family_combo.setCurrentText(CONFIGURATION_POLICY_FAMILY)
                window._populate_snapshot_combos(snapshots)
                window.run_comparison()
                wait_for_compare(window)
                self.assertIsNotNone(window._policy_comparison)
                self.assertIsNone(window.current_comparison)
                summary = window._policy_comparison.summary["policies"]
                self.assertEqual(summary["unchanged"], 1)
                self.assertEqual(window.compare_cards["Stable"].value.text(), "1")

    def test_partial_comparison_shows_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = policy_triplet(MONDAY_ID)
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {
                "status": "error",
                "count": 0,
                "exportedCount": 0,
                "processingErrors": 1,
            }
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[],
                source_coverage=coverage,
            )
            build_comparison_bundle(
                root,
                snapshot_id=TUESDAY_ID,
                captured_at_utc="2099-01-07T09:00:00.0000000Z",
                policies=[policy],
            )
            comparison = compare_policy_bundles(root / MONDAY_ID, root / TUESDAY_ID)
            with isolated_main_window(report_dir=root) as window:
                window._comparison_generation = 1
                window._policy_comparison_ready(1, comparison)
                self.assertFalse(window.diff_notice.isHidden())
                self.assertIn("suppression", window.diff_notice.text().lower())
                self.assertEqual(len(build_semantic_detail_rows(comparison)), 0)


if __name__ == "__main__":
    unittest.main()
