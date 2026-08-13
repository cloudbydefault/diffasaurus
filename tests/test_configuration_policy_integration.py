"""Phase 4 Configuration Policy integration tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.configuration_policies.history import discover_policy_snapshots
from diffasaurus.core.configuration_policies.integration import (
    ConfigurationPolicyCsvComparisonError,
    POLICY_SESSION_CACHE,
    anchor_snapshot_id,
    build_policy_metric_history,
    classify_trust_banner,
    configuration_policy_family_change_status,
    guard_generic_csv_comparison,
    resolve_bundle_for_anchor,
    resolve_group_display_name,
    resolve_policy_period_pair,
)
from diffasaurus.core.configuration_policies.models import (
    NormalizedPolicy,
    NormalizedPolicyCoverage,
    NormalizedSnapshot,
)
from diffasaurus.core.report_history import (
    REASON_NO_BASELINE,
    ReportSnapshot,
    compare_snapshots,
    family_change_status,
    scan_report_index,
)
from diffasaurus.ui.report_runner import CATALOG_FAMILY_ORDER, REPORT_CATALOG
from tests.fixtures.configuration_policy_comparison import (
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)


def _anchor_for_bundle(root: Path, snapshot_id: str, captured_at: str) -> Path:
    return root / f"{snapshot_id}.csv"


def _write_anchor_csv(path: Path, snapshot_id: str, captured_at: str) -> None:
    path.write_text(
        "SnapshotId,CapturedAtUtc,PolicyId,PolicyName\n"
        f"{snapshot_id},{captured_at},policy-1,Synthetic\n",
        encoding="utf-8-sig",
    )


def _family_snapshot(
    family: str,
    captured_at: datetime,
    *,
    headers: tuple[str, ...] = ("PolicyId",),
) -> ReportSnapshot:
    return ReportSnapshot(
        path=Path(f"{family}_{captured_at:%Y%m%d-%H%M%S}.csv"),
        family=family,
        captured_at=captured_at,
        row_count=1,
        headers=headers,
    )


class ConfigurationPolicyCatalogTests(unittest.TestCase):
    def test_exporter_registered_in_catalog(self):
        self.assertIn("app_INTUNE_ConfigurationPolicy.ps1", REPORT_CATALOG)
        entry = REPORT_CATALOG["app_INTUNE_ConfigurationPolicy.ps1"]
        self.assertEqual(entry[3], CONFIGURATION_POLICY_FAMILY)
        self.assertIn(CONFIGURATION_POLICY_FAMILY, CATALOG_FAMILY_ORDER)


class CsvComparisonGuardTests(unittest.TestCase):
    def test_generic_compare_snapshots_rejects_configuration_policy_family(self):
        baseline = _family_snapshot(
            CONFIGURATION_POLICY_FAMILY,
            datetime(2099, 1, 1, 9, 0, 0),
            headers=("PolicyId",),
        )
        latest = _family_snapshot(
            CONFIGURATION_POLICY_FAMILY,
            datetime(2099, 1, 2, 9, 0, 0),
            headers=("PolicyId",),
        )
        with self.assertRaises(ConfigurationPolicyCsvComparisonError):
            compare_snapshots(baseline, latest, "PolicyId", CONFIGURATION_POLICY_FAMILY)

    def test_guard_helper(self):
        with self.assertRaises(ConfigurationPolicyCsvComparisonError):
            guard_generic_csv_comparison(CONFIGURATION_POLICY_FAMILY)


class AnchorBundleResolutionTests(unittest.TestCase):
    def test_anchor_stem_resolves_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_id = "Intune_ConfigurationPolicies_20990106-090000"
            policy = (
                "Windows/Modern/P.json",
                build_basic_modern_policy_document(policy_id="policy-1"),
                build_modern_inventory_row(
                    policy_id="policy-1",
                    policy_name="Synthetic",
                    json_relative_path="Windows/Modern/P.json",
                ),
            )
            build_comparison_bundle(
                root,
                snapshot_id=snapshot_id,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            anchor = _anchor_for_bundle(root, snapshot_id, "2099-01-06T09:00:00.0000000Z")
            _write_anchor_csv(anchor, snapshot_id, "2099-01-06T09:00:00.0000000Z")
            index = scan_report_index(root)
            anchors = index[CONFIGURATION_POLICY_FAMILY]
            descriptor = resolve_bundle_for_anchor(root, anchors[0])
            self.assertIsNotNone(descriptor)
            self.assertEqual(descriptor.snapshot_id, snapshot_id)


class LegacyExportDetectionTests(unittest.TestCase):
    def test_legacy_folder_emits_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "Intune_ConfigurationPolicies_20990101-090000"
            legacy.mkdir()
            (legacy / "Intune_ConfigurationPolicies_Manifest_20990101-090000.json").write_text(
                json.dumps({"legacy": True}),
                encoding="utf-8",
            )
            (legacy / "Intune_ConfigurationPolicies_Inventory_20990101-090000.csv").write_text(
                "PolicyId,PolicyName\npolicy-1,Synthetic\n",
                encoding="utf-8-sig",
            )
            result = discover_policy_snapshots(root)
            self.assertEqual(len(result.snapshots), 0)
            self.assertTrue(
                any(item.category == "legacy_configuration_policy_export" for item in result.diagnostics)
            )

    def test_legacy_and_valid_bundle_coexist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "Intune_ConfigurationPolicies_20990101-090000"
            legacy.mkdir()
            (legacy / "Intune_ConfigurationPolicies_Manifest_20990101-090000.json").write_text("{}", encoding="utf-8")
            (legacy / "Intune_ConfigurationPolicies_Inventory_20990101-090000.csv").write_text(
                "PolicyId\np\n", encoding="utf-8-sig"
            )
            snapshot_id = "Intune_ConfigurationPolicies_20990106-090000"
            policy = (
                "Windows/Modern/P.json",
                build_basic_modern_policy_document(policy_id="policy-1"),
                build_modern_inventory_row(
                    policy_id="policy-1",
                    policy_name="Synthetic",
                    json_relative_path="Windows/Modern/P.json",
                ),
            )
            build_comparison_bundle(
                root,
                snapshot_id=snapshot_id,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            result = discover_policy_snapshots(root)
            self.assertEqual(len(result.snapshots), 1)
            self.assertTrue(
                any(item.category == "legacy_configuration_policy_export" for item in result.diagnostics)
            )


class RecentChangesIntegrationTests(unittest.TestCase):
    def test_no_baseline_for_single_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_id = "Intune_ConfigurationPolicies_20990106-090000"
            build_comparison_bundle(
                root,
                snapshot_id=snapshot_id,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[
                    (
                        "Windows/Modern/P.json",
                        build_basic_modern_policy_document(policy_id="policy-1"),
                        build_modern_inventory_row(
                            policy_id="policy-1",
                            policy_name="Synthetic",
                            json_relative_path="Windows/Modern/P.json",
                        ),
                    )
                ],
            )
            anchor = _anchor_for_bundle(root, snapshot_id, "2099-01-06T09:00:00.0000000Z")
            _write_anchor_csv(anchor, snapshot_id, "2099-01-06T09:00:00.0000000Z")
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            status = family_change_status(
                CONFIGURATION_POLICY_FAMILY,
                anchors,
                timedelta(days=7),
                report_dir=root,
            )
            self.assertEqual(status.status, "no_data")
            self.assertEqual(status.reason, REASON_NO_BASELINE)

    def test_unchanged_self_comparison_not_used_for_two_distinct_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = (
                "Windows/Modern/P.json",
                build_basic_modern_policy_document(policy_id="policy-1"),
                build_modern_inventory_row(
                    policy_id="policy-1",
                    policy_name="Synthetic",
                    json_relative_path="Windows/Modern/P.json",
                ),
            )
            for snapshot_id, captured in (
                ("Intune_ConfigurationPolicies_20990106-090000", "2099-01-06T09:00:00.0000000Z"),
                ("Intune_ConfigurationPolicies_20990107-090000", "2099-01-07T09:00:00.0000000Z"),
            ):
                build_comparison_bundle(
                    root,
                    snapshot_id=snapshot_id,
                    captured_at_utc=captured,
                    policies=[policy],
                )
                _write_anchor_csv(
                    _anchor_for_bundle(root, snapshot_id, captured),
                    snapshot_id,
                    captured,
                )
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            status = configuration_policy_family_change_status(
                root,
                anchors,
                timedelta(days=2),
                datetime(2099, 1, 8, 12, 0, 0),
            )
            self.assertEqual(status.status, "unchanged")


class GroupResolutionTests(unittest.TestCase):
    def _write_groups(self, root: Path, captured_at: datetime, mapping: dict[str, str]) -> None:
        family = "Entra_Groups_Dependencies"
        stamp = captured_at.strftime("%Y%m%d-%H%M%S")
        path = root / f"{family}_{stamp}.csv"
        rows = ["GroupId,DisplayName\n"]
        for group_id, name in mapping.items():
            rows.append(f"{group_id},{name}\n")
        path.write_text("".join(rows), encoding="utf-8-sig")

    def test_at_or_before_group_name_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_groups(root, datetime(2099, 1, 6, 9, 0, 0), {"G1": "Old Name"})
            self._write_groups(root, datetime(2099, 1, 8, 9, 0, 0), {"G1": "New Name"})
            POLICY_SESSION_CACHE.invalidate(root)
            tuesday = datetime(2099, 1, 7, 12, 0, 0)
            thursday = datetime(2099, 1, 9, 12, 0, 0)
            self.assertEqual(resolve_group_display_name(root, "G1", tuesday), "Old Name")
            self.assertEqual(resolve_group_display_name(root, "G1", thursday), "New Name")
            self.assertEqual(
                resolve_group_display_name(root, "G1", datetime(2099, 1, 5, 12, 0, 0)),
                "G1",
            )

    def test_naive_entra_and_aware_utc_policy_datetime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_groups(root, datetime(2099, 1, 6, 9, 0, 0), {"G1": "Old Name"})
            self._write_groups(root, datetime(2099, 1, 8, 9, 0, 0), {"G1": "New Name"})
            POLICY_SESSION_CACHE.invalidate(root)
            policy_time = datetime(2099, 1, 7, 12, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(resolve_group_display_name(root, "G1", policy_time), "Old Name")

    def test_non_utc_aware_policy_datetime_uses_utc_instant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Entra snapshot at 09:00 naive (treated as local wall time in filename semantics).
            self._write_groups(root, datetime(2099, 1, 6, 9, 0, 0), {"G1": "Morning Name"})
            self._write_groups(root, datetime(2099, 1, 6, 11, 0, 0), {"G1": "Later Name"})
            POLICY_SESSION_CACHE.invalidate(root)
            # 10:00 UTC == 12:00 +02:00; eligible snapshot is 09:00 naive, not 11:00.
            policy_time = datetime(2099, 1, 6, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
            self.assertEqual(resolve_group_display_name(root, "G1", policy_time), "Morning Name")

    def test_future_entra_snapshot_excluded_with_aware_policy_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_groups(root, datetime(2099, 1, 6, 9, 0, 0), {"G1": "Old Name"})
            self._write_groups(root, datetime(2099, 1, 8, 9, 0, 0), {"G1": "Future Name"})
            POLICY_SESSION_CACHE.invalidate(root)
            policy_time = datetime(2099, 1, 7, 12, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(resolve_group_display_name(root, "G1", policy_time), "Old Name")
            self.assertNotEqual(
                resolve_group_display_name(root, "G1", policy_time),
                "Future Name",
            )

    def test_before_first_entra_snapshot_falls_back_with_aware_policy_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_groups(root, datetime(2099, 1, 8, 9, 0, 0), {"G1": "New Name"})
            POLICY_SESSION_CACHE.invalidate(root)
            policy_time = datetime(2099, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(resolve_group_display_name(root, "G1", policy_time), "G1")


class TrustPresentationTests(unittest.TestCase):
    def test_complete_classic_informational_only(self):
        policy = NormalizedPolicy(
            policy_key="deviceConfigurations:classic-1",
            policy_id="classic-1",
            export_source="deviceConfigurations",
            coverage=NormalizedPolicyCoverage(
                semantic_hash_eligible=True,
                normalization_warnings=["classic_explicitness_unknown"],
            ),
        )
        snapshot = NormalizedSnapshot(
            source_export_status="complete",
            normalization_status="partial",
            policies=[policy],
            normalization_warnings=["classic_explicitness_unknown"],
        )
        banner = classify_trust_banner("complete", snapshot)
        self.assertEqual(banner.level, "informational")
        self.assertIn("semantic coverage available", banner.headline)

    def test_incomplete_export_is_warning(self):
        snapshot = NormalizedSnapshot(source_export_status="incomplete", normalization_status="partial")
        banner = classify_trust_banner("incomplete", snapshot)
        self.assertEqual(banner.level, "warning")


class PolicyMetricHistoryTests(unittest.TestCase):
    def test_policy_metrics_not_csv_row_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_id = "Intune_ConfigurationPolicies_20990106-090000"
            build_comparison_bundle(
                root,
                snapshot_id=snapshot_id,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[
                    (
                        "Windows/Modern/P.json",
                        build_basic_modern_policy_document(policy_id="policy-1"),
                        build_modern_inventory_row(
                            policy_id="policy-1",
                            policy_name="Synthetic",
                            json_relative_path="Windows/Modern/P.json",
                        ),
                    )
                ],
            )
            anchor_path = _anchor_for_bundle(root, snapshot_id, "2099-01-06T09:00:00.0000000Z")
            _write_anchor_csv(anchor_path, snapshot_id, "2099-01-06T09:00:00.0000000Z")
            anchors = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            POLICY_SESSION_CACHE.invalidate(root)
            history = build_policy_metric_history(root, anchors)
            self.assertEqual(len(history), 1)
            _anchor, metrics = history[0]
            self.assertIn("Policies", metrics)
            self.assertGreaterEqual(metrics["Policies"], 1.0)


if __name__ == "__main__":
    unittest.main()
