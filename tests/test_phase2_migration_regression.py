from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.entity.history import enrich_user_managed_devices
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_projection import (
    USER_DEVICE_LINK_PROJECTION_VERSION,
    build_user_device_link_projection,
    user_device_link_projection_version,
)
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_schema import (
    SCHEMA_VERSION,
    metadata_value,
    open_connection,
)
from diffasaurus.core.entity.index_sync import (
    compute_adapter_version,
    compute_family_adapter_version,
    run_sync,
)
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import scan_report_history
from tests.fixtures.entity_index_generator import write_report


def _write_v1_fixture(root: Path) -> None:
    write_report(
        root / "Entra_Users_Properties_20260701-010000.csv",
        [
            {
                "Id": "user-1",
                "UPN": "ada@example.com",
                "DisplayName": "Ada Lovelace",
                "Mail": "ada@example.com",
            }
        ],
    )
    write_report(
        root / "Entra_Users_Activity_20260701-010000.csv",
        [
            {
                "UserId": "user-1",
                "UPN": "ada@example.com",
                "DisplayName": "Ada Lovelace",
                "LastInteractiveSignInDateTime": "2026-06-30T12:00:00",
            }
        ],
    )
    write_report(
        root / "Entra_Users_AuthenticationMethods_Hybrid_20260701-010000.csv",
        [
            {
                "UPN": "ada@example.com",
                "DisplayName": "Ada Lovelace",
                "MethodsRegistered": "Authenticator ; FIDO2",
                "AuthenticationMethods": "Authenticator ; FIDO2",
                "IsMfaRegistered": "True",
            }
        ],
    )
    write_report(
        root / "Entra_Role_Assignments_20260701-010000.csv",
        [
            {
                "UserPrincipalName": "ada@example.com",
                "DisplayName": "Ada Lovelace",
                "RoleName": "Global Reader",
                "RoleState": "Active",
            }
        ],
    )
    write_report(
        root / "Entra_Group_User_Memberships_20260701-010000.csv",
        [
            {
                "UserId": "user-1",
                "UserPrincipalName": "ada@example.com",
                "UserDisplayName": "Ada Lovelace",
                "GroupId": "g-1",
                "GroupName": "Engineering",
                "MembershipType": "Assigned",
            }
        ],
    )
    write_report(
        root / "Intune_ManagedDevices_Compliance_20260701-010000.csv",
        [
            {
                "UserId": "user-1",
                "UserPrincipalName": "ada@example.com",
                "AzureADDeviceId": "aad-1",
                "ManagedDeviceId": "md-1",
                "DeviceName": "Ada-Laptop",
                "SerialNumber": "SN-1",
                "ComplianceState": "Compliant",
                "OperatingSystem": "Windows",
            }
        ],
    )


class Phase2MigrationRegressionTests(unittest.TestCase):
    def test_phase2_migration_preserves_ordinary_user_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_v1_fixture(root)
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            run_sync(root, cold=True, db_path=db_path)

            # Simulate a schema-v1 / pre-family-fingerprint index that still has
            # readable occurrences, then an interrupted global pending invalidation.
            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            try:
                source_id = int(
                    connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
                )
                before_files = {
                    (row["relative_path"], row["family"], row["captured_at"])
                    for row in connection.execute(
                        "SELECT relative_path, family, captured_at FROM indexed_files WHERE source_id=?",
                        (source_id,),
                    )
                }
                before_occ = int(
                    connection.execute("SELECT COUNT(*) AS c FROM entity_occurrences").fetchone()["c"]
                )
                # Downgrade metadata to look like a pre-Phase-2 published index.
                connection.execute(
                    "UPDATE metadata SET value='1' WHERE key='schema_version'"
                )
                connection.execute(
                    "DELETE FROM metadata WHERE key IN ("
                    "'family_adapter_versions','user_device_link_projection_version',"
                    "'user_device_link_projection_repaired_at')"
                )
                connection.execute("DELETE FROM user_device_link_observations")
                # Simulate the Phase 2 regression: mass-pending after adapter bump.
                connection.execute(
                    "UPDATE indexed_files SET status='pending' WHERE source_id=? AND status='indexed'",
                    (source_id,),
                )
                connection.commit()
            finally:
                connection.close()

            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            key = CanonicalEntityKey("user", "user-1")
            target = datetime(2026, 7, 15, 12, 0, 0)
            # Pending files are invisible before heal.
            broken = repo.reconstruct_state(key, target)
            self.assertEqual(
                next(
                    item.status
                    for item in broken.coverage
                    if item.family == "Entra_Users_AuthenticationMethods"
                ),
                "no_snapshot",
            )
            repo.close()

            # Warm sync applies schema migration, heals pending files, selectively
            # reindexes managed devices, and builds the projection.
            events = []
            run_sync(root, cold=False, db_path=db_path, progress=events.append)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)

            connection = open_connection(db_path, readonly=True)
            try:
                self.assertEqual(metadata_value(connection, "schema_version"), str(SCHEMA_VERSION))
                self.assertEqual(
                    user_device_link_projection_version(connection),
                    USER_DEVICE_LINK_PROJECTION_VERSION,
                )
                after_files = {
                    (row["relative_path"], row["family"], row["captured_at"])
                    for row in connection.execute(
                        "SELECT relative_path, family, captured_at FROM indexed_files"
                    )
                }
                self.assertTrue(before_files.issubset(after_files))
                pending_auth = connection.execute(
                    """
                    SELECT COUNT(*) AS c FROM indexed_files
                    WHERE family='Entra_Users_AuthenticationMethods' AND status='pending'
                    """
                ).fetchone()["c"]
                self.assertEqual(pending_auth, 0)
                after_occ = int(
                    connection.execute("SELECT COUNT(*) AS c FROM entity_occurrences").fetchone()["c"]
                )
                self.assertGreaterEqual(after_occ, before_occ)
                link_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS c FROM user_device_link_observations"
                    ).fetchone()["c"]
                )
                self.assertGreaterEqual(link_count, 1)
            finally:
                connection.close()

            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            state = repo.reconstruct_state(key, target)
            by_family = {item.family: item for item in state.coverage}
            self.assertEqual(by_family["Entra_Users_Properties"].status, "snapshot_used")
            self.assertEqual(by_family["Entra_Users_Activity"].status, "snapshot_used")
            self.assertEqual(
                by_family["Entra_Users_AuthenticationMethods"].status, "snapshot_used"
            )
            self.assertEqual(by_family["Entra_Role_Assignments"].status, "snapshot_used")
            self.assertEqual(
                by_family["Entra_Group_User_Memberships"].status, "snapshot_used"
            )
            self.assertIn("Entra_Users_AuthenticationMethods", state.scalar_properties_by_family)
            self.assertIn("Entra_Role_Assignments", state.relationships_by_family)
            self.assertIn("Entra_Group_User_Memberships", state.relationships_by_family)
            self.assertEqual(
                by_family["Entra_Users_AuthenticationMethods"].snapshot_at,
                datetime(2026, 7, 1, 1, 0, 0),
            )

            enrichment = repo.user_managed_devices_at(key, target)
            self.assertEqual(enrichment.coverage, "populated")
            self.assertEqual(len(enrichment.devices), 1)

            # Ordinary reconstruction must remain unchanged by enrichment.
            state_again = repo.reconstruct_state(key, target)
            self.assertEqual(
                {item.family: item.status for item in state.coverage},
                {item.family: item.status for item in state_again.coverage},
            )
            repo.close()

    def test_failed_projection_build_leaves_index_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_v1_fixture(root)
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            run_sync(root, cold=True, db_path=db_path)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)

            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            try:
                source_id = int(
                    connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
                )
                before_version = user_device_link_projection_version(connection)
                before_auth_status = connection.execute(
                    """
                    SELECT status FROM indexed_files
                    WHERE family='Entra_Users_AuthenticationMethods'
                    ORDER BY captured_at DESC LIMIT 1
                    """
                ).fetchone()["status"]
                connection.execute(
                    "DELETE FROM metadata WHERE key='user_device_link_projection_version'"
                )
                connection.commit()
                with patch(
                    "diffasaurus.core.entity.index_projection.replace_file_user_device_link_observations",
                    side_effect=RuntimeError("injected projection failure"),
                ):
                    with self.assertRaises(RuntimeError):
                        build_user_device_link_projection(connection, source_id, root)
                self.assertEqual(user_device_link_projection_version(connection), 0)
                after_auth_status = connection.execute(
                    """
                    SELECT status FROM indexed_files
                    WHERE family='Entra_Users_AuthenticationMethods'
                    ORDER BY captured_at DESC LIMIT 1
                    """
                ).fetchone()["status"]
                self.assertEqual(after_auth_status, before_auth_status)
                self.assertNotEqual(before_version, 0)
            finally:
                connection.close()

            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            state = repo.reconstruct_state(
                CanonicalEntityKey("user", "user-1"),
                datetime(2026, 7, 15, 12, 0, 0),
            )
            auth = next(
                item
                for item in state.coverage
                if item.family == "Entra_Users_AuthenticationMethods"
            )
            self.assertEqual(auth.status, "snapshot_used")
            repo.close()

    def test_adapter_change_does_not_mass_pending_unrelated_families(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_v1_fixture(root)
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            run_sync(root, cold=True, db_path=db_path)

            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            try:
                # Pretend overall catalog hash changed while family fingerprints are current.
                connection.execute(
                    "UPDATE metadata SET value=? WHERE key='adapter_version'",
                    ("deadbeefdeadbeef",),
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
                auth_version = connection.execute(
                    """
                    SELECT adapter_version FROM indexed_files
                    WHERE family='Entra_Users_AuthenticationMethods'
                    LIMIT 1
                    """
                ).fetchone()["adapter_version"]
                self.assertEqual(
                    auth_version,
                    compute_family_adapter_version("Entra_Users_AuthenticationMethods"),
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
