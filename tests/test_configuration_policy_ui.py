"""Phase 3 Configuration Policy UI tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHeaderView

from diffasaurus.core.configuration_policies import discover_policy_snapshots
from diffasaurus.ui.configuration_policy_page import (
    CLASSIC_EXPLICITNESS_NOTE,
    ConfigurationPolicyLoadResult,
    ConfigurationPolicyPage,
    load_configuration_policy_session,
)
from diffasaurus.ui.configuration_policy_presentation import build_page_model
from diffasaurus.ui.main_window import DiffasaurusWindow
from diffasaurus.ui.navigation_pages import (
    PAGE_CONFIGURATION_POLICIES,
    PAGE_COUNT,
    PAGE_RECENT_CHANGES,
    PAGE_SNAPSHOT_EXPLORER,
)
from tests.fixtures.configuration_policy_bundle import build_synthetic_bundle
from tests.fixtures.configuration_policy_comparison import (
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)

MONDAY_ID = "Intune_ConfigurationPolicies_20990106-090000"
TUESDAY_ID = "Intune_ConfigurationPolicies_20990107-090000"
WEDNESDAY_ID = "Intune_ConfigurationPolicies_20990108-090000"


def _policy_triplet(snapshot_id: str, policy_id: str, policy_name: str | None = None) -> tuple:
    name = policy_name or f"Policy {policy_id}"
    rel = f"Windows/Modern/Policy__{policy_id}.json"
    doc = build_basic_modern_policy_document(policy_id=policy_id, policy_name=name)
    row = build_modern_inventory_row(
        policy_id=policy_id,
        policy_name=name,
        json_relative_path=rel,
    )
    row["SnapshotId"] = snapshot_id
    return rel, doc, row


def _wait_for_page_load(page: ConfigurationPolicyPage, timeout_ms: int = 8000) -> None:
    deadline = time.time() + timeout_ms / 1000
    while page.progress.isVisible() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    page.thread_pool.waitForDone(3000)
    QApplication.processEvents()


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_nav_button_count_matches_stack_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=Path(directory),
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    self.assertEqual(len(window.nav_buttons), PAGE_COUNT)
                    self.assertEqual(window.stack.count(), PAGE_COUNT)
                    self.assertEqual(PAGE_CONFIGURATION_POLICIES, 8)
                    self.assertIs(window.configuration_policy_page, window.stack.widget(PAGE_CONFIGURATION_POLICIES))
                finally:
                    window.close()
                    window.thread_pool.waitForDone(2000)

    def test_snapshot_explorer_still_page_seven(self):
        self.assertEqual(PAGE_SNAPSHOT_EXPLORER, 7)
        self.assertEqual(PAGE_RECENT_CHANGES, 0)

    def test_configuration_policies_hides_family_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "diffasaurus.ui.main_window.get_active_reports_dir",
                return_value=Path(directory),
            ), patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                window = DiffasaurusWindow()
                try:
                    window.resize(1_200, 800)
                    window._apply_responsive_layout(1_200, 800)
                    window.show_page(3)
                    self.assertFalse(window.family_combo.isHidden())
                    window.show_page(PAGE_CONFIGURATION_POLICIES)
                    self.assertTrue(window.family_combo.isHidden())
                    window.show_page(PAGE_RECENT_CHANGES)
                    self.assertTrue(window.family_combo.isHidden())
                    window.show_page(3)
                    self.assertFalse(window.family_combo.isHidden())
                finally:
                    window.close()
                    window.thread_pool.waitForDone(2000)


class ConfigurationPolicyPageDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_empty_state_when_no_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = ConfigurationPolicyPage()
            page.activate(Path(tmp))
            _wait_for_page_load(page)
            self.assertEqual(page.snapshot_count, 0)
            self.assertIn("No Configuration Policy snapshots", page.empty_state.text())

    def test_single_snapshot_no_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[_policy_triplet(MONDAY_ID, "policy-1")],
            )
            page = ConfigurationPolicyPage()
            page.activate(root)
            _wait_for_page_load(page)
            self.assertEqual(page.snapshot_count, 1)
            self.assertIsNotNone(page._model)
            self.assertIsNone(page._model.previous_snapshot)
            self.assertEqual(page.card_changes.value.text(), "—")

    def test_three_snapshots_latest_selected_and_chronological_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[_policy_triplet(MONDAY_ID, "policy-1")],
            )
            build_comparison_bundle(
                root,
                snapshot_id=TUESDAY_ID,
                captured_at_utc="2099-01-07T09:00:00.0000000Z",
                policies=[_policy_triplet(TUESDAY_ID, "policy-1")],
            )
            build_comparison_bundle(
                root,
                snapshot_id=WEDNESDAY_ID,
                captured_at_utc="2099-01-08T09:00:00.0000000Z",
                policies=[_policy_triplet(WEDNESDAY_ID, "policy-1")],
            )
            discovery = discover_policy_snapshots(root)
            self.assertEqual([item.snapshot_id for item in discovery.snapshots], [MONDAY_ID, TUESDAY_ID, WEDNESDAY_ID])

            page = ConfigurationPolicyPage()
            page.activate(root)
            _wait_for_page_load(page)
            self.assertEqual(page._model.selected_snapshot.snapshot_id, WEDNESDAY_ID)
            self.assertEqual(page._model.previous_snapshot.snapshot_id, TUESDAY_ID)
            labels = [page.snapshot_combo.itemText(index) for index in range(page.snapshot_combo.count())]
            self.assertTrue(labels[0].startswith("08 Jan 2099"))

    def test_incomplete_snapshot_still_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[_policy_triplet(MONDAY_ID, "policy-1")],
                export_status="incomplete",
            )
            page = ConfigurationPolicyPage()
            page.activate(root)
            _wait_for_page_load(page)
            self.assertEqual(page.snapshot_count, 1)
            self.assertIn("Incomplete", page.snapshot_combo.itemText(0))


class ConfigurationPolicyInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_inventory_filters_and_policy_key_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_synthetic_bundle(root)
            page = ConfigurationPolicyPage()
            page.activate(root)
            _wait_for_page_load(page)
            self.assertGreater(page.inventory_table.rowCount(), 0)

            page.platform_filter.setCurrentText("Windows")
            page._apply_filters()
            for row in range(page.inventory_table.rowCount()):
                self.assertEqual(page.inventory_table.item(row, 1).text(), "Windows")

            page.platform_filter.setCurrentText("All")
            page.source_filter.setCurrentText("Modern")
            page._apply_filters()
            for row in range(page.inventory_table.rowCount()):
                self.assertEqual(page.inventory_table.item(row, 3).text(), "Modern")

            first_key = page.inventory_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
            page.inventory_table.selectRow(0)
            page._policy_selection_changed()
            self.assertEqual(page._selected_policy_key, first_key)


class ConfigurationPolicyRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_classic_explicitness_warning_and_false_zero_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_synthetic_bundle(root)
            page = ConfigurationPolicyPage()
            page.activate(root)
            _wait_for_page_load(page)
            normalized = page._model.normalized
            classic = next(
                policy for policy in normalized.policies if policy.export_source == "deviceConfigurations"
            )
            page._render_settings(classic)
            self.assertIn(CLASSIC_EXPLICITNESS_NOTE, page.classic_notice.text())
            self.assertGreater(page.classic_table.rowCount(), 0)
            values = {
                page.classic_table.item(row, 1).text()
                for row in range(page.classic_table.rowCount())
            }
            self.assertIn("False", values)

    def test_admx_incomplete_presentation_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_synthetic_bundle(root)
            page = ConfigurationPolicyPage()
            page.activate(root)
            _wait_for_page_load(page)
            admx = next(
                policy for policy in page._model.normalized.policies
                if policy.export_source == "groupPolicyConfigurations"
            )
            page._render_settings(admx)
            self.assertIn("incomplete", page.admx_notice.text().casefold())


class SnapshotSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_policy_key_persists_across_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy_id = "policy-stable-001"
            monday = build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[_policy_triplet(MONDAY_ID, policy_id, "Name Alpha")],
            )
            tuesday = build_comparison_bundle(
                root,
                snapshot_id=TUESDAY_ID,
                captured_at_utc="2099-01-07T09:00:00.0000000Z",
                policies=[_policy_triplet(TUESDAY_ID, policy_id, "Name Beta")],
            )
            page = ConfigurationPolicyPage()
            expected_key = f"configurationPolicies:{policy_id}"
            monday_result = load_configuration_policy_session(str(root), str(monday), 1)
            page._generation = 1
            page._apply_model(monday_result.model, 1)
            page._selected_policy_key = expected_key
            page._apply_filters()

            tuesday_result = load_configuration_policy_session(str(root), str(tuesday), 2)
            page._generation = 2
            page._apply_model(tuesday_result.model, 2)
            page._apply_filters()

            selected_items = page.inventory_table.selectedItems()
            self.assertTrue(selected_items)
            self.assertEqual(selected_items[0].data(Qt.ItemDataRole.UserRole), expected_key)
            self.assertIn("Beta", page.inventory_table.item(page.inventory_table.currentRow(), 0).text())


class StaleWorkerTests(unittest.TestCase):
    def test_stale_generation_ignored(self):
        model = build_page_model(
            snapshots=[],
            diagnostics_count=0,
            selected=None,
            previous=None,
            normalized=None,
            comparison=None,
        )
        page = ConfigurationPolicyPage()
        page._generation = 5
        stale = ConfigurationPolicyLoadResult(
            generation=4,
            report_dir="/tmp",
            snapshot_path=None,
            model=model,
        )
        page._load_succeeded(stale, generation=4)
        self.assertIsNone(page._model)


class VisualPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_modern_tree_uses_dark_alternate_background(self):
        page = ConfigurationPolicyPage()
        style = page.modern_tree.styleSheet().casefold()
        self.assertIn("alternate-background-color", style)
        self.assertIn("#152331", style)
        self.assertNotIn("#ffffff", style)
        self.assertNotIn("alternate-background-color: white", style)

    def test_inventory_name_column_uses_stretch(self):
        page = ConfigurationPolicyPage()
        header = page.inventory_table.horizontalHeader()
        self.assertEqual(header.sectionResizeMode(0), QHeaderView.ResizeMode.Stretch)
        self.assertFalse(header.stretchLastSection())

    def test_inventory_name_tooltip_set_on_populate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_synthetic_bundle(root)
            page = ConfigurationPolicyPage()
            result = load_configuration_policy_session(str(root), None, 1)
            page._generation = 1
            page._apply_model(result.model, 1)
            page._apply_filters()
            name_item = page.inventory_table.item(0, 0)
            self.assertIsNotNone(name_item)
            self.assertTrue(name_item.toolTip())
            self.assertGreater(page.inventory_table.columnWidth(0), 120)


class LoadSessionTests(unittest.TestCase):
    def test_first_snapshot_has_no_comparison_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_comparison_bundle(
                root,
                snapshot_id=MONDAY_ID,
                captured_at_utc="2099-01-06T09:00:00.0000000Z",
                policies=[_policy_triplet(MONDAY_ID, "policy-1")],
            )
            result = load_configuration_policy_session(str(root), None, 1)
            self.assertIsNone(result.model.comparison_error)
            self.assertIsNone(result.model.previous_snapshot)


if __name__ == "__main__":
    unittest.main()
