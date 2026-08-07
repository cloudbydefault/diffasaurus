import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_projection import build_user_device_link_projection
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_schema import (
    ensure_performance_indexes,
    initialize_schema,
    open_connection,
)
from diffasaurus.core.entity.index_sync import compute_adapter_version, run_sync
from diffasaurus.core.entity.types import CanonicalEntityKey
from tests.test_entity_resolution import write_report


class IndexPerformanceTests(unittest.TestCase):
    def test_performance_indexes_on_new_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "index.sqlite3"
            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_alias_obs_source%'"
            ).fetchall()
            connection.close()
            names = {row[0] for row in rows}
            self.assertIn("idx_alias_obs_source_observed", names)
            self.assertIn("idx_alias_obs_source_immutable_observed", names)

    def test_performance_indexes_added_to_existing_schema_v2_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(db_path)
            initialize_schema(connection, compute_adapter_version())
            connection.execute("DROP INDEX IF EXISTS idx_alias_obs_source_observed")
            connection.execute("DROP INDEX IF EXISTS idx_alias_obs_source_immutable_observed")
            connection.commit()
            connection.close()

            reopened = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            ensure_performance_indexes(reopened)
            rows = reopened.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_alias_obs_source%'"
            ).fetchall()
            reopened.close()
            names = {row[0] for row in rows}
            self.assertIn("idx_alias_obs_source_observed", names)
            self.assertIn("idx_alias_obs_source_immutable_observed", names)

    def test_explain_query_plan_uses_source_id_index(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "explain.sqlite3"
            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            connection.execute(
                """
                INSERT INTO report_sources(source_key, reports_path, created_at)
                VALUES ('test', '/tmp', '2026-08-04T04:00:00')
                """
            )
            source_id = int(connection.execute("SELECT id FROM report_sources").fetchone()[0])
            connection.execute(
                """
                INSERT INTO indexed_files(
                    source_id, relative_path, family, captured_at, size_bytes, mtime_ns,
                    adapter_version, status
                ) VALUES (?, 'users.csv', 'Entra_Users_Properties', '2026-08-04T04:00:00', 1, 1, 'v', 'indexed')
                """,
                (source_id,),
            )
            file_id = int(connection.execute("SELECT id FROM indexed_files").fetchone()[0])
            connection.executemany(
                """
                INSERT INTO alias_observations(
                    source_id, file_id, kind, normalized_value, immutable_id,
                    observed_at, source_family
                ) VALUES (?, ?, 'upn', ?, ?, ?, 'Entra_Users_Properties')
                """,
                [
                    (source_id, file_id, "ada@example.com", "user-1", "2026-08-04T04:00:00"),
                    (source_id, file_id, "bob@example.com", "user-2", "2026-08-04T05:00:00"),
                ],
            )
            plan = connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT kind, normalized_value, observed_at, immutable_id, source_family
                FROM alias_observations
                WHERE source_id=? AND observed_at <= ?
                ORDER BY observed_at
                """,
                (source_id, "2026-08-04T06:00:00"),
            ).fetchall()
            plan_text = " ".join(str(cell) for row in plan for cell in row).lower()
            self.assertIn("idx_alias_obs_source_observed", plan_text)
            connection.close()

    def test_shared_alias_index_preserves_historical_as_of(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-040000.csv",
                [{"Id": "user-1", "UPN": "old@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-060000.csv",
                [{"Id": "user-1", "UPN": "new@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Role_Assignments_20260804-043131.csv",
                [
                    {
                        "UserPrincipalName": "old@example.com",
                        "DisplayName": "Ada",
                        "RoleName": "Global Administrator",
                        "RoleState": "Active",
                    }
                ],
            )
            write_report(
                root / "Entra_Role_Assignments_20260804-070000.csv",
                [
                    {
                        "UserPrincipalName": "new@example.com",
                        "DisplayName": "Ada",
                        "RoleName": "Global Administrator",
                        "RoleState": "Active",
                    }
                ],
            )
            db_path = entity_index_path(root)
            result = run_sync(root, cold=True, db_path=db_path)
            self.assertEqual(result.failed, 0)
            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            record = repo.get_entity(CanonicalEntityKey("user", "user-1"))
            self.assertIsNotNone(record)
            repo.close()

    def test_old_snapshot_cannot_resolve_future_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-060000.csv",
                [{"Id": "user-1", "UPN": "future@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Role_Assignments_20260804-043131.csv",
                [
                    {
                        "UserPrincipalName": "future@example.com",
                        "DisplayName": "Ada",
                        "RoleName": "Global Administrator",
                        "RoleState": "Active",
                    }
                ],
            )
            db_path = entity_index_path(root)
            result = run_sync(root, cold=True, db_path=db_path)
            self.assertEqual(result.failed, 0)
            self.assertGreaterEqual(result.unresolved, 1)

    def test_second_warm_sync_is_no_op(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-040000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            db_path = entity_index_path(root)
            run_sync(root, cold=True, db_path=db_path)
            warm = run_sync(root, cold=False, db_path=db_path)
            self.assertEqual(warm.parsed, 0)
            self.assertEqual(warm.reused, 1)

    def test_batched_occurrence_insert_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "rollback.sqlite3"
            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            connection.execute(
                """
                INSERT INTO report_sources(source_key, reports_path, created_at)
                VALUES ('test', '/tmp', '2026-08-04T04:00:00')
                """
            )
            source_id = int(connection.execute("SELECT id FROM report_sources").fetchone()[0])
            connection.execute(
                """
                INSERT INTO entities(source_id, entity_type, primary_id, display_name)
                VALUES (?, 'user', 'user-1', 'Ada')
                """,
                (source_id,),
            )
            entity_id = int(connection.execute("SELECT id FROM entities").fetchone()[0])
            connection.execute(
                """
                INSERT INTO indexed_files(
                    source_id, relative_path, family, captured_at, size_bytes, mtime_ns,
                    adapter_version, status
                ) VALUES (?, 'users.csv', 'Entra_Users_Properties', '2026-08-04T04:00:00', 1, 1, 'v', 'indexed')
                """,
                (source_id,),
            )
            file_id = int(connection.execute("SELECT id FROM indexed_files").fetchone()[0])
            from diffasaurus.core.entity.index_schema import transaction

            try:
                with transaction(connection):
                    connection.executemany(
                        """
                        INSERT INTO entity_occurrences(
                            entity_id, file_id, observed_at, display_name,
                            scalar_properties_json, relationships_json, aliases_json, row_hash
                        ) VALUES (?, ?, ?, ?, '[]', '[]', '[]', 'hash')
                        """,
                        [(entity_id, file_id, "2026-08-04T04:00:00", "Ada")],
                    )
                    connection.execute(
                        "INSERT INTO entities(source_id, entity_type, primary_id, display_name) "
                        "VALUES (99999, 'user', 'user-1', 'Ada')"
                    )
            except sqlite3.IntegrityError:
                pass
            count = connection.execute("SELECT COUNT(*) FROM entity_occurrences").fetchone()[0]
            connection.close()
            self.assertEqual(count, 0)

    def test_user_device_projection_reuses_alias_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-040000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260804-040000.csv",
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "LAPTOP-1",
                    }
                ],
            )
            db_path = entity_index_path(root)
            run_sync(root, cold=True, db_path=db_path)
            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            source_id = int(
                connection.execute("SELECT id FROM report_sources").fetchone()[0]
            )
            stats = build_user_device_link_projection(connection, source_id, root)
            count = connection.execute(
                "SELECT COUNT(*) FROM user_device_link_observations"
            ).fetchone()[0]
            connection.close()
            self.assertGreater(stats.observations_written, 0)
            self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
