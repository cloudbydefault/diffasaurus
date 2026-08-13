"""Configuration Policy core acceptance tests (no Qt main window)."""

from __future__ import annotations

import ast
import copy
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from diffasaurus.core.configuration_policies import compare_policy_bundles
from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.configuration_policies.history import discover_policy_snapshots
from diffasaurus.core.configuration_policies.integration import (
    POLICY_SESSION_CACHE,
    build_semantic_event_details,
    configuration_policy_family_change_status,
    resolve_policy_period_pair,
)
from diffasaurus.core.report_history import (
    REASON_NO_BASELINE,
    report_run_health,
    scan_report_index,
)
from diffasaurus.ui.configuration_policy_presentation import (
    event_type_label,
    semantic_event_details_to_display_rows,
)
from tests.configuration_policy_acceptance_support import (
    MONDAY_ID,
    TUESDAY_ID,
    WEDNESDAY_ID,
    build_two_snapshot_root,
    policy_triplet,
    write_anchor,
)
from tests.fixtures.configuration_policy_comparison import (
    DEFAULT_SOURCE_COVERAGE,
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)

CORE_PACKAGE = Path(__file__).resolve().parents[1] / "diffasaurus" / "core" / "configuration_policies"


class CoreLayerIsolationTests(unittest.TestCase):
    def test_configuration_policies_core_modules_do_not_import_ui(self):
        offenders: list[str] = []
        for path in CORE_PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("diffasaurus.ui"):
                            offenders.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("diffasaurus.ui"):
                        offenders.append(f"{path.name}: from {node.module}")
        self.assertEqual(offenders, [])

    def test_build_semantic_event_details_is_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_doc = build_basic_modern_policy_document(policy_name="Old Name")
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["policy"]["name"] = "New Name"
            rel = "Windows/Modern/P__policy-1.json"
            row = build_modern_inventory_row(
                policy_id="policy-1",
                policy_name="Old Name",
                json_relative_path=rel,
            )
            target_row = build_modern_inventory_row(
                policy_id="policy-1",
                policy_name="New Name",
                json_relative_path=rel,
            )
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[(rel, baseline_doc, row)],
            )
            build_comparison_bundle(
                root,
                snapshot_id=TUESDAY_ID,
                captured_at_utc="2099-01-07T09:00:00.0000000Z",
                policies=[(rel, target_doc, target_row)],
            )
            comparison = compare_policy_bundles(root / MONDAY_ID, root / TUESDAY_ID)
            neutral = build_semantic_event_details(comparison)
            self.assertGreaterEqual(len(neutral), 1)
            event_types = {item["event_type"] for item in neutral}
            self.assertIn("policy_renamed", event_types)
            self.assertNotIn("Change", neutral[0])
            display = semantic_event_details_to_display_rows(
                [item for item in neutral if item["event_type"] == "policy_renamed"]
            )
            self.assertEqual(display[0]["Change"], event_type_label("policy_renamed"))


class CompareSemanticCoreTests(unittest.TestCase):
    def test_anchor_csv_row_difference_does_not_create_semantic_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = build_two_snapshot_root(root)
            write_anchor(
                root,
                MONDAY_ID,
                "2099-01-06T09:00:00.0000000Z",
                extra_rows="bogus-id,2099-01-06T09:00:00.0000000Z,policy-9,Fake\n",
            )
            write_anchor(
                root,
                TUESDAY_ID,
                "2099-01-07T09:00:00.0000000Z",
                extra_rows="bogus-id,2099-01-07T09:00:00.0000000Z,policy-9,Fake\n",
            )
            status = configuration_policy_family_change_status(
                root,
                snapshots,
                timedelta(days=2),
                datetime(2099, 1, 8, 12, 0, 0),
            )
            self.assertEqual(status.status, "unchanged")
            self.assertEqual(status.policy_summary.event_count, 0)


class RecentChangesMatrixTests(unittest.TestCase):
    def _two_snapshot_root(self, root: Path, *, target_doc=None, target_name: str | None = None):
        policy = policy_triplet(MONDAY_ID)
        build_comparison_bundle(
            root,
            snapshot_id=MONDAY_ID,
            captured_at_utc="2099-01-06T09:00:00.0000000Z",
            policies=[policy],
        )
        if target_doc is not None:
            target_policy = (
                policy[0],
                target_doc,
                build_modern_inventory_row(
                    policy_id="policy-1",
                    policy_name=target_name or target_doc["policy"]["name"],
                    json_relative_path=policy[0],
                ),
            )
        else:
            target_policy = policy
        build_comparison_bundle(
            root,
            snapshot_id=TUESDAY_ID,
            captured_at_utc="2099-01-07T09:00:00.0000000Z",
            policies=[target_policy],
        )
        for snapshot_id, captured in (
            (MONDAY_ID, "2099-01-06T09:00:00.0000000Z"),
            (TUESDAY_ID, "2099-01-07T09:00:00.0000000Z"),
        ):
            write_anchor(root, snapshot_id, captured)
        POLICY_SESSION_CACHE.invalidate(root)
        return scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]

    def test_policy_rename_changed_with_human_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_doc = build_basic_modern_policy_document(policy_name="Old Name")
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["policy"]["name"] = "New Name"
            rel = "Windows/Modern/P__policy-1.json"
            row = build_modern_inventory_row(
                policy_id="policy-1",
                policy_name="Old Name",
                json_relative_path=rel,
            )
            target_row = build_modern_inventory_row(
                policy_id="policy-1",
                policy_name="New Name",
                json_relative_path=rel,
            )
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[(rel, baseline_doc, row)],
            )
            build_comparison_bundle(
                root,
                snapshot_id=TUESDAY_ID,
                captured_at_utc="2099-01-07T09:00:00.0000000Z",
                policies=[(rel, target_doc, target_row)],
            )
            for snapshot_id, captured in (
                (MONDAY_ID, "2099-01-06T09:00:00.0000000Z"),
                (TUESDAY_ID, "2099-01-07T09:00:00.0000000Z"),
            ):
                write_anchor(root, snapshot_id, captured)
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            status = configuration_policy_family_change_status(
                root,
                anchors,
                timedelta(days=2),
                datetime(2099, 1, 8, 12, 0, 0),
                include_details=True,
            )
            self.assertEqual(status.status, "changed")
            self.assertEqual(status.policy_summary.modified, 1)
            details = semantic_event_details_to_display_rows(status.semantic_details)
            self.assertEqual(details[0]["Change"], event_type_label("policy_renamed"))
            self.assertNotIn("policy_added", {item["event_type"] for item in status.semantic_details})

    def test_assignment_change_semantic_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_doc = build_basic_modern_policy_document()
            baseline_doc["assignments"] = [
                {"target": {"@odata.type": "#microsoft.graph.allLicensedUsersAssignmentTarget"}},
            ]
            baseline_doc["retrieval"]["assignments"] = {"status": "success", "count": 1, "error": None}
            target_doc = copy.deepcopy(baseline_doc)
            target_doc["assignments"] = [
                {
                    "target": {
                        "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                        "groupId": "group-1",
                    }
                },
            ]
            policy = policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[(policy[0], baseline_doc, policy[2])],
            )
            build_comparison_bundle(
                root,
                snapshot_id=TUESDAY_ID,
                captured_at_utc="2099-01-07T09:00:00.0000000Z",
                policies=[(policy[0], target_doc, policy[2])],
            )
            for snapshot_id, captured in (
                (MONDAY_ID, "2099-01-06T09:00:00.0000000Z"),
                (TUESDAY_ID, "2099-01-07T09:00:00.0000000Z"),
            ):
                write_anchor(root, snapshot_id, captured)
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            status = configuration_policy_family_change_status(
                root,
                anchors,
                timedelta(days=2),
                datetime(2099, 1, 8, 12, 0, 0),
                include_details=True,
            )
            event_types = {item["event_type"] for item in status.semantic_details}
            self.assertIn("assignment_added", event_types)
            self.assertIn("assignment_removed", event_types)

    def test_partial_comparison_not_counted_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {
                "status": "error",
                "count": 0,
                "exportedCount": 0,
                "processingErrors": 1,
            }
            policy = policy_triplet(MONDAY_ID)
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
            for snapshot_id, captured in (
                (MONDAY_ID, "2099-01-06T09:00:00.0000000Z"),
                (TUESDAY_ID, "2099-01-07T09:00:00.0000000Z"),
            ):
                write_anchor(root, snapshot_id, captured)
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            status = configuration_policy_family_change_status(
                root,
                anchors,
                timedelta(days=2),
                datetime(2099, 1, 8, 12, 0, 0),
            )
            self.assertEqual(status.status, "partial")
            self.assertGreater(status.policy_summary.suppression_count, 0)
            self.assertEqual(status.policy_summary.event_count, 0)

    def test_period_cutoff_chooses_baseline_at_or_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = policy_triplet(MONDAY_ID)
            for snapshot_id, captured in (
                (MONDAY_ID, "2099-01-06T09:00:00.0000000Z"),
                (TUESDAY_ID, "2099-01-07T09:00:00.0000000Z"),
                (WEDNESDAY_ID, "2099-01-08T09:00:00.0000000Z"),
            ):
                build_comparison_bundle(
                    root,
                    snapshot_id=snapshot_id,
                    captured_at_utc=captured,
                    policies=[policy],
                )
                write_anchor(root, snapshot_id, captured)
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            pair = resolve_policy_period_pair(
                root,
                anchors,
                timedelta(days=1),
                datetime(2099, 1, 8, 15, 0, 0),
            )
            self.assertEqual(pair.baseline.snapshot_id, TUESDAY_ID)
            self.assertEqual(pair.target.snapshot_id, WEDNESDAY_ID)

    def test_incomplete_snapshot_not_skipped_in_chronology(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = policy_triplet(MONDAY_ID)
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
            for snapshot_id, captured in (
                (MONDAY_ID, "2099-01-06T09:00:00.0000000Z"),
                (TUESDAY_ID, "2099-01-07T09:00:00.0000000Z"),
                (WEDNESDAY_ID, "2099-01-08T09:00:00.0000000Z"),
            ):
                write_anchor(root, snapshot_id, captured)
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            pair = resolve_policy_period_pair(
                root,
                anchors,
                timedelta(days=2),
                datetime(2099, 1, 9, 12, 0, 0),
            )
            self.assertEqual(pair.baseline.snapshot_id, TUESDAY_ID)

    def test_anchor_csv_diff_without_semantic_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            anchors = self._two_snapshot_root(root)
            write_anchor(
                root,
                TUESDAY_ID,
                "2099-01-07T09:00:00.0000000Z",
                extra_rows="x,2099-01-07T09:00:00.0000000Z,policy-9,Changed CSV only\n",
            )
            status = configuration_policy_family_change_status(
                root,
                anchors,
                timedelta(days=2),
                datetime(2099, 1, 8, 12, 0, 0),
            )
            self.assertEqual(status.status, "unchanged")

    def test_no_baseline_single_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            status = configuration_policy_family_change_status(
                root,
                anchors,
                timedelta(days=7),
            )
            self.assertEqual(status.status, "no_data")
            self.assertEqual(status.reason, REASON_NO_BASELINE)


class RunHealthPolicyTests(unittest.TestCase):
    def test_complete_bundle_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            families = scan_report_index(root)
            health = report_run_health(
                families,
                reference=datetime(2099, 1, 6, 12, 0, 0),
                business_day_count=1,
                report_dir=root,
            )[0]
            self.assertEqual(health.observed, 1)
            self.assertEqual(health.missing, 0)
            self.assertEqual(health.attention, 0)

    def test_anchor_without_bundle_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            families = scan_report_index(root)
            health = report_run_health(
                families,
                reference=datetime(2099, 1, 6, 12, 0, 0),
                business_day_count=1,
                report_dir=root,
            )[0]
            self.assertEqual(health.observed, 0)
            self.assertEqual(health.missing, 1)

    def test_incomplete_bundle_observed_with_attention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
                export_status="incomplete",
            )
            write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            families = scan_report_index(root)
            health = report_run_health(
                families,
                reference=datetime(2099, 1, 6, 12, 0, 0),
                business_day_count=1,
                report_dir=root,
            )[0]
            self.assertEqual(health.observed, 1)
            self.assertEqual(health.attention, 1)
            self.assertEqual(health.status, "Attention")

    def test_legacy_only_not_counted_as_successful_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / MONDAY_ID
            legacy.mkdir()
            (legacy / "Intune_ConfigurationPolicies_Manifest_20990106-090000.json").write_text(
                "{}",
                encoding="utf-8",
            )
            (legacy / "Intune_ConfigurationPolicies_Inventory_20990106-090000.csv").write_text(
                "PolicyId\np\n",
                encoding="utf-8-sig",
            )
            result = discover_policy_snapshots(root)
            self.assertEqual(len(result.snapshots), 0)
            families = scan_report_index(root)
            self.assertNotIn(CONFIGURATION_POLICY_FAMILY, families)


if __name__ == "__main__":
    unittest.main()
