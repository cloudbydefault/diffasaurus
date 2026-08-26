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
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.ui.entity_history import EntityHistoryPage
from diffasaurus.ui.entity_search import EntitySelectorPanel
from diffasaurus.ui.point_in_time import PointInTimePage
from tests.fixtures.entity_index_generator import write_report


def _write_john_michael_smith_fixture(root: Path) -> None:
    write_report(
        root / "Entra_Users_Properties_20260701-010000.csv",
        [
            {
                "Id": "user-jms",
                "UPN": "john.smith@example.com",
                "DisplayName": "John Michael Smith",
            }
        ],
    )
    write_report(
        root / "Entra_Users_Properties_20260801-010000.csv",
        [
            {
                "Id": "user-jms",
                "UPN": "j.smith@example.com",
                "DisplayName": "John Michael Smith",
            }
        ],
    )
    write_report(
        root / "Entra_Users_Properties_20260901-010000.csv",
        [
            {
                "Id": "user-jane",
                "UPN": "jane.smith@example.com",
                "DisplayName": "Jane Smith",
            }
        ],
    )


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

    def test_token_anywhere_prefix_search_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_john_michael_smith_fixture(root)
            repo = self._build(root)
            expected_key = CanonicalEntityKey("user", "user-jms")

            def assert_matches(query: str) -> None:
                suggestions = repo.autocomplete_prefix(query, "user")
                self.assertTrue(
                    suggestions,
                    msg=f"expected suggestions for {query!r}, got none",
                )
                search = repo.search(query, "user")
                self.assertIn(
                    expected_key,
                    {record.key for record in search.matches},
                    msg=f"expected entity for {query!r}",
                )

            for query in (
                "John",
                "Smith",
                "Smi",
                "Michael",
                "smith john",
                "john smi",
                "SMITH",
                "j.smith",
            ):
                assert_matches(query)

            jane_only = repo.search("smith jane", "user")
            self.assertEqual(
                {record.key for record in jane_only.matches},
                {CanonicalEntityKey("user", "user-jane")},
            )
            repo.close()

    def test_token_search_result_ordering_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_john_michael_smith_fixture(root)
            repo = self._build(root)
            first = repo.autocomplete_prefix("smith", "user")
            second = repo.autocomplete_prefix("smith", "user")
            self.assertEqual(first, second)
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
            self.assertEqual(len(by_serial), 1)
            self.assertTrue(repo.search("sn-surf", "device").matches)
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
            self.assertEqual(suggestions, ["Ada · new.upn@example.com"])
            self.assertTrue(repo.search("old.upn@example.com", "user").matches)
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
            self.assertEqual(suggestions, ["Finance · finance@example.com"])
            self.assertTrue(repo.search("old-finance@example.com", "shared_mailbox").matches)
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
            with patch.object(repo, "autocomplete_matches", wraps=repo.autocomplete_matches) as mocked:
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
            with patch.object(repo, "autocomplete_matches", wraps=repo.autocomplete_matches) as mocked:
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
            with patch.object(repo, "autocomplete_matches", wraps=repo.autocomplete_matches) as mocked:
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
            self.assertGreater(panel._completer_model.rowCount(), 0)
            panel.type_combo.setCurrentIndex(panel.type_combo.findData("device"))
            self.assertEqual(panel._completer_model.rowCount(), 0)
            repo.close()


class EntityAutocompleteDeduplicationTests(unittest.TestCase):
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

    def _write_many_alias_john_smith(self, root: Path) -> None:
        write_report(
            root / "Entra_Users_Properties_20260701-010000.csv",
            [
                {
                    "Id": "user-jms",
                    "UPN": "john.smith@example.com",
                    "DisplayName": "John Smith",
                    "Mail": "jsmith@example.com",
                }
            ],
        )
        write_report(
            root / "Entra_Users_Properties_20260801-010000.csv",
            [
                {
                    "Id": "user-jms",
                    "UPN": "legacy.john@example.com",
                    "DisplayName": "John Smith",
                    "Mail": "jsmith@example.com",
                }
            ],
        )
        write_report(
            root / "Entra_Users_Properties_20260901-010000.csv",
            [
                {
                    "Id": "user-jms",
                    "UPN": "j.smith@example.com",
                    "DisplayName": "John Smith",
                    "Mail": "jsmith@example.com",
                }
            ],
        )

    def test_one_entity_many_aliases_returns_single_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_many_alias_john_smith(root)
            repo = self._build(root)
            expected_key = CanonicalEntityKey("user", "user-jms")
            for query in ("smith", "john", "legacy"):
                matches = repo.autocomplete_matches(query, "user")
                keys = [key for _, key in matches if key == expected_key]
                self.assertEqual(len(keys), 1, msg=f"query={query!r}")
            repo.close()

    def test_friendly_label_uses_display_name_and_primary_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_many_alias_john_smith(root)
            repo = self._build(root)
            matches = repo.autocomplete_matches("smith", "user")
            self.assertEqual(len(matches), 1)
            label, key = matches[0]
            self.assertEqual(key, CanonicalEntityKey("user", "user-jms"))
            self.assertIn("John Smith", label)
            self.assertIn(" · ", label)
            self.assertIn("j.smith@example.com", label)
            self.assertNotIn("legacy.john@example.com", label)
            repo.close()

    def test_two_smith_entities_return_two_completions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_john_michael_smith_fixture(root)
            repo = self._build(root)
            matches = repo.autocomplete_matches("smith", "user")
            self.assertEqual(len(matches), 2)
            self.assertEqual(
                {key.label() for _, key in matches},
                {
                    CanonicalEntityKey("user", "user-jms").label(),
                    CanonicalEntityKey("user", "user-jane").label(),
                },
            )
            repo.close()

    def test_duplicate_display_names_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {"Id": "user-1", "UPN": "john.one@example.com", "DisplayName": "John Smith"},
                    {"Id": "user-2", "UPN": "john.two@example.com", "DisplayName": "John Smith"},
                ],
            )
            repo = self._build(root)
            matches = repo.autocomplete_matches("smith", "user")
            self.assertEqual(len(matches), 2)
            self.assertEqual(len({key.primary_id for _, key in matches}), 2)
            repo.close()

    def test_alias_heavy_entity_does_not_crowd_result_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_many_alias_john_smith(root)
            write_report(
                root / "Entra_Users_Properties_20260902-010000.csv",
                [
                    {"Id": "user-jane", "UPN": "jane.smith@example.com", "DisplayName": "Jane Smith"},
                    {"Id": "user-alex", "UPN": "alex.smith@example.com", "DisplayName": "Alex Smith"},
                ],
            )
            repo = self._build(root)
            matches = repo.autocomplete_matches("smith", "user", limit=3)
            self.assertEqual(len(matches), 3)
            self.assertEqual(len({key.label() for _, key in matches}), 3)
            repo.close()

    def test_deduplicated_completion_still_commits_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_john_michael_smith_fixture(root)
            repo = self._build(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            panel.search_input.setText("smith")
            panel._flush_autocomplete_debounce()
            suggestion, key = repo.autocomplete_matches("smith", "user")[0]
            panel._completer.activated.emit(suggestion)
            self.assertEqual(panel.selected.key, key)
            page = PointInTimePage()
            page.entity_selector.set_repository(repo)
            page.entity_selector.search_input.setText("smith")
            page.entity_selector._flush_autocomplete_debounce()
            suggestion, key = repo.autocomplete_matches("smith", "user")[0]
            page.entity_selector._completer.activated.emit(suggestion)
            self.assertTrue(page.reconstruct_button.isEnabled())
            repo.close()


class EntitySelectorCompleterCommitTests(unittest.TestCase):
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

    def test_completer_activation_commits_entity_without_return_pressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_john_michael_smith_fixture(root)
            repo = self._build(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            panel.search_input.setText("Smith")
            panel._flush_autocomplete_debounce()
            matches = repo.autocomplete_matches("Smith", "user")
            self.assertTrue(matches)
            suggestion, key = matches[0]
            panel._completer.activated.emit(suggestion)
            self.assertIsNotNone(panel.selected)
            self.assertEqual(panel.selected.key, key)
            repo.close()

    def test_entity_history_reacts_immediately_to_completer_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_john_michael_smith_fixture(root)
            repo = self._build(root)
            page = EntityHistoryPage()
            page.set_repository(repo)
            page.search_input.setText("Smith")
            page.entity_selector._flush_autocomplete_debounce()
            key = CanonicalEntityKey("user", "user-jms")
            suggestion = next(
                text for text, match_key in repo.autocomplete_matches("Smith", "user") if match_key == key
            )
            page.entity_selector._completer.activated.emit(suggestion)
            self.assertIsNotNone(page.entity_selector.selected)
            self.assertEqual(page.entity_selector.selected.key, key)
            self.assertTrue(page.view_at_date_button.isEnabled())
            self.assertEqual(page.card_title.text(), "John Michael Smith")
            repo.close()

    def test_point_in_time_reacts_immediately_to_completer_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_john_michael_smith_fixture(root)
            repo = self._build(root)
            page = PointInTimePage()
            page.entity_selector.set_repository(repo)
            page.entity_selector.search_input.setText("Smith")
            page.entity_selector._flush_autocomplete_debounce()
            suggestion, key = repo.autocomplete_matches("Smith", "user")[0]
            page.entity_selector._completer.activated.emit(suggestion)
            self.assertIsNotNone(page.entity_selector.selected)
            self.assertEqual(page.entity_selector.selected.key, key)
            self.assertTrue(page.reconstruct_button.isEnabled())
            repo.close()

    def test_completer_activation_uses_stable_entity_key_for_duplicate_display_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {"Id": "user-1", "UPN": "john.one@example.com", "DisplayName": "John Smith"},
                    {"Id": "user-2", "UPN": "john.two@example.com", "DisplayName": "John Smith"},
                ],
            )
            repo = self._build(root)
            panel = EntitySelectorPanel()
            panel.set_repository(repo)
            panel.search_input.setText("john.one")
            panel._flush_autocomplete_debounce()
            suggestion, key = repo.autocomplete_matches("john.one", "user")[0]
            panel._completer.activated.emit(suggestion)
            self.assertEqual(panel.selected.key, CanonicalEntityKey("user", "user-1"))
            self.assertEqual(key, CanonicalEntityKey("user", "user-1"))
            repo.close()


if __name__ == "__main__":
    unittest.main()
