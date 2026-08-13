"""Configuration Policy Snapshot Explorer acceptance tests (isolated Qt process)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.report_history import ReportSnapshot, scan_report_index
from diffasaurus.ui.snapshot_explorer import load_policy_snapshot_payload
from tests.configuration_policy_acceptance_support import (
    MONDAY_ID,
    isolated_snapshot_explorer,
    policy_triplet,
    wait_for_explorer,
    write_anchor,
)
from tests.fixtures.configuration_policy_comparison import build_comparison_bundle


class SnapshotExplorerPolicyModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_policy_aware_mode_uses_bundle_summary(self):
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
            snapshot = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY][0]
            with isolated_snapshot_explorer() as explorer:
                explorer.set_report_dir(root)
                explorer.set_family(CONFIGURATION_POLICY_FAMILY)
                explorer.set_snapshots([snapshot])
                explorer.activate()
                wait_for_explorer(explorer)
                self.assertFalse(explorer.open_policy_button.isHidden())
                self.assertIn("Policy bundle", explorer.status.text())
                self.assertEqual(list(explorer.model.headers), ["Name", "Platform", "Type", "Source"])
                payload = load_policy_snapshot_payload(root, snapshot)
                self.assertGreater(int(payload[4][0]["value"]), 0)

    def test_generic_csv_family_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Entra_Users_Properties_20260101-120000.csv"
            path.write_text("UPN,Department\nada@example.com,IT\n", encoding="utf-8-sig")
            snapshot = ReportSnapshot(
                path=path,
                family="Entra_Users_Properties",
                captured_at=datetime(2026, 1, 1, 12, 0, 0),
                row_count=1,
                headers=("UPN", "Department"),
            )
            with isolated_snapshot_explorer() as explorer:
                explorer.set_family("Entra_Users_Properties")
                explorer.set_snapshots([snapshot])
                explorer.activate()
                wait_for_explorer(explorer)
                self.assertFalse(explorer.open_policy_button.isVisible())
                self.assertIn("rows", explorer.status.text())


if __name__ == "__main__":
    unittest.main()
