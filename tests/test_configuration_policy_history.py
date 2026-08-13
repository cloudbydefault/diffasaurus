"""Phase 2 tests for Configuration Policy snapshot history discovery."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from diffasaurus.core.configuration_policies.history import (
    discover_policy_snapshots,
    select_latest_pair,
    select_previous_snapshot,
)
from tests.fixtures.configuration_policy_comparison import (
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)
from tests.fixtures.configuration_policy_bundle import SNAPSHOT_SCHEMA_VERSION

MONDAY_ID = "Intune_ConfigurationPolicies_20990106-090000"
TUESDAY_ID = "Intune_ConfigurationPolicies_20990107-090000"
WEDNESDAY_ID = "Intune_ConfigurationPolicies_20990108-090000"


def _policy_triplet(snapshot_id: str, policy_id: str) -> tuple[str, dict, dict]:
    rel = f"Windows/Modern/Policy__{policy_id}.json"
    doc = build_basic_modern_policy_document(policy_id=policy_id, policy_name=f"Policy {policy_id}")
    row = build_modern_inventory_row(
        policy_id=policy_id,
        policy_name=f"Policy {policy_id}",
        json_relative_path=rel,
    )
    row["SnapshotId"] = snapshot_id
    return rel, doc, row


class ConfigurationPolicyHistoryDiscoveryTests(unittest.TestCase):
    def test_chronology_uses_captured_at_not_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _policy_triplet(MONDAY_ID, "policy-1")
            newer_path = build_comparison_bundle(
                root,
                snapshot_id=WEDNESDAY_ID,
                captured_at_utc="2099-01-08T09:00:00.0000000Z",
                policies=[policy],
            )
            older_path = build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            os.utime(newer_path, (1_700_000_000, 1_700_000_000))
            os.utime(older_path, (1_600_000_000, 1_600_000_000))

            result = discover_policy_snapshots(root)
            self.assertEqual(len(result.snapshots), 2)
            self.assertEqual(result.snapshots[0].snapshot_id, MONDAY_ID)
            self.assertEqual(result.snapshots[1].snapshot_id, WEDNESDAY_ID)

    def test_equal_captured_at_uses_snapshot_id_tie_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _policy_triplet("alpha", "policy-1")
            build_comparison_bundle(
                root,
                snapshot_id="Intune_ConfigurationPolicies_B",
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[policy],
            )
            build_comparison_bundle(
                root,
                snapshot_id="Intune_ConfigurationPolicies_A",
                captured_at_utc="2099-01-01T12:00:00.0000000Z",
                policies=[policy],
            )
            result = discover_policy_snapshots(root)
            self.assertEqual(
                [item.snapshot_id for item in result.snapshots],
                ["Intune_ConfigurationPolicies_A", "Intune_ConfigurationPolicies_B"],
            )

    def test_previous_snapshot_does_not_skip_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _policy_triplet(MONDAY_ID, "policy-1")
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
                export_status="complete",
            )
            build_comparison_bundle(
                root,
                snapshot_id=TUESDAY_ID,
                captured_at_utc="2099-01-07T09:00:00.0000000Z",
                policies=[policy],
                export_status="incomplete",
            )
            build_comparison_bundle(
                root,
                snapshot_id=WEDNESDAY_ID,
                captured_at_utc="2099-01-08T09:00:00.0000000Z",
                policies=[policy],
                export_status="complete",
            )

            previous = select_previous_snapshot(root, WEDNESDAY_ID)
            self.assertIsNotNone(previous)
            assert previous is not None
            self.assertEqual(previous.snapshot_id, TUESDAY_ID)

            pair = select_latest_pair(root)
            self.assertIsNotNone(pair)
            assert pair is not None
            self.assertEqual(pair.baseline.snapshot_id, TUESDAY_ID)
            self.assertEqual(pair.target.snapshot_id, WEDNESDAY_ID)

    def test_malformed_bundle_reported_and_valid_siblings_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _policy_triplet(MONDAY_ID, "policy-1")
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            build_comparison_bundle(
                root,
                snapshot_id=WEDNESDAY_ID,
                captured_at_utc="2099-01-08T09:00:00.0000000Z",
                policies=[policy],
            )
            malformed = root / "Intune_ConfigurationPolicies_malformed"
            malformed.mkdir()
            (malformed / "snapshot_manifest.json").write_text("{not-json", encoding="utf-8")

            result = discover_policy_snapshots(root)
            self.assertEqual(len(result.snapshots), 2)
            self.assertTrue(any(item.category == "manifest_unreadable" for item in result.diagnostics))

    def test_unsupported_schema_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "Intune_ConfigurationPolicies_badschema"
            bundle.mkdir()
            (bundle / "inventory.csv").write_text("SnapshotId\n", encoding="utf-8")
            manifest = {
                "snapshotSchemaVersion": 99,
                "policyExportSchemaVersion": 4,
                "snapshotId": "Intune_ConfigurationPolicies_badschema",
                "capturedAtUtc": "2099-01-01T12:00:00.0000000Z",
                "inventoryRelativePath": "inventory.csv",
            }
            (bundle / "snapshot_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = discover_policy_snapshots(root)
            self.assertEqual(len(result.snapshots), 0)
            self.assertTrue(
                any(item.category == "unsupported_snapshot_schema_version" for item in result.diagnostics)
            )
            self.assertEqual(SNAPSHOT_SCHEMA_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
