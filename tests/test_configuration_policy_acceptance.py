"""Phase 4 freeze acceptance tests for Configuration Policy integration."""

from __future__ import annotations

import ast
import copy
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

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
    ReportSnapshot,
    compare_snapshot_counts,
    compare_snapshots,
    report_run_health,
    scan_report_index,
)
from diffasaurus.ui.configuration_policy_presentation import (
    build_semantic_detail_rows,
    event_type_label,
    semantic_event_details_to_display_rows,
)
from diffasaurus.ui.main_window import DiffasaurusWindow
from diffasaurus.ui.navigation_pages import PAGE_CONFIGURATION_POLICIES
from diffasaurus.ui.report_runner import family_display_name
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer, load_policy_snapshot_payload
from tests.fixtures.configuration_policy_comparison import (
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)

CORE_PACKAGE = Path(__file__).resolve().parents[1] / "diffasaurus" / "core" / "configuration_policies"
MONDAY_ID = "Intune_ConfigurationPolicies_20990106-090000"
TUESDAY_ID = "Intune_ConfigurationPolicies_20990107-090000"
WEDNESDAY_ID = "Intune_ConfigurationPolicies_20990108-090000"


def _anchor_path(root: Path, snapshot_id: str) -> Path:
    return root / f"{snapshot_id}.csv"


def _write_anchor(
    root: Path,
    snapshot_id: str,
    captured_at: str,
    *,
    extra_rows: str = "",
) -> Path:
    path = _anchor_path(root, snapshot_id)
    path.write_text(
        "SnapshotId,CapturedAtUtc,PolicyId,PolicyName\n"
        f"{snapshot_id},{captured_at},policy-1,Synthetic\n"
        f"{extra_rows}",
        encoding="utf-8-sig",
    )
    return path


def _policy_triplet(snapshot_id: str, policy_id: str = "policy-1", policy_name: str = "Synthetic"):
    rel = f"Windows/Modern/P__{policy_id}.json"
    doc = build_basic_modern_policy_document(policy_id=policy_id, policy_name=policy_name)
    row = build_modern_inventory_row(
        policy_id=policy_id,
        policy_name=policy_name,
        json_relative_path=rel,
    )
    return rel, doc, row


def _wait_for_compare(window: DiffasaurusWindow, timeout_s: float = 8.0) -> None:
    deadline = time.time() + timeout_s
    while window.compare_button.text() == "Comparing…" and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    window.thread_pool.waitForDone(int(timeout_s * 1000))
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.02)


def _wait_for_explorer(explorer: SnapshotExplorer, timeout_s: float = 8.0) -> None:
    deadline = time.time() + timeout_s
    while explorer.progress.isVisible() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    explorer.thread_pool.waitForDone(int(timeout_s * 1000))
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.02)


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


class CompareSemanticModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build_two_snapshot_root(self, root: Path) -> list[ReportSnapshot]:
        policy = _policy_triplet(MONDAY_ID)
        for snapshot_id, captured in (
            (MONDAY_ID, "2099-01-06T09:00:00.0000000Z"),
            (TUESDAY_ID, "2099-01-07T09:00:00.0000000Z"),
        ):
            build_comparison_bundle(
                root,
                snapshot_id=snapshot_id,
                captured_at_utc=captured,
                policies=[policy],
            )
            _write_anchor(root, snapshot_id, captured)
        POLICY_SESSION_CACHE.invalidate(root)
        return scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]

    def test_policy_family_hides_key_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = self._build_two_snapshot_root(root)
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=root,
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window.families = {CONFIGURATION_POLICY_FAMILY: snapshots}
                    window.report_dir = root
                    window.family_combo.setCurrentText(CONFIGURATION_POLICY_FAMILY)
                    window._populate_snapshot_combos(snapshots)
                    self.assertFalse(window.key_selector_group.isVisible())
                finally:
                    window.close()

    def test_compare_uses_semantic_path_not_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = self._build_two_snapshot_root(root)
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=root,
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None            ), patch(
                "diffasaurus.ui.main_window.compare_snapshots",
                side_effect=AssertionError("generic CSV compare must not run"),
            ):
                window = DiffasaurusWindow()
                try:
                    window.families = {CONFIGURATION_POLICY_FAMILY: snapshots}
                    window.report_dir = root
                    window.family_combo.setCurrentText(CONFIGURATION_POLICY_FAMILY)
                    window._populate_snapshot_combos(snapshots)
                    window.run_comparison()
                    _wait_for_compare(window)
                    self.assertIsNotNone(window._policy_comparison)
                    self.assertIsNone(window.current_comparison)
                    summary = window._policy_comparison.summary["policies"]
                    self.assertEqual(summary["unchanged"], 1)
                    self.assertEqual(window.compare_cards["Stable"].value.text(), "1")
                finally:
                    window.close()

    def test_anchor_csv_row_difference_does_not_create_semantic_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = self._build_two_snapshot_root(root)
            _write_anchor(
                root,
                MONDAY_ID,
                "2099-01-06T09:00:00.0000000Z",
                extra_rows="bogus-id,2099-01-06T09:00:00.0000000Z,policy-9,Fake\n",
            )
            _write_anchor(
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

    def test_partial_comparison_shows_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from tests.fixtures.configuration_policy_comparison import DEFAULT_SOURCE_COVERAGE

            policy = _policy_triplet(MONDAY_ID)
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
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=root,
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window._comparison_generation = 1
                    window._policy_comparison_ready(1, comparison)
                    self.assertFalse(window.diff_notice.isHidden())
                    self.assertIn("suppression", window.diff_notice.text().lower())
                    self.assertEqual(len(build_semantic_detail_rows(comparison)), 0)
                finally:
                    window.close()


class SnapshotExplorerPolicyModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_policy_aware_mode_uses_bundle_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            anchor = _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            snapshot = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY][0]
            explorer = SnapshotExplorer()
            try:
                explorer.set_report_dir(root)
                explorer.set_family(CONFIGURATION_POLICY_FAMILY)
                explorer.set_snapshots([snapshot])
                explorer.activate()
                _wait_for_explorer(explorer)
                self.assertFalse(explorer.open_policy_button.isHidden())
                self.assertIn("Policy bundle", explorer.status.text())
                self.assertEqual(list(explorer.model.headers), ["Name", "Platform", "Type", "Source"])
                payload = load_policy_snapshot_payload(root, snapshot)
                self.assertGreater(int(payload[4][0]["value"]), 0)  # Policies count
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1000)

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
            explorer = SnapshotExplorer()
            try:
                explorer.set_family("Entra_Users_Properties")
                explorer.set_snapshots([snapshot])
                explorer.activate()
                _wait_for_explorer(explorer)
                self.assertFalse(explorer.open_policy_button.isVisible())
                self.assertIn("rows", explorer.status.text())
            finally:
                explorer.close()
                explorer.thread_pool.waitForDone(1000)


class FossilLibraryPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_one_bundle_one_fossil_and_open_routes_to_config_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            anchor = _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            snapshots = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
            self.assertEqual(len(snapshots), 1)
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=root,
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window.families = {CONFIGURATION_POLICY_FAMILY: snapshots}
                    window.report_dir = root
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
                finally:
                    window.close()

    def test_per_policy_json_not_indexed_as_fossils(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy_triplet(MONDAY_ID)
            bundle = build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
            families = scan_report_index(root)
            self.assertEqual(len(families[CONFIGURATION_POLICY_FAMILY]), 1)
            json_files = list(bundle.rglob("*.json"))
            self.assertGreater(len(json_files), 1)
            for json_file in json_files:
                self.assertNotIn(json_file.name, families)


class DeepLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _root_with_snapshot(self) -> tuple[Path, list[ReportSnapshot], str]:
        directory = tempfile.mkdtemp()
        root = Path(directory)
        policy = _policy_triplet(MONDAY_ID, policy_id="policy-1", policy_name="Synthetic")
        build_comparison_bundle(
            root,
            snapshot_id=MONDAY_ID,
            captured_at_utc="2099-01-06T09:00:00.0000000Z",
            policies=[policy],
        )
        anchor = _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
        snapshots = scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]
        return root, snapshots, str(anchor)

    def test_recent_changes_open_configuration_policies(self):
        root, snapshots, anchor_path = self._root_with_snapshot()
        try:
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=root,
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window.report_dir = root
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
                    window.close()
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    def test_explorer_open_emits_bundle_and_policy_key(self):
        root, snapshots, _ = self._root_with_snapshot()
        opened: list[tuple[str, object]] = []
        explorer = SnapshotExplorer()
        try:
            explorer.set_report_dir(root)
            explorer.set_family(CONFIGURATION_POLICY_FAMILY)
            explorer.set_snapshots(snapshots)
            explorer.activate()
            _wait_for_explorer(explorer)
            explorer._policy_rows = [{"policy_key": "configurationPolicies:policy-1"}]
            explorer.table.selectRow(0)
            explorer.open_configuration_policies_requested.connect(
                lambda path, key: opened.append((path, key))
            )
            explorer._emit_open_configuration_policies()
            self.assertEqual(opened[0][1], "configurationPolicies:policy-1")
            self.assertTrue(str(opened[0][0]).endswith(MONDAY_ID))
        finally:
            explorer.close()
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    def test_fossil_library_open_uses_anchor_path(self):
        root, snapshots, anchor_path = self._root_with_snapshot()
        try:
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=root,
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window.report_dir = root
                    window.configuration_policy_page.open_snapshot(
                        root,
                        anchor_path,
                        policy_key="configurationPolicies:policy-1",
                    )
                    window.thread_pool.waitForDone(5000)
                    for _ in range(30):
                        QApplication.processEvents()
                    self.assertEqual(
                        window.configuration_policy_page._selected_policy_key,
                        "configurationPolicies:policy-1",
                    )
                finally:
                    window.close()
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)


class RecentChangesMatrixTests(unittest.TestCase):
    def _two_snapshot_root(self, root: Path, *, target_doc=None, target_name: str | None = None):
        policy = _policy_triplet(MONDAY_ID)
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
            _write_anchor(root, snapshot_id, captured)
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
                _write_anchor(root, snapshot_id, captured)
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
            policy = _policy_triplet(MONDAY_ID)
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
                _write_anchor(root, snapshot_id, captured)
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
            from tests.fixtures.configuration_policy_comparison import DEFAULT_SOURCE_COVERAGE

            coverage = copy.deepcopy(DEFAULT_SOURCE_COVERAGE)
            coverage["modern"] = {
                "status": "error",
                "count": 0,
                "exportedCount": 0,
                "processingErrors": 1,
            }
            policy = _policy_triplet(MONDAY_ID)
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
                _write_anchor(root, snapshot_id, captured)
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
            policy = _policy_triplet(MONDAY_ID)
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
                _write_anchor(root, snapshot_id, captured)
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
            policy = _policy_triplet(MONDAY_ID)
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
                _write_anchor(root, snapshot_id, captured)
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
            _write_anchor(
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
            policy = _policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
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
            policy = _policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
            )
            _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
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
            _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
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
            policy = _policy_triplet(MONDAY_ID)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[policy],
                export_status="incomplete",
            )
            _write_anchor(root, MONDAY_ID, "2099-01-06T09:00:00.0000000Z")
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
