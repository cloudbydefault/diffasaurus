"""Shared helpers for Configuration Policy acceptance tests."""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.configuration_policies.integration import POLICY_SESSION_CACHE
from diffasaurus.core.report_history import ReportSnapshot, scan_report_index
from diffasaurus.ui.main_window import DiffasaurusWindow
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer
from tests.fixtures.configuration_policy_comparison import (
    build_basic_modern_policy_document,
    build_comparison_bundle,
    build_modern_inventory_row,
)

MONDAY_ID = "Intune_ConfigurationPolicies_20990106-090000"
TUESDAY_ID = "Intune_ConfigurationPolicies_20990107-090000"
WEDNESDAY_ID = "Intune_ConfigurationPolicies_20990108-090000"


def anchor_path(root: Path, snapshot_id: str) -> Path:
    return root / f"{snapshot_id}.csv"


def write_anchor(
    root: Path,
    snapshot_id: str,
    captured_at: str,
    *,
    extra_rows: str = "",
) -> Path:
    path = anchor_path(root, snapshot_id)
    path.write_text(
        "SnapshotId,CapturedAtUtc,PolicyId,PolicyName\n"
        f"{snapshot_id},{captured_at},policy-1,Synthetic\n"
        f"{extra_rows}",
        encoding="utf-8-sig",
    )
    return path


def policy_triplet(snapshot_id: str, policy_id: str = "policy-1", policy_name: str = "Synthetic"):
    rel = f"Windows/Modern/P__{policy_id}.json"
    doc = build_basic_modern_policy_document(policy_id=policy_id, policy_name=policy_name)
    row = build_modern_inventory_row(
        policy_id=policy_id,
        policy_name=policy_name,
        json_relative_path=rel,
    )
    return rel, doc, row


def build_two_snapshot_root(root: Path) -> list[ReportSnapshot]:
    policy = policy_triplet(MONDAY_ID)
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
        write_anchor(root, snapshot_id, captured)
    POLICY_SESSION_CACHE.invalidate(root)
    return scan_report_index(root)[CONFIGURATION_POLICY_FAMILY]


def wait_for_compare(window: DiffasaurusWindow, timeout_s: float = 8.0) -> None:
    deadline = time.time() + timeout_s
    while window.compare_button.text() == "Comparing…" and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    window.thread_pool.waitForDone(int(timeout_s * 1000))
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.02)


def wait_for_explorer(explorer: SnapshotExplorer, timeout_s: float = 8.0) -> None:
    deadline = time.time() + timeout_s
    while explorer.progress.isVisible() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    explorer.thread_pool.waitForDone(int(timeout_s * 1000))
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.02)


def drain_qt(*, thread_pools: list | None = None, events: int = 20) -> None:
    for pool in thread_pools or []:
        pool.waitForDone(5000)
    for _ in range(events):
        QApplication.processEvents()
        time.sleep(0.02)


def close_main_window(window: DiffasaurusWindow) -> None:
    window.close()
    drain_qt(thread_pools=[window.thread_pool, window._entity_index_pool])
    window.deleteLater()
    drain_qt()


def close_explorer(explorer: SnapshotExplorer) -> None:
    explorer.close()
    drain_qt(thread_pools=[explorer.thread_pool])
    explorer.deleteLater()
    drain_qt()


@contextmanager
def isolated_main_window(*, report_dir: Path | None = None):
    root = report_dir or Path(tempfile.mkdtemp())
    with (
        patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=root),
        patch.object(DiffasaurusWindow, "refresh_history", lambda self: None),
        patch("diffasaurus.ui.main_window.persistent_entity_index_enabled", return_value=False),
        patch.object(DiffasaurusWindow, "_request_persistent_entity_sync", lambda *args, **kwargs: None),
    ):
        window = DiffasaurusWindow()
        window.report_dir = root
        try:
            yield window
        finally:
            close_main_window(window)


@contextmanager
def isolated_snapshot_explorer():
    explorer = SnapshotExplorer()
    try:
        yield explorer
    finally:
        close_explorer(explorer)
