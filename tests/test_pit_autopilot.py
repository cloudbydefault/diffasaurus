from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.entity.adapters import DEVICE_AUTOPILOT
from diffasaurus.core.entity.history import (
    enrich_user_managed_devices_with_autopilot,
)
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_schema import open_connection
from diffasaurus.core.entity.index_sync import (
    compute_family_adapter_version,
    run_sync,
)
from diffasaurus.core.entity.pit_enrichment import AUTOPILOT_FAMILY
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import scan_report_history
from tests.fixtures.entity_index_generator import write_report

AAD_W = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AAD_MAC = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
AAD_IOS = "cccccccc-cccc-cccc-cccc-cccccccccccc"
MD_W = "11111111-1111-1111-1111-111111111111"
MD_MAC = "22222222-2222-2222-2222-222222222222"
MD_IOS = "33333333-3333-3333-3333-333333333333"


def _build(root: Path) -> EntityIndexRepository:
    os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
    run_sync(root, cold=True)
    os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
    repo = EntityIndexRepository.open(root)
    assert repo is not None
    return repo


def _write_fixture(root: Path) -> None:
    write_report(
        root / "Entra_Users_Properties_20260701-010000.csv",
        [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada", "Mail": "ada@example.com"}],
    )
    write_report(
        root / "Intune_ManagedDevices_Compliance_20260715-010000.csv",
        [
            {
                "UserId": "user-1",
                "UserPrincipalName": "ada@example.com",
                "AzureADDeviceId": AAD_W,
                "ManagedDeviceId": MD_W,
                "DeviceName": "Win-Laptop",
                "SerialNumber": "WIN-SN",
                "OperatingSystem": "Windows",
                "ComplianceState": "Compliant",
            },
            {
                "UserId": "user-1",
                "UserPrincipalName": "ada@example.com",
                "AzureADDeviceId": AAD_MAC,
                "ManagedDeviceId": MD_MAC,
                "DeviceName": "MacBook",
                "SerialNumber": "MAC-SN",
                "OperatingSystem": "macOS",
                "ComplianceState": "Compliant",
            },
            {
                "UserId": "user-1",
                "UserPrincipalName": "ada@example.com",
                "AzureADDeviceId": AAD_IOS,
                "ManagedDeviceId": MD_IOS,
                "DeviceName": "iPhone",
                "SerialNumber": "IOS-SN",
                "OperatingSystem": "iOS",
                "ComplianceState": "Compliant",
            },
        ],
    )
    write_report(
        root / "Intune_Devices_Autopilot_20260710-010000.csv",
        [
            {
                "AzureADDeviceId": AAD_W,
                "ManagedDeviceId": MD_W,
                "AutopilotObjectId": "ap-1",
                "SerialNumber": "WIN-SN",
                "DisplayName": "Win-Laptop",
                "EnrollmentState": "enrolled",
                "Manufacturer": "Dell",
                "Model": "XPS",
            }
        ],
    )
    write_report(
        root / "Intune_Devices_Autopilot_20260725-010000.csv",
        [
            {
                "AzureADDeviceId": AAD_W,
                "ManagedDeviceId": MD_W,
                "AutopilotObjectId": "ap-1",
                "SerialNumber": "WIN-SN-FUTURE",
                "DisplayName": "Win-Laptop-Future",
                "EnrollmentState": "enrolled",
            }
        ],
    )


class PitAutopilotRepositoryTests(unittest.TestCase):
    def test_windows_matched_mac_ios_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            key = CanonicalEntityKey("user", "user-1")
            enrichment = repo.user_managed_devices_with_autopilot_at(key, target)
            self.assertEqual(len(enrichment.enriched_devices), 3)
            by_os = {
                next(p.value for p in item.device.properties if p.name == "OperatingSystem"): item
                for item in enrichment.enriched_devices
            }
            self.assertEqual(by_os["Windows"].autopilot.status, "matched")
            self.assertEqual(by_os["macOS"].autopilot.status, "not_applicable")
            self.assertEqual(by_os["iOS"].autopilot.status, "not_applicable")
            self.assertIsNotNone(enrichment.autopilot_family_coverage)
            assert enrichment.autopilot_family_coverage is not None
            self.assertEqual(
                enrichment.autopilot_family_coverage.snapshot_at,
                datetime(2026, 7, 10, 1, 0, 0),
            )
            repo.close()

    def test_future_autopilot_snapshot_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            enrichment = repo.user_managed_devices_with_autopilot_at(
                CanonicalEntityKey("user", "user-1"),
                target,
            )
            win = next(
                item
                for item in enrichment.enriched_devices
                if any(p.name == "OperatingSystem" and p.value == "Windows" for p in item.device.properties)
            )
            serials = {p.value for p in win.autopilot.properties if p.name == "SerialNumber"}
            self.assertEqual(serials, {"WIN-SN"})
            repo.close()

    def test_target_before_first_autopilot_snapshot_no_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260701-010000.csv",
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "AzureADDeviceId": AAD_W,
                        "ManagedDeviceId": MD_W,
                        "DeviceName": "Win-Laptop",
                        "SerialNumber": "WIN-SN",
                        "OperatingSystem": "Windows",
                        "ComplianceState": "Compliant",
                    }
                ],
            )
            repo = _build(root)
            target = datetime(2026, 7, 5, 1, 0, 0)
            enrichment = repo.user_managed_devices_with_autopilot_at(
                CanonicalEntityKey("user", "user-1"),
                target,
            )
            win = next(
                item
                for item in enrichment.enriched_devices
                if any(p.name == "OperatingSystem" and p.value == "Windows" for p in item.device.properties)
            )
            self.assertEqual(win.autopilot.status, "no_coverage")
            assert enrichment.autopilot_family_coverage is not None
            self.assertEqual(enrichment.autopilot_family_coverage.status, "no_snapshot")
            repo.close()

    def test_independent_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            enrichment = repo.user_managed_devices_with_autopilot_at(
                CanonicalEntityKey("user", "user-1"),
                target,
            )
            win = next(
                item
                for item in enrichment.enriched_devices
                if any(p.name == "OperatingSystem" and p.value == "Windows" for p in item.device.properties)
            )
            self.assertEqual(win.device.provenance.observations[0].family, "Intune_ManagedDevices_Compliance")
            assert win.autopilot.provenance is not None
            self.assertEqual(win.autopilot.provenance.observations[0].family, AUTOPILOT_FAMILY)
            self.assertEqual(
                win.device.provenance.observations[0].snapshot_at,
                datetime(2026, 7, 15, 1, 0, 0),
            )
            self.assertEqual(
                win.autopilot.provenance.observations[0].snapshot_at,
                datetime(2026, 7, 10, 1, 0, 0),
            )
            repo.close()

    def test_legacy_and_repository_equivalent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            key = CanonicalEntityKey("user", "user-1")
            indexed = repo.user_managed_devices_with_autopilot_at(key, target)
            families = scan_report_history(root)
            legacy = enrich_user_managed_devices_with_autopilot(key, families, target)
            indexed_status = sorted(
                (next(p.value for p in item.device.properties if p.name == "OperatingSystem"), item.autopilot.status)
                for item in indexed.enriched_devices
            )
            legacy_status = sorted(
                (next(p.value for p in item.device.properties if p.name == "OperatingSystem"), item.autopilot.status)
                for item in legacy.enriched_devices
            )
            self.assertEqual(indexed_status, legacy_status)
            repo.close()

    def test_background_thread_repository_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            key = CanonicalEntityKey("user", "user-1")

            def _worker():
                worker_repo = EntityIndexRepository.open(root)
                assert worker_repo is not None
                result = worker_repo.user_managed_devices_with_autopilot_at(key, target)
                worker_repo.close()
                return result

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: _worker(), range(2)))
            self.assertEqual(len(results[0].enriched_devices), 3)
            repo.close()

    def test_bounded_autopilot_query_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            key = CanonicalEntityKey("user", "user-1")
            base = repo.user_managed_devices_at(key, target)
            load_calls = 0
            original_load = repo._load_autopilot_snapshot_index

            def counting_load(connection, target_dt):
                nonlocal load_calls
                load_calls += 1
                return original_load(connection, target_dt)

            with patch.object(repo, "_load_autopilot_snapshot_index", counting_load):
                repo.enrich_managed_devices_with_autopilot_at(base, target)
            self.assertEqual(load_calls, 1)
            repo.close()

    def test_enrich_user_point_in_time_does_not_change_entity_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            write_report(
                root / "Entra_Users_AuthenticationMethods_Hybrid_20260701-010000.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "MethodsRegistered": "Authenticator",
                        "AuthenticationMethods": "Authenticator",
                        "IsMfaRegistered": "True",
                    }
                ],
            )
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            key = CanonicalEntityKey("user", "user-1")
            before = repo.reconstruct_state(key, target)
            repo.enrich_user_point_in_time(key, target)
            after = repo.reconstruct_state(key, target)
            self.assertEqual(
                {item.family: item.status for item in before.coverage},
                {item.family: item.status for item in after.coverage},
            )
            repo.close()

    def test_multiple_devices_one_snapshot_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            repo = _build(root)
            target = datetime(2026, 7, 20, 1, 0, 0)
            enrichment = repo.user_managed_devices_with_autopilot_at(
                CanonicalEntityKey("user", "user-1"),
                target,
            )
            self.assertEqual(len(enrichment.enriched_devices), 3)
            matched = [item for item in enrichment.enriched_devices if item.autopilot.status == "matched"]
            self.assertEqual(len(matched), 1)
            repo.close()


class AutopilotSelectiveReindexTests(unittest.TestCase):
    def test_autopilot_adapter_change_reindexes_only_autopilot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            run_sync(root, cold=True, db_path=db_path)

            connection = open_connection(db_path, readonly=False)
            try:
                connection.execute(
                    """
                    UPDATE indexed_files
                    SET adapter_version='stale-autopilot'
                    WHERE family=?
                    """,
                    (AUTOPILOT_FAMILY,),
                )
                connection.commit()
            finally:
                connection.close()

            run_sync(root, cold=False, db_path=db_path)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)

            connection = open_connection(db_path, readonly=True)
            try:
                pending = connection.execute(
                    """
                    SELECT family, COUNT(*) AS c FROM indexed_files
                    WHERE status='pending'
                    GROUP BY family
                    """
                ).fetchall()
                self.assertEqual(pending, [])
                autopilot_version = connection.execute(
                    """
                    SELECT adapter_version FROM indexed_files
                    WHERE family=?
                    LIMIT 1
                    """,
                    (AUTOPILOT_FAMILY,),
                ).fetchone()["adapter_version"]
                self.assertEqual(
                    autopilot_version,
                    compute_family_adapter_version(AUTOPILOT_FAMILY),
                )
                managed_version = connection.execute(
                    """
                    SELECT adapter_version FROM indexed_files
                    WHERE family='Intune_ManagedDevices_Compliance'
                    LIMIT 1
                    """
                ).fetchone()["adapter_version"]
                self.assertEqual(
                    managed_version,
                    compute_family_adapter_version("Intune_ManagedDevices_Compliance"),
                )
            finally:
                connection.close()

    def test_second_warm_sync_no_op_after_autopilot_reindex(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            run_sync(root, cold=True, db_path=db_path)
            run_sync(root, cold=False, db_path=db_path)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)

            connection = open_connection(db_path, readonly=True)
            try:
                pending = connection.execute(
                    "SELECT COUNT(*) AS c FROM indexed_files WHERE status='pending'"
                ).fetchone()["c"]
                self.assertEqual(pending, 0)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
