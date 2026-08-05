import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from diffasaurus.core.entity.bindings import AliasBindingIndex
from diffasaurus.core.entity.history import classify_row_entity_key, row_entity_key
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.registry import ADAPTERS_BY_FAMILY
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import scan_report_history
from tests.test_entity_resolution import write_report


class EntityIndexBindingTests(unittest.TestCase):
    def _role_adapter(self):
        adapter = ADAPTERS_BY_FAMILY["Entra_Role_Assignments"]
        assert adapter is not None
        return adapter

    def test_role_assignments_upn_only_rows_index_without_canonical_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-040000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Role_Assignments_20260804-043131.csv",
                [
                    {
                        "UserPrincipalName": "ada@example.com",
                        "DisplayName": "Ada",
                        "RoleName": "Global Administrator",
                        "RoleState": "Active",
                    }
                ],
            )
            db_path = entity_index_path(root)
            result = run_sync(root, cold=True, db_path=db_path)
            self.assertIn(result.status, ("complete", "completed_with_errors"))
            self.assertEqual(result.failed, 0)
            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            record = repo.get_entity(CanonicalEntityKey("user", "user-1"))
            self.assertIsNotNone(record)
            repo.close()

    def test_binding_at_0400_resolves_role_row_at_0405(self):
        adapter = self._role_adapter()
        index = AliasBindingIndex()
        index.record(
            "upn",
            "ada@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        row = {
            "UserPrincipalName": "ada@example.com",
            "DisplayName": "Ada",
            "RoleName": "Global Administrator",
            "RoleState": "Active",
        }
        key, reason = classify_row_entity_key(
            adapter,
            row,
            datetime(2026, 8, 4, 4, 5, 0),
            index,
        )
        self.assertIsNone(reason)
        assert key is not None
        self.assertEqual(key.primary_id, "user-1")

    def test_future_binding_is_rejected(self):
        adapter = self._role_adapter()
        index = AliasBindingIndex()
        index.record(
            "upn",
            "ada@example.com",
            datetime(2026, 8, 4, 5, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        row = {
            "UserPrincipalName": "ada@example.com",
            "DisplayName": "Ada",
            "RoleName": "Global Administrator",
            "RoleState": "Active",
        }
        key, reason = classify_row_entity_key(
            adapter,
            row,
            datetime(2026, 8, 4, 4, 5, 0),
            index,
        )
        self.assertIsNone(key)
        self.assertEqual(reason, "unbound_upn")

    def test_upn_rename_resolves_historically(self):
        adapter = self._role_adapter()
        index = AliasBindingIndex()
        index.record(
            "upn",
            "old@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            "new@example.com",
            datetime(2026, 8, 4, 6, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        old_row = {
            "UserPrincipalName": "old@example.com",
            "DisplayName": "Ada",
            "RoleName": "Global Administrator",
            "RoleState": "Active",
        }
        key, reason = classify_row_entity_key(
            adapter,
            old_row,
            datetime(2026, 8, 4, 4, 30, 0),
            index,
        )
        self.assertIsNone(reason)
        assert key is not None
        self.assertEqual(key.primary_id, "user-1")

    def test_recycled_ambiguous_upn_is_not_guessed(self):
        adapter = self._role_adapter()
        index = AliasBindingIndex()
        index.record(
            "upn",
            "shared@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-1",
            "Entra_Users_Properties",
        )
        index.record(
            "upn",
            "shared@example.com",
            datetime(2026, 8, 4, 4, 0, 0),
            "user-2",
            "Entra_Users_Properties",
        )
        row = {
            "UserPrincipalName": "shared@example.com",
            "DisplayName": "Shared",
            "RoleName": "Reader",
            "RoleState": "Active",
        }
        key, reason = classify_row_entity_key(
            adapter,
            row,
            datetime(2026, 8, 4, 4, 5, 0),
            index,
        )
        self.assertIsNone(key)
        self.assertEqual(reason, "ambiguous_upn")

    def test_unresolved_row_does_not_fail_valid_rows_in_same_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-040000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Role_Assignments_20260804-043131.csv",
                [
                    {
                        "UserPrincipalName": "unknown@example.com",
                        "DisplayName": "Unknown",
                        "RoleName": "Reader",
                        "RoleState": "Active",
                    },
                    {
                        "UserPrincipalName": "ada@example.com",
                        "DisplayName": "Ada",
                        "RoleName": "Global Administrator",
                        "RoleState": "Active",
                    },
                ],
            )
            db_path = entity_index_path(root)
            result = run_sync(root, cold=True, db_path=db_path)
            self.assertEqual(result.failed, 0)
            self.assertGreaterEqual(result.unresolved, 1)
            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            self.assertIsNotNone(repo.get_entity(CanonicalEntityKey("user", "user-1")))
            repo.close()


if __name__ == "__main__":
    unittest.main()
