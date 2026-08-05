import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_path, source_key
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_schema import open_connection
from diffasaurus.core.entity.index_sync import run_sync
from tests.fixtures.entity_index_generator import generate_multi_family_manifest, write_report


class EntityIndexSyncTests(unittest.TestCase):
    def _sync(self, root: Path, *, cold: bool = True, db_path: Path | None = None):
        path = db_path or entity_index_path(root)
        os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(path)
        try:
            return run_sync(root, cold=cold, db_path=path)
        finally:
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)

    def test_unsupported_family_is_tracked_without_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate_multi_family_manifest(root, snapshots_per_family=1)
            result = self._sync(root)
            self.assertIn(result.status, ("complete", "completed_with_errors"))
            connection = open_connection(entity_index_path(root), readonly=True)
            unsupported = connection.execute(
                "SELECT COUNT(*) AS count FROM unsupported_files"
            ).fetchone()["count"]
            self.assertEqual(int(unsupported), 1)
            connection.close()

    def test_unchanged_resync_parses_zero_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            first = self._sync(root)
            self.assertEqual(first.parsed, 1)
            second = self._sync(root, cold=False)
            self.assertEqual(second.parsed, 0)
            self.assertEqual(second.reused, 1)

    def test_single_modified_file_reparses_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Entra_Users_Properties_20260701-010000.csv"
            write_report(
                path,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            self._sync(root)
            write_report(
                path,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada Updated"}],
            )
            result = self._sync(root, cold=False)
            self.assertEqual(result.parsed, 1)
            self.assertEqual(result.reused, 0)

    def test_new_and_deleted_files_are_incremental(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "Entra_Users_Properties_20260701-010000.csv"
            second_path = root / "Entra_Users_Properties_20260801-010000.csv"
            write_report(
                first_path,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            self._sync(root)
            write_report(
                second_path,
                [{"Id": "user-2", "UPN": "other@example.com", "DisplayName": "Other"}],
            )
            added = self._sync(root, cold=False)
            self.assertEqual(added.parsed, 1)
            first_path.unlink()
            removed = self._sync(root, cold=False)
            self.assertEqual(removed.parsed, 0)

    def test_malformed_replacement_preserves_previous_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Entra_Users_Properties_20260701-010000.csv"
            write_report(
                path,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            self._sync(root)
            path.write_text("Foo,Bar\n1,2\n", encoding="utf-8-sig")
            result = self._sync(root, cold=False)
            self.assertEqual(result.failed, 1)
            repo = EntityIndexRepository.open(root)
            assert repo is not None
            record = repo.search("ada@example.com", "user")
            self.assertEqual(len(record.matches), 1)
            repo.close()

    def test_two_report_sources_remain_isolated(self):
        with tempfile.TemporaryDirectory() as a_dir, tempfile.TemporaryDirectory() as b_dir:
            root_a = Path(a_dir)
            root_b = Path(b_dir)
            write_report(
                root_a / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-a", "UPN": "a@example.com", "DisplayName": "A"}],
            )
            write_report(
                root_b / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-b", "UPN": "b@example.com", "DisplayName": "B"}],
            )
            db_a = root_a / f"{source_key(root_a)}.sqlite3"
            db_b = root_b / f"{source_key(root_b)}.sqlite3"
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_a)
            run_sync(root_a, cold=True, db_path=db_a)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_b)
            run_sync(root_b, cold=True, db_path=db_b)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
            repo_a = EntityIndexRepository.open(root_a, db_path=db_a)
            repo_b = EntityIndexRepository.open(root_b, db_path=db_b)
            assert repo_a is not None and repo_b is not None
            self.assertEqual(len(repo_a.search("a@example.com", "user").matches), 1)
            self.assertEqual(len(repo_b.search("b@example.com", "user").matches), 1)
            self.assertEqual(len(repo_a.search("b@example.com", "user").matches), 0)
            repo_a.close()
            repo_b.close()


if __name__ == "__main__":
    unittest.main()
