import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from diffasaurus.core.entity.index_lock import (
    EntityIndexLockError,
    acquire_entity_index_lock,
    check_lock_holder,
    lock_path_for_db,
    read_lock_info,
)
from diffasaurus.core.entity.index_paths import cleanup_index_files


class EntityIndexLockTests(unittest.TestCase):
    def test_different_sources_have_independent_locks(self):
        with tempfile.TemporaryDirectory() as left_dir, tempfile.TemporaryDirectory() as right_dir:
            left_db = Path(left_dir) / "left.sqlite3"
            right_db = Path(right_dir) / "right.sqlite3"
            with acquire_entity_index_lock(left_db, "left-key"):
                with acquire_entity_index_lock(right_db, "right-key"):
                    self.assertIsNotNone(check_lock_holder(left_db))
                    self.assertIsNotNone(check_lock_holder(right_db))

    def test_second_writer_is_rejected_for_same_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tenant.sqlite3"
            with acquire_entity_index_lock(db_path, "tenant-key"):
                with self.assertRaises(EntityIndexLockError):
                    acquire_entity_index_lock(db_path, "tenant-key")

    def test_stale_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tenant.sqlite3"
            lock_path = lock_path_for_db(db_path)
            lock_path.write_text(
                '{"pid": 999999999, "source_key": "tenant-key", "db_path": "x", "started_at": "t"}',
                encoding="utf-8",
            )
            with acquire_entity_index_lock(db_path, "tenant-key") as lock:
                self.assertTrue(lock.lock_path.is_file())
            self.assertFalse(lock_path.is_file())

    def test_lock_removed_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tenant.sqlite3"
            lock = acquire_entity_index_lock(db_path, "tenant-key")
            lock_path = lock.lock_path
            lock.release()
            self.assertFalse(lock_path.is_file())

    def test_cold_cleanup_does_not_run_without_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tenant.sqlite3"
            temp_path = db_path.with_suffix(".sqlite3.tmp")
            temp_path.write_text("keep", encoding="utf-8")
            (Path(f"{temp_path}-wal")).write_text("wal", encoding="utf-8")
            holder = check_lock_holder(db_path)
            if holder is None:
                cleanup_index_files(temp_path)
            self.assertFalse(temp_path.exists())

    def test_second_process_does_not_delete_active_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tenant.sqlite3"
            temp_path = db_path.with_suffix(".sqlite3.tmp")
            with acquire_entity_index_lock(db_path, "tenant-key", cold=True):
                temp_path.write_text("active-build", encoding="utf-8")
                with self.assertRaises(EntityIndexLockError):
                    acquire_entity_index_lock(db_path, "tenant-key", cold=True)
                self.assertTrue(temp_path.is_file())
                self.assertEqual(temp_path.read_text(encoding="utf-8"), "active-build")

    def test_lock_records_pid_and_source_key(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tenant.sqlite3"
            with acquire_entity_index_lock(db_path, "tenant-key", cold=True):
                info = read_lock_info(lock_path_for_db(db_path))
                assert info is not None
                self.assertEqual(info.source_key, "tenant-key")
                self.assertEqual(info.pid, os.getpid())
                self.assertTrue(info.cold)


if __name__ == "__main__":
    unittest.main()
