import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.types import CanonicalEntityKey
from tests.fixtures.entity_index_generator import write_report


class EntityIndexRepositoryTests(unittest.TestCase):
    def _build(self, root: Path) -> EntityIndexRepository:
        os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
        run_sync(root, cold=True)
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
        repo = EntityIndexRepository.open(root)
        assert repo is not None
        return repo

    def _write_search_fixture(self, root: Path) -> None:
        write_report(
            root / "Entra_Users_Properties_20260701-010000.csv",
            [
                {
                    "Id": "guid-duthil",
                    "UPN": "jonathan.duthil@example.com",
                    "DisplayName": "Jonathan Duthil",
                },
                {
                    "Id": "guid-adnet",
                    "UPN": "adm_adnet@example.com",
                    "DisplayName": "ADM ADNET",
                },
                {
                    "Id": "guid-axel",
                    "UPN": "adm_axel@example.com",
                    "DisplayName": "ADM AXEL",
                },
            ],
        )

    def test_deleted_entity_remains_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "old@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-2",
                        "UPN": "other@example.com",
                        "DisplayName": "Other",
                    }
                ],
            )
            repo = self._build(root)
            by_id = repo.search("user-1", "user")
            self.assertEqual(len(by_id.matches), 1)
            self.assertFalse(by_id.matches[0].present_in_latest)
            by_alias = repo.search("old@example.com", "user")
            self.assertEqual(len(by_alias.matches), 1)
            repo.close()

    def test_exact_id_and_prefix_autocomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada Lovelace"}],
            )
            repo = self._build(root)
            entity = repo.get_entity(CanonicalEntityKey("user", "user-1"))
            self.assertIsNotNone(entity)
            suggestions = repo.autocomplete_prefix("ada", "user")
            self.assertTrue(any("ada" in value.lower() for value in suggestions))
            repo.close()

    def test_search_capabilities_exposed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            repo = self._build(root)
            capabilities = repo.search_capabilities()
            self.assertTrue(capabilities.exact_id)
            self.assertTrue(capabilities.prefix_autocomplete)
            repo.close()

    def test_search_duthil_excludes_unrelated_adm_users(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_search_fixture(root)
            repo = self._build(root)
            result = repo.search("duthil", "user")
            self.assertEqual(len(result.matches), 1)
            self.assertEqual(result.matches[0].key.primary_id, "guid-duthil")
            for record in result.matches:
                haystack = " ".join(
                    [
                        record.key.primary_id,
                        record.display_name,
                        *(alias.value for alias in record.aliases),
                    ]
                ).casefold()
                self.assertIn("duthil", haystack)
            repo.close()

    def test_exact_upn_is_first_and_case_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_search_fixture(root)
            repo = self._build(root)
            result = repo.search("Jonathan.Duthil@Example.com", "user")
            self.assertGreaterEqual(len(result.matches), 1)
            self.assertEqual(result.matches[0].key.primary_id, "guid-duthil")
            repo.close()

    def test_partial_upn_prefix_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_search_fixture(root)
            repo = self._build(root)
            result = repo.search("jonathan.duth", "user")
            self.assertEqual(len(result.matches), 1)
            self.assertEqual(result.matches[0].key.primary_id, "guid-duthil")
            repo.close()

    def test_unknown_query_returns_zero_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_search_fixture(root)
            repo = self._build(root)
            result = repo.search("zzzz-not-present", "user")
            self.assertEqual(result.matches, ())
            repo.close()

    def test_search_cache_invalidated_on_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_search_fixture(root)
            repo = self._build(root)
            first = repo.search("duthil", "user")
            repo.invalidate_caches()
            second = repo.search("duthil", "user")
            self.assertEqual(first.matches[0].key, second.matches[0].key)
            repo.close()

    def test_search_does_not_reuse_other_entity_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260701-010000.csv",
                [
                    {
                        "AzureADDeviceId": "aad-ada",
                        "ManagedDeviceId": "md-ada",
                        "DeviceName": "ada-laptop",
                        "SerialNumber": "SN-ADA",
                        "ComplianceState": "Compliant",
                    }
                ],
            )
            repo = self._build(root)
            user_result = repo.search("ada", "user")
            device_result = repo.search("ada", "device")
            self.assertEqual(user_result.matches[0].key.entity_type, "user")
            self.assertEqual(device_result.matches[0].key.entity_type, "device")
            repo.close()

    def test_period_changes_from_background_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada Updated"}],
            )
            repo = self._build(root)
            key = CanonicalEntityKey("user", "user-1")

            def _run_period_changes():
                return repo.period_changes(key, timedelta(days=30))

            with ThreadPoolExecutor(max_workers=1) as executor:
                changes = executor.submit(_run_period_changes).result()
            self.assertIsNotNone(changes)
            repo.close()

    def test_reconstruct_state_from_background_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            repo = self._build(root)
            key = CanonicalEntityKey("user", "user-1")
            target = datetime(2026, 7, 1, 1, 0, 0)

            def _run_reconstruct():
                return repo.reconstruct_state(key, target)

            with ThreadPoolExecutor(max_workers=1) as executor:
                state = executor.submit(_run_reconstruct).result()
            self.assertEqual(state.key, key)
            repo.close()


if __name__ == "__main__":
    unittest.main()
