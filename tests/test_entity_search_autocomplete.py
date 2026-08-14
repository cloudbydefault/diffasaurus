import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.ui.entity_search import EntitySelectorPanel
from tests.fixtures.entity_index_generator import write_report


class EntitySearchAutocompleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self, root: Path) -> EntityIndexRepository:
        os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
        run_sync(root, cold=True)
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
        repo = EntityIndexRepository.open(root)
        assert repo is not None
        return repo

    def test_empty_prefix_returns_no_suggestions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            repo = self._build(root)
            self.assertEqual(repo.autocomplete_prefix("", "user"), [])
            self.assertEqual(repo.autocomplete_prefix("   ", "user"), [])
            repo.close()

    def test_user_prefix_autocomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {"Id": "user-1", "UPN": "ada.lovelace@example.com", "DisplayName": "Ada Lovelace"},
                    {"Id": "user-2", "UPN": "jane.doe@example.com", "DisplayName": "Jane"},
                ],
            )
            repo = self._build(root)
            suggestions = repo.autocomplete_prefix("ada", "user")
            self.assertTrue(any("ada" in value.casefold() for value in suggestions))
            repo.close()

    def test_device_prefix_autocomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260701-010000.csv",
                [
                    {
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Surface-Laptop-7",
                        "SerialNumber": "SN-SURF-001",
                        "ComplianceState": "Compliant",
                    }
                ],
            )
            repo = self._build(root)
            by_name = repo.autocomplete_prefix("surf", "device")
            by_serial = repo.autocomplete_prefix("sn-surf", "device")
            self.assertTrue(any("surface" in value.casefold() for value in by_name))
            self.assertTrue(any("sn-surf" in value.casefold() for value in by_serial))
            repo.close()

    def test_shared_mailbox_prefix_autocomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Exchange_SharedMailboxes_20260801-010000.csv",
                [
                    {
                        "DisplayName": "Finance",
                        "PrimarySmtpAddress": "finance@example.com",
                        "Alias": "finance",
                        "ExternalDirectoryObjectId": "mbx-1",
                        "HasForwarding": "False",
                        "ForwardingSmtpAddress": "",
                    }
                ],
            )
            repo = self._build(root)
            suggestions = repo.autocomplete_prefix("fin", "shared_mailbox")
            self.assertTrue(any("finance" in value.casefold() for value in suggestions))
            repo.close()

    def test_entity_type_isolation(self):
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
            user_suggestions = repo.autocomplete_prefix("ada", "user")
            device_suggestions = repo.autocomplete_prefix("ada", "device")
            self.assertTrue(user_suggestions)
            self.assertTrue(device_suggestions)
            self.assertTrue(all("@" in value or "ada" in value.casefold() for value in user_suggestions))
            self.assertTrue(all("ada" in value.casefold() for value in device_suggestions))
            repo.close()

    def test_case_insensitive_prefix_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "Ada.Lovelace@Example.com", "DisplayName": "Ada Lovelace"}],
            )
            repo = self._build(root)
            lower = repo.autocomplete_prefix("ada", "user")
            mixed = repo.autocomplete_prefix("ADA", "user")
            self.assertEqual(lower, mixed)
            repo.close()

    def test_historical_upn_still_suggested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "old.upn@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [{"Id": "user-1", "UPN": "new.upn@example.com", "DisplayName": "Ada"}],
            )
            repo = self._build(root)
            suggestions = repo.autocomplete_prefix("old.upn", "user")
            self.assertIn("old.upn@example.com", suggestions)
            repo.close()

    def test_shared_mailbox_historical_smtp_still_suggested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Exchange_SharedMailboxes_20260701-010000.csv",
                [
                    {
                        "DisplayName": "Finance",
                        "PrimarySmtpAddress": "old-finance@example.com",
                        "Alias": "finance",
                        "ExternalDirectoryObjectId": "mbx-1",
                        "HasForwarding": "False",
                        "ForwardingSmtpAddress": "",
                    }
                ],
            )
            write_report(
                root / "Exchange_SharedMailboxes_20260801-010000.csv",
                [
                    {
                        "DisplayName": "Finance",
                        "PrimarySmtpAddress": "finance@example.com",
                        "Alias": "finance",
                        "ExternalDirectoryObjectId": "mbx-1",
                        "HasForwarding": "False",
                        "ForwardingSmtpAddress": "",
                    }
                ],
            )
            repo = self._build(root)
            suggestions = repo.autocomplete_prefix("old-finance", "shared_mailbox")
            self.assertIn("old-finance@example.com", suggestions)
            repo.close()

    def test_result_limit_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"Id": f"user-{index}", "UPN": f"prefix{index}@example.com", "DisplayName": f"User {index}"}
                for index in range(60)
            ]
            write_report(root / "Entra_Users_Properties_20260701-010000.csv", rows)
            repo = self._build(root)
            suggestions = repo.autocomplete_prefix("prefix", "user", limit=20)
            self.assertLessEqual(len(suggestions), 20)
            repo.close()

    def test_no_duplicate_suggestions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260701-010100.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "IsMfaRegistered": "True",
                        "DefaultMfaMethod": "Authenticator",
                    }
                ],
            )
            repo = self._build(root)
            suggestions = repo.autocomplete_prefix("ada", "user")
            self.assertEqual(len(suggestions), len(set(suggestions)))
            repo.close()


class EntitySelectorAutocompleteDebounceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self, root: Path) -> EntityIndexRepository:
        os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
        run_sync(root, cold=True)
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
        repo = EntityIndexRepository.open(root)
        assert repo is not None
        return repo

    def test_rapid_text_changes_query_once_after_debounce(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada.lovelace@example.com", "DisplayName": "Ada Lovelace"}],
            )
            repo = self._build(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            with patch.object(repo, "autocomplete_prefix", wraps=repo.autocomplete_prefix) as mocked:
                panel.search_input.setText("a")
                panel.search_input.setText("ad")
                panel.search_input.setText("ada")
                mocked.assert_not_called()
                panel._flush_autocomplete_debounce()
                mocked.assert_called_once_with("ada", "user")
            repo.close()

    def test_latest_prefix_wins_after_debounce(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada.lovelace@example.com", "DisplayName": "Ada Lovelace"}],
            )
            repo = self._build(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            with patch.object(repo, "autocomplete_prefix", wraps=repo.autocomplete_prefix) as mocked:
                panel.search_input.setText("a")
                panel.search_input.setText("ada.lovelace")
                panel._flush_autocomplete_debounce()
                mocked.assert_called_once_with("ada.lovelace", "user")
            repo.close()

    def test_enter_search_remains_immediate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            repo = self._build(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            with patch.object(repo, "autocomplete_prefix", wraps=repo.autocomplete_prefix) as mocked:
                panel.search_input.setText("ada@example.com")
                mocked.assert_not_called()
                panel._run_search()
                mocked.assert_not_called()
                self.assertIsNotNone(panel.selected)
            repo.close()

    def test_entity_type_change_clears_stale_suggestions(self):
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
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            panel.search_input.setText("ada")
            panel._flush_autocomplete_debounce()
            self.assertTrue(panel._completer_model.stringList())
            panel.type_combo.setCurrentIndex(panel.type_combo.findData("device"))
            self.assertEqual(panel._completer_model.stringList(), [])
            repo.close()


if __name__ == "__main__":
    unittest.main()
