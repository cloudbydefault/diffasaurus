from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.entity.index_lock import EntityIndexLockError, acquire_entity_index_lock
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_progress import SyncProgressEvent
from diffasaurus.core.entity.index_projection import (
    USER_DEVICE_LINK_PROJECTION_VERSION,
    build_user_device_link_projection,
    user_device_link_projection_version,
    user_device_links_need_build,
    user_device_links_need_build_at_path,
)
from diffasaurus.core.entity.index_schema import metadata_value, open_connection
from diffasaurus.core.entity.index_sync import compute_adapter_version, run_sync
from tests.fixtures.entity_index_generator import write_report


def _build_index(root: Path) -> Path:
    db_path = entity_index_path(root)
    os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
    run_sync(root, cold=True, db_path=db_path)
    os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
    return db_path


def _fixture(root: Path) -> None:
    write_report(
        root / "Entra_Users_Properties_20260701-010000.csv",
        [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
    )
    write_report(
        root / "Intune_ManagedDevices_Compliance_20260715-010000.csv",
        [
            {
                "UserId": "user-1",
                "AzureADDeviceId": "aad-1",
                "ManagedDeviceId": "md-1",
                "DeviceName": "Laptop",
            }
        ],
    )


class UserDeviceProjectionTests(unittest.TestCase):
    def test_projection_repair_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            db_path = _build_index(root)
            connection = open_connection(db_path, readonly=False, adapter_version=compute_adapter_version())
            try:
                source_id = int(
                    connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
                )
                self.assertEqual(
                    user_device_link_projection_version(connection),
                    USER_DEVICE_LINK_PROJECTION_VERSION,
                )
                first = build_user_device_link_projection(connection, source_id, root)
                second = build_user_device_link_projection(connection, source_id, root)
                self.assertEqual(first.projection_version, second.projection_version)
                self.assertEqual(first.observations_written, second.observations_written)
                self.assertFalse(user_device_links_need_build(connection))
            finally:
                connection.close()

    def test_projection_repair_rollback_leaves_no_partial_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            db_path = _build_index(root)
            connection = open_connection(db_path, readonly=False, adapter_version=compute_adapter_version())
            try:
                source_id = int(
                    connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
                )
                before_version = user_device_link_projection_version(connection)
                before_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM user_device_link_observations"
                ).fetchone()["count"]
                connection.execute(
                    "DELETE FROM metadata WHERE key='user_device_link_projection_version'"
                )
                connection.commit()

                with patch(
                    "diffasaurus.core.entity.index_projection.replace_file_user_device_link_observations",
                    side_effect=RuntimeError("injected projection failure"),
                ):
                    with self.assertRaises(RuntimeError):
                        build_user_device_link_projection(connection, source_id, root)

                after_version = user_device_link_projection_version(connection)
                after_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM user_device_link_observations"
                ).fetchone()["count"]
                self.assertEqual(after_version, 0)
                # Failed rebuild rolled back the DELETE of prior observations.
                self.assertEqual(after_count, before_count)
                self.assertNotEqual(before_version, 0)
            finally:
                connection.close()

    def test_process_lock_prevents_concurrent_builders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            db_path = _build_index(root)
            with acquire_entity_index_lock(db_path, "tenant-key"):
                with self.assertRaises(EntityIndexLockError):
                    acquire_entity_index_lock(db_path, "tenant-key")

    def test_worker_reports_user_device_link_progress_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            events: list[SyncProgressEvent] = []

            def progress(event: SyncProgressEvent) -> None:
                events.append(event)

            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
            run_sync(root, cold=True, progress=progress)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
            phases = [event.phase for event in events]
            self.assertIn("building_user_device_links", phases)
            labels = [event.label for event in events if event.phase == "building_user_device_links"]
            self.assertTrue(
                any("Building historical user-device links" in label for label in labels)
            )

    def test_warm_startup_after_repair_does_not_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            db_path = _build_index(root)
            self.assertFalse(user_device_links_need_build_at_path(db_path))
            events: list[SyncProgressEvent] = []

            def progress(event: SyncProgressEvent) -> None:
                events.append(event)

            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            run_sync(root, cold=False, db_path=db_path, progress=progress)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
            phases = [event.phase for event in events]
            self.assertNotIn("building_user_device_links", phases)

    def test_open_existing_does_not_block_on_user_device_projection(self):
        """user_device_links_need_build_at_path is a cheap metadata check only."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            db_path = _build_index(root)
            connection = open_connection(db_path, readonly=False, adapter_version=compute_adapter_version())
            try:
                connection.execute(
                    "DELETE FROM metadata WHERE key='user_device_link_projection_version'"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertTrue(user_device_links_need_build_at_path(db_path))
            # Metadata-only check must not publish a new projection version.
            connection = open_connection(db_path, readonly=True)
            try:
                self.assertIsNone(metadata_value(connection, "user_device_link_projection_version"))
            finally:
                connection.close()
            self.assertTrue(user_device_links_need_build_at_path(db_path))


if __name__ == "__main__":
    unittest.main()
