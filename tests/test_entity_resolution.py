import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from diffasaurus.core.entity.resolution import build_entity_resolver
from diffasaurus.core.entity.snapshots import clear_parse_cache
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import scan_report_history


def write_report(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class EntityResolutionTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def _families(self, root: Path):
        return scan_report_history(root)

    def test_deleted_entity_remains_searchable_by_id_and_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "old@example.com",
                        "DisplayName": "Ada Lovelace",
                        "Department": "R&D",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-2",
                        "UPN": "other@example.com",
                        "DisplayName": "Other User",
                        "Department": "IT",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            by_id = resolver.search("user-1", "user")
            self.assertEqual(len(by_id.matches), 1)
            self.assertFalse(by_id.matches[0].present_in_latest)
            by_alias = resolver.search("old@example.com", "user")
            self.assertEqual(len(by_alias.matches), 1)
            self.assertEqual(by_alias.matches[0].key.primary_id, "user-1")

    def test_first_and_last_seen_use_full_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = datetime(2026, 7, 1, 1, 0, 0)
            middle = datetime(2026, 7, 15, 1, 0, 0)
            last = datetime(2026, 8, 1, 1, 0, 0)
            for captured_at in (first, middle, last):
                write_report(
                    root / f"Entra_Users_Properties_{captured_at:%Y%m%d-%H%M%S}.csv",
                    [
                        {
                            "Id": "user-1",
                            "UPN": "ada@example.com",
                            "DisplayName": "Ada",
                        }
                    ],
                )
            resolver = build_entity_resolver(self._families(root))
            record = resolver.search("user-1", "user").matches[0]
            self.assertEqual(record.first_seen, first)
            self.assertEqual(record.last_seen, last)

    def test_old_upn_finds_same_user_after_rename(self):
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
                        "Id": "user-1",
                        "UPN": "new@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            result = resolver.search("old@example.com", "user")
            self.assertEqual(len(result.matches), 1)
            self.assertEqual(result.matches[0].key.primary_id, "user-1")

    def test_recycled_upn_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "shared@example.com",
                        "DisplayName": "First",
                    },
                    {
                        "Id": "user-2",
                        "UPN": "shared@example.com",
                        "DisplayName": "Second",
                    },
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            result = resolver.search("shared@example.com", "user")
            self.assertTrue(result.ambiguous)
            self.assertEqual(len(result.matches), 2)
            ids = {match.key.primary_id for match in result.matches}
            self.assertEqual(ids, {"user-1", "user-2"})

    def test_catalog_only_family_without_snapshots_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            families_seen = {
                family
                for record in resolver.records
                for family in record.source_families
            }
            self.assertIn("Entra_Users_Properties", families_seen)
            self.assertNotIn("Exchange_SharedMailboxes", families_seen)

    def test_devices_merge_only_on_matching_aad_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_serial = "SN-123"
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260801-010000.csv",
                [
                    {
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop-A",
                        "SerialNumber": shared_serial,
                        "ComplianceState": "Compliant",
                    }
                ],
            )
            write_report(
                root / "Intune_Devices_Autopilot_20260801-010000.csv",
                [
                    {
                        "AzureADDeviceId": "aad-1",
                        "AutopilotObjectId": "ap-1",
                        "DisplayName": "Laptop-A",
                        "SerialNumber": shared_serial,
                        "EnrollmentState": "Enrolled",
                    }
                ],
            )
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260802-010000.csv",
                [
                    {
                        "AzureADDeviceId": "aad-2",
                        "ManagedDeviceId": "md-2",
                        "DeviceName": "Laptop-B",
                        "SerialNumber": shared_serial,
                        "ComplianceState": "Compliant",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            merged = resolver.get(CanonicalEntityKey("device", "aad:aad-1"))
            self.assertIsNotNone(merged)
            assert merged is not None
            self.assertIn("Intune_ManagedDevices_Compliance", merged.source_families)
            self.assertIn("Intune_Devices_Autopilot", merged.source_families)
            separate = resolver.get(CanonicalEntityKey("device", "aad:aad-2"))
            self.assertIsNotNone(separate)
            assert separate is not None
            self.assertNotIn("Intune_Devices_Autopilot", separate.source_families)

    def test_fallback_device_identifiers_do_not_merge_across_families(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260801-010000.csv",
                [
                    {
                        "ManagedDeviceId": "md-only",
                        "DeviceName": "Shared Name",
                        "SerialNumber": "SN-999",
                    }
                ],
            )
            write_report(
                root / "Intune_Devices_Autopilot_20260801-010000.csv",
                [
                    {
                        "AutopilotObjectId": "ap-only",
                        "DisplayName": "Shared Name",
                        "SerialNumber": "SN-999",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            by_serial = resolver.search("SN-999", "device")
            self.assertEqual(len(by_serial.matches), 2)
            keys = {match.key.primary_id for match in by_serial.matches}
            self.assertIn("intune:Intune_ManagedDevices_Compliance:md-only", keys)
            self.assertIn("autopilot:ap-only", keys)

    def test_shared_mailbox_uses_verified_exchange_columns(self):
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
                        "HasFullAccessDelegates": "True",
                        "FullAccessDelegates": "ada@example.com",
                        "LitigationHoldEnabled": "False",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            by_smtp = resolver.search("finance@example.com", "shared_mailbox")
            self.assertEqual(len(by_smtp.matches), 1)
            record = by_smtp.matches[0]
            props = {
                prop.name: prop.value
                for family_props in record.properties_by_family.values()
                for prop in family_props
            }
            self.assertEqual(props["PrimarySmtpAddress"], "finance@example.com")
            self.assertEqual(props["FullAccessDelegates"], "ada@example.com")

    def test_temporal_upn_binding_resolves_auth_row_after_properties(self):
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
                root / "Entra_Users_AuthenticationMethods_20260804-040500.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "IsMfaRegistered": "True",
                        "DefaultMfaMethod": "Authenticator",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            record = resolver.get(CanonicalEntityKey("user", "user-1"))
            self.assertIsNotNone(record)
            assert record is not None
            self.assertIn("Entra_Users_AuthenticationMethods", record.source_families)

    def test_future_upn_binding_not_used_for_older_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-040000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "old@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-060000.csv",
                [
                    {
                        "Id": "user-2",
                        "UPN": "old@example.com",
                        "DisplayName": "Other",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260804-040500.csv",
                [
                    {
                        "UPN": "old@example.com",
                        "DisplayName": "Ada",
                        "IsMfaRegistered": "True",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            record = resolver.get(CanonicalEntityKey("user", "user-1"))
            self.assertIsNotNone(record)
            assert record is not None
            self.assertIn("Entra_Users_AuthenticationMethods", record.source_families)
            other = resolver.get(CanonicalEntityKey("user", "user-2"))
            self.assertIsNotNone(other)
            assert other is not None
            self.assertNotIn("Entra_Users_AuthenticationMethods", other.source_families)

    def test_renamed_upn_resolves_correct_user_before_and_after_rename(self):
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
                        "Id": "user-1",
                        "UPN": "new@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260715-010000.csv",
                [
                    {
                        "UPN": "old@example.com",
                        "DisplayName": "Ada",
                        "IsMfaRegistered": "True",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            record = resolver.get(CanonicalEntityKey("user", "user-1"))
            self.assertIsNotNone(record)
            assert record is not None
            self.assertIn("Entra_Users_AuthenticationMethods", record.source_families)

    def test_recycled_upn_with_ambiguous_candidates_not_auto_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-040000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "shared@example.com",
                        "DisplayName": "First",
                    },
                    {
                        "Id": "user-2",
                        "UPN": "shared@example.com",
                        "DisplayName": "Second",
                    },
                ],
            )
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260804-040500.csv",
                [
                    {
                        "UPN": "shared@example.com",
                        "DisplayName": "Ambiguous",
                        "IsMfaRegistered": "False",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            auth_only = [
                record
                for record in resolver.records
                if "Entra_Users_AuthenticationMethods" in record.source_families
            ]
            self.assertEqual(len(auth_only), 0)


class EntityResolutionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_ambiguous_search_shows_disambiguation_list(self):
        from diffasaurus.core.entity.types import EntityRecord, TimedAlias
        from diffasaurus.ui.entity_history import EntityHistoryPage

        page = EntityHistoryPage()
        first = EntityRecord(
            key=CanonicalEntityKey("user", "user-1"),
            display_name="First",
            aliases=[TimedAlias("upn", "shared@example.com", datetime.now(), datetime.now(), "Entra_Users_Properties")],
            first_seen=datetime(2026, 8, 1),
            last_seen=datetime(2026, 8, 2),
        )
        second = EntityRecord(
            key=CanonicalEntityKey("user", "user-2"),
            display_name="Second",
            aliases=[TimedAlias("upn", "shared@example.com", datetime.now(), datetime.now(), "Entra_Users_Properties")],
            first_seen=datetime(2026, 8, 1),
            last_seen=datetime(2026, 8, 3),
        )
        from diffasaurus.core.entity.resolution import SearchResult

        page.entity_selector._show_disambiguation(SearchResult((first, second), True))
        self.assertEqual(page.disambiguation.count(), 2)
        self.assertFalse(page.disambiguation.isHidden())
