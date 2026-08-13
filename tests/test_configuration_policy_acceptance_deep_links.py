"""Configuration Policy deep-link acceptance tests (isolated Qt process)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.configuration_policies.history import discover_policy_snapshots
from diffasaurus.core.report_history import ReportSnapshot, scan_report_index
from diffasaurus.ui.navigation_pages import PAGE_CONFIGURATION_POLICIES
from tests.configuration_policy_acceptance_support import (
    MONDAY_ID,
    drain_qt,
    isolated_main_window,
    isolated_snapshot_explorer,
    policy_triplet,
    wait_for_explorer,
    write_anchor,
)
from tests.fixtures.configuration_policy_comparison import build_comparison_bundle


class DeepLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _root_with_snapshot(self) -> tuple[Path, list[ReportSnapshot], str]:
        directory = tempfile.mkdtemp()
        root = Path(directory)
        policy = policy_triplet(MONDAY_ID, policy_id="policy-1", policy_name="Synthetic")
        build_comparison_bundle(
            root,
            snapshot_id=MONDAY_ID,
            captured_at_utc="2099-01-06T09:00:00.0000000Z",
            policies=[policy],
        )
        anchor = write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
        snapshots = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
        return root, snapshots, str(anchor)

    def test_recent_changes_open_configuration_policies(self):
        root, _snapshots, _anchor_path = self._root_with_snapshot()
        try:
            with isolated_main_window(report_dir=root) as window:
                descriptor = discover_policy_snapshots(root).snapshots[0]
                window._open_configuration_policies_from_recent(
                    CONFIGURATION_POLICY_FAMILY,
                    descriptor,
                    "configurationPolicies:policy-1",
                )
                self.assertEqual(window.stack.currentIndex(), PAGE_CONFIGURATION_POLICIES)
                self.assertEqual(
                    window.configuration_policy_page._selected_policy_key,
                    "configurationPolicies:policy-1",
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_explorer_open_emits_bundle_and_policy_key(self):
        root, snapshots, _ = self._root_with_snapshot()
        opened: list[tuple[str, object]] = []
        try:
            with isolated_snapshot_explorer() as explorer:
                explorer.set_report_dir(root)
                explorer.set_family(CONFIGURATION_POLICY_FAMILY)
                explorer.set_snapshots(snapshots)
                explorer.activate()
                wait_for_explorer(explorer)
                explorer._policy_rows = [{"policy_key": "configurationPolicies:policy-1"}]
                explorer.table.selectRow(0)
                explorer.open_configuration_policies_requested.connect(
                    lambda path, key: opened.append((path, key))
                )
                explorer._emit_open_configuration_policies()
                self.assertEqual(opened[0][1], "configurationPolicies:policy-1")
                self.assertTrue(str(opened[0][0]).endswith(MONDAY_ID))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_fossil_library_open_uses_anchor_path(self):
        root, _snapshots, anchor_path = self._root_with_snapshot()
        try:
            with isolated_main_window(report_dir=root) as window:
                window.configuration_policy_page.open_snapshot(
                    root,
                    anchor_path,
                    policy_key="configurationPolicies:policy-1",
                )
                drain_qt(thread_pools=[window.thread_pool])
                self.assertEqual(
                    window.configuration_policy_page._selected_policy_key,
                    "configurationPolicies:policy-1",
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
