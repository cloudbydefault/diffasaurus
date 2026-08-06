from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.entity.adapters import DEVICE_MANAGED
from diffasaurus.core.entity.history import enrich_user_managed_devices
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import scan_report_history
from tests.fixtures.entity_index_generator import write_report


def _build(root: Path) -> EntityIndexRepository:
    os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
    run_sync(root, cold=True)
    os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
    repo = EntityIndexRepository.open(root)
    assert repo is not None
    return repo


def _write_users(root: Path, rows: list[dict], stamp: str = "20260701-010000") -> None:
    write_report(root / f"Entra_Users_Properties_{stamp}.csv", rows)


def _write_devices(root: Path, rows: list[dict], stamp: str) -> None:
    write_report(root / f"Intune_ManagedDevices_Compliance_{stamp}.csv", rows)


class PitUserDevicesTests(unittest.TestCase):
    def test_one_user_two_devices_at_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada", "Mail": "ada@example.com"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop-A",
                        "SerialNumber": "SN-A",
                        "ComplianceState": "Compliant",
                        "OperatingSystem": "Windows",
                        "Manufacturer": "Dell",
                        "Model": "XPS",
                    },
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "AzureADDeviceId": "aad-2",
                        "ManagedDeviceId": "md-2",
                        "DeviceName": "Laptop-B",
                        "SerialNumber": "SN-B",
                        "ComplianceState": "Compliant",
                        "OperatingSystem": "Windows",
                    },
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(enrichment.coverage, "populated")
            self.assertEqual(len(enrichment.devices), 2)
            names = {
                next(p.value for p in device.properties if p.name == "DeviceName")
                for device in enrichment.devices
            }
            self.assertEqual(names, {"Laptop-A", "Laptop-B"})
            repo.close()

    def test_userid_exact_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(len(enrichment.devices), 1)
            self.assertEqual(enrichment.devices[0].link_kind, "user_id")
            repo.close()

    def test_userid_wins_over_consistent_upn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(enrichment.devices[0].link_kind, "user_id")
            repo.close()

    def test_historical_upn_rename_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "old@example.com", "DisplayName": "Ada"}],
                "20260601-010000",
            )
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "new@example.com", "DisplayName": "Ada"}],
                "20260701-010000",
            )
            _write_devices(
                root,
                [
                    {
                        "UserPrincipalName": "old@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260615-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 6, 20, 1, 0, 0),
            )
            self.assertEqual(enrichment.coverage, "populated")
            self.assertEqual(len(enrichment.devices), 1)
            self.assertEqual(enrichment.devices[0].link_kind, "upn")
            repo.close()

    def test_device_reassignment_by_target_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [
                    {"Id": "user-a", "UPN": "a@example.com", "DisplayName": "A"},
                    {"Id": "user-b", "UPN": "b@example.com", "DisplayName": "B"},
                ],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-a",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Shared",
                    }
                ],
                "20260701-010000",
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-b",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Shared",
                    }
                ],
                "20260801-010000",
            )
            repo = _build(root)
            early = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-a"),
                datetime(2026, 7, 15, 1, 0, 0),
            )
            late_a = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-a"),
                datetime(2026, 8, 15, 1, 0, 0),
            )
            late_b = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-b"),
                datetime(2026, 8, 15, 1, 0, 0),
            )
            self.assertEqual(len(early.devices), 1)
            self.assertEqual(late_a.coverage, "known_zero")
            self.assertEqual(len(late_b.devices), 1)
            repo.close()

    def test_future_snapshot_never_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Future",
                    }
                ],
                "20260901-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 15, 1, 0, 0),
            )
            self.assertEqual(enrichment.coverage, "no_coverage")
            self.assertEqual(enrichment.devices, ())
            repo.close()

    def test_ambiguous_upn_not_attached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Same UPN observed for two users at the newest binding timestamp.
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {"Id": "user-1", "UPN": "shared@example.com", "DisplayName": "One"},
                    {"Id": "user-2", "UPN": "shared@example.com", "DisplayName": "Two"},
                ],
            )
            _write_devices(
                root,
                [
                    {
                        "UserPrincipalName": "shared@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(enrichment.coverage, "ambiguous_association")
            self.assertEqual(enrichment.devices, ())
            self.assertTrue(
                any(item.resolution_status == "ambiguous" for item in enrichment.unresolved_observations)
            )
            repo.close()

    def test_unbound_upn_remains_auditable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserPrincipalName": "ghost@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            # Unbound for another UPN does not attach to user-1; coverage is known_zero.
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(enrichment.coverage, "known_zero")
            # Direct observation audit via SQLite.
            with sqlite3.connect(entity_index_path(root)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    """
                    SELECT resolution_status, normalized_link_value, diagnostic
                    FROM user_device_link_observations
                    WHERE normalized_link_value='ghost@example.com'
                    """
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["resolution_status"], "unbound")
            repo.close()

    def test_userid_vs_upn_different_users_conflicting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [
                    {"Id": "user-a", "UPN": "a@example.com", "DisplayName": "A"},
                    {"Id": "user-b", "UPN": "b@example.com", "DisplayName": "B"},
                ],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-a",
                        "UserPrincipalName": "b@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            for user_id in ("user-a", "user-b"):
                enrichment = repo.user_managed_devices_at(
                    CanonicalEntityKey("user", user_id),
                    datetime(2026, 7, 20, 1, 0, 0),
                )
                self.assertEqual(enrichment.devices, ())
                self.assertEqual(enrichment.coverage, "ambiguous_association")
                self.assertTrue(
                    any(
                        item.resolution_status == "conflicting"
                        for item in enrichment.unresolved_observations
                    )
                )
            repo.close()

    def test_duplicate_identical_rows_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            row = {
                "UserId": "user-1",
                "UserPrincipalName": "ada@example.com",
                "AzureADDeviceId": "aad-1",
                "ManagedDeviceId": "md-1",
                "DeviceName": "Laptop",
            }
            _write_devices(root, [row, dict(row)], "20260715-010000")
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(len(enrichment.devices), 1)
            with sqlite3.connect(entity_index_path(root)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM user_device_link_observations"
                ).fetchone()[0]
                self.assertEqual(count, 1)
            repo.close()

    def test_different_immutable_owners_conflicting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [
                    {"Id": "user-a", "UPN": "a@example.com", "DisplayName": "A"},
                    {"Id": "user-b", "UPN": "b@example.com", "DisplayName": "B"},
                ],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-a",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    },
                    {
                        "UserId": "user-b",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    },
                ],
                "20260715-010000",
            )
            repo = _build(root)
            for user_id in ("user-a", "user-b"):
                enrichment = repo.user_managed_devices_at(
                    CanonicalEntityKey("user", user_id),
                    datetime(2026, 7, 20, 1, 0, 0),
                )
                self.assertEqual(enrichment.devices, ())
                self.assertTrue(
                    any(
                        item.resolution_status == "conflicting"
                        for item in enrichment.unresolved_observations
                    )
                )
            repo.close()

    def test_dedupe_by_azure_ad_device_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": "aad-same",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Name-A",
                    },
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": "aad-same",
                        "ManagedDeviceId": "md-2",
                        "DeviceName": "Name-B",
                    },
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(len(enrichment.devices), 1)
            self.assertTrue(enrichment.devices[0].dedup_key.startswith("aad:"))
            repo.close()

    def test_managed_device_id_fallback_without_aad(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": "",
                        "ManagedDeviceId": "md-only",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(len(enrichment.devices), 1)
            self.assertTrue(enrichment.devices[0].dedup_key.startswith("md:"))
            repo.close()

    def test_authoritative_empty_is_known_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "AzureADDeviceId": "aad-other",
                        "ManagedDeviceId": "md-other",
                        "DeviceName": "Other",
                        "UserId": "someone-else",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(enrichment.coverage, "known_zero")
            self.assertTrue(DEVICE_MANAGED.authoritative_inventory)
            repo.close()

    def test_no_snapshot_is_no_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertEqual(enrichment.coverage, "no_coverage")
            repo.close()

    def test_ambiguous_not_known_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {"Id": "user-1", "UPN": "shared@example.com", "DisplayName": "One"},
                    {"Id": "user-2", "UPN": "shared@example.com", "DisplayName": "Two"},
                ],
            )
            _write_devices(
                root,
                [
                    {
                        "UserPrincipalName": "shared@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            self.assertNotEqual(enrichment.coverage, "known_zero")
            self.assertEqual(enrichment.coverage, "ambiguous_association")
            repo.close()

    def test_expanded_adapter_payload_preserves_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "EmailAddress": "ada.mail@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                        "SerialNumber": "SN-1",
                        "Manufacturer": "Dell",
                        "Model": "Latitude",
                        "OperatingSystem": "Windows",
                        "OSVersion": "10.0",
                        "ComplianceState": "Compliant",
                        "ManagementAgent": "mdm",
                        "EnrolledDateTime": "2026-01-01T00:00:00",
                        "LastSyncDateTime": "2026-07-01T00:00:00",
                        "OwnerType": "company",
                        "JailBroken": "False",
                        "DaysSinceLastSync": "5",
                        "DeviceActivityStatus": "Active<=30d",
                        "PhoneNumber": "",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            enrichment = repo.user_managed_devices_at(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 20, 1, 0, 0),
            )
            names = {prop.name for prop in enrichment.devices[0].properties}
            for required in (
                "UserId",
                "UserPrincipalName",
                "Manufacturer",
                "Model",
                "OSVersion",
                "ManagementAgent",
                "LastSyncDateTime",
                "OwnerType",
                "DeviceActivityStatus",
            ):
                self.assertIn(required, names)
            self.assertNotIn("EncryptionState", names)
            self.assertNotIn("EnrollmentType", names)
            repo.close()

    def test_persistent_and_legacy_equivalent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                        "SerialNumber": "SN-1",
                        "ComplianceState": "Compliant",
                        "OperatingSystem": "Windows",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            key = CanonicalEntityKey("user", "user-1")
            persistent = repo.user_managed_devices_at(key, target)
            legacy = enrich_user_managed_devices(key, scan_report_history(root), target)
            self.assertEqual(persistent.coverage, legacy.coverage)
            self.assertEqual(
                {device.dedup_key for device in persistent.devices},
                {device.dedup_key for device in legacy.devices},
            )
            self.assertEqual(
                {device.link_kind for device in persistent.devices},
                {device.link_kind for device in legacy.devices},
            )
            repo.close()

    def test_repository_api_from_background_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            _write_devices(
                root,
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": "aad-1",
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Laptop",
                    }
                ],
                "20260715-010000",
            )
            repo = _build(root)
            key = CanonicalEntityKey("user", "user-1")
            target = datetime(2026, 7, 20, 1, 0, 0)

            def _run():
                return repo.user_managed_devices_at(key, target)

            with ThreadPoolExecutor(max_workers=1) as executor:
                enrichment = executor.submit(_run).result()
            self.assertEqual(enrichment.coverage, "populated")
            repo.close()

    def test_query_count_bounded_for_many_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_users(
                root,
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            rows = [
                {
                    "UserId": "user-1",
                    "AzureADDeviceId": f"aad-{index}",
                    "ManagedDeviceId": f"md-{index}",
                    "DeviceName": f"Device-{index}",
                }
                for index in range(40)
            ]
            _write_devices(root, rows, "20260715-010000")
            repo = _build(root)
            key = CanonicalEntityKey("user", "user-1")
            target = datetime(2026, 7, 20, 1, 0, 0)
            execute_count = {"n": 0}
            from diffasaurus.core.entity import index_repository as repo_mod

            original_open = repo_mod.open_connection

            def counting_open(*args, **kwargs):
                connection = original_open(*args, **kwargs)
                connection.set_trace_callback(lambda _sql: execute_count.__setitem__("n", execute_count["n"] + 1))
                return connection

            with patch.object(repo_mod, "open_connection", side_effect=counting_open):
                enrichment = repo.user_managed_devices_at(key, target)
            self.assertEqual(len(enrichment.devices), 40)
            # Snapshot + resolved + unresolved + entity batch + occurrence batch + aliases
            self.assertLessEqual(execute_count["n"], 20)
            repo.close()

if __name__ == "__main__":
    unittest.main()
