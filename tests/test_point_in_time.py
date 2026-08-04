import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from diffasaurus.core.entity.history import (
    compare_entity_states,
    present_at_target,
    reconstruct_entity_state,
)
from diffasaurus.core.entity.resolution import build_entity_resolver
from diffasaurus.core.entity.snapshots import clear_parse_cache
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import scan_report_history


def write_report(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class PointInTimeTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def _families(self, root: Path):
        return scan_report_history(root)

    def test_user_reconstructed_from_newest_snapshot_at_or_before_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 1, 12, 0, 0)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "R&D",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "IT",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                target,
            )
            props = {
                prop.name: prop.value
                for prop in state.scalar_properties_by_family.get("Entra_Users_Properties", ())
            }
            self.assertEqual(props["Department"], "R&D")
            self.assertEqual(state.presence, "present")

    def test_snapshot_after_target_never_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 7, 15, 1, 0, 0)
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
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                target,
            )
            self.assertEqual(state.presence, "unknown")
            coverage = {
                item.family: item.status for item in state.coverage
            }
            self.assertEqual(
                coverage.get("Entra_Users_Properties"),
                "no_snapshot",
            )

    def test_target_before_first_snapshot_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 6, 1, 1, 0, 0)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                target,
            )
            self.assertEqual(present_at_target(state), "unknown")

    def test_irregular_schedules_use_different_snapshot_times(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 4, 13, 0, 0)
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "R&D",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Activity_20260804-042100.csv",
                [
                    {
                        "UserId": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "IT",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                target,
            )
            props_at = {
                item.family: item.snapshot_at
                for item in state.coverage
                if item.snapshot_at is not None
            }
            self.assertEqual(props_at["Entra_Users_Properties"], datetime(2026, 8, 1, 1, 0, 0))
            self.assertEqual(props_at["Entra_Users_Activity"], datetime(2026, 8, 4, 4, 21, 0))

    def test_upn_rename_preserves_canonical_key(self):
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
            before = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                datetime(2026, 7, 15, 1, 0, 0),
            )
            after = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                datetime(2026, 8, 15, 1, 0, 0),
            )
            self.assertEqual(before.key.primary_id, "user-1")
            self.assertEqual(after.key.primary_id, "user-1")

    def test_deleted_user_present_before_absent_after(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260731-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-010000.csv",
                [
                    {
                        "Id": "user-2",
                        "UPN": "other@example.com",
                        "DisplayName": "Other",
                    }
                ],
            )
            before = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                datetime(2026, 8, 1, 1, 0, 0),
            )
            after = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                datetime(2026, 8, 4, 12, 0, 0),
            )
            self.assertEqual(before.presence, "present")
            self.assertEqual(after.presence, "absent")

    def test_ambiguous_recycled_upn_search(self):
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

    def test_device_identity_never_merges_on_serial_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260801-010000.csv",
                [
                    {
                        "AzureADDeviceId": "aad-1",
                        "SerialNumber": "SN-123",
                        "DeviceName": "A",
                    }
                ],
            )
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260802-010000.csv",
                [
                    {
                        "AzureADDeviceId": "aad-2",
                        "SerialNumber": "SN-123",
                        "DeviceName": "B",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            matches = resolver.search("SN-123", "device")
            ids = {match.key.primary_id for match in matches.matches}
            self.assertEqual(ids, {"aad:aad-1", "aad:aad-2"})

    def test_shared_mailbox_uses_external_directory_object_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 4, 1, 0, 0)
            write_report(
                root / "Exchange_SharedMailboxes_20260801-010000.csv",
                [
                    {
                        "DisplayName": "Finance",
                        "PrimarySmtpAddress": "finance@example.com",
                        "ExternalDirectoryObjectId": "mbx-1",
                        "HasForwarding": "False",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("shared_mailbox", "mbx-1"),
                self._families(root),
                target,
            )
            props = {
                prop.name: prop.value
                for prop in state.scalar_properties_by_family.get("Exchange_SharedMailboxes", ())
            }
            self.assertEqual(props["PrimarySmtpAddress"], "finance@example.com")

    def test_multi_row_relationships_use_row_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 4, 1, 0, 0)
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
            write_report(
                root / "Entra_Group_User_Memberships_20260801-010000.csv",
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "GroupId": "g-1",
                        "GroupName": "Finance",
                        "MembershipType": "Member",
                    },
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "GroupId": "g-2",
                        "GroupName": "IT",
                        "MembershipType": "Owner",
                    },
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                target,
            )
            relationships = state.relationships_by_family.get("Entra_Group_User_Memberships", ())
            scopes = {rel.row_scope for rel in relationships}
            self.assertIn("GroupId: g-1 / GroupName: Finance", scopes)
            self.assertIn("GroupId: g-2 / GroupName: IT", scopes)

    def test_property_provenance_matches_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 4, 1, 0, 0)
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
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                target,
            )
            for props in state.scalar_properties_by_family.values():
                for prop in props:
                    self.assertEqual(prop.family, "Entra_Users_Properties")

    def test_requested_and_actual_times_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 4, 13, 0, 0)
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
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                target,
            )
            coverage = next(
                item for item in state.coverage if item.family == "Entra_Users_Properties"
            )
            self.assertEqual(coverage.requested_at, target)
            self.assertEqual(coverage.snapshot_at, datetime(2026, 8, 1, 1, 0, 0))
            self.assertEqual(coverage.gap, target - datetime(2026, 8, 1, 1, 0, 0))

    def test_entity_history_handoff_preserves_canonical_key(self):
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
            key = CanonicalEntityKey("user", "user-1")
            state = reconstruct_entity_state(key, self._families(root), datetime(2026, 8, 4, 1, 0, 0))
            self.assertEqual(state.key, key)

    def test_compare_entity_states_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            families = self._families(root)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "R&D",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "IT",
                    }
                ],
            )
            families = self._families(root)
            before = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                families,
                datetime(2026, 7, 15, 1, 0, 0),
            )
            after = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                families,
                datetime(2026, 8, 15, 1, 0, 0),
            )
            diff = compare_entity_states(before, after)
            self.assertEqual(
                diff.modified_properties,
                (("Entra_Users_Properties", "Department", "R&D", "IT"),),
            )

    def test_definitive_absence_with_authoritative_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-010000.csv",
                [
                    {
                        "Id": "user-2",
                        "UPN": "other@example.com",
                        "DisplayName": "Other",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                datetime(2026, 8, 4, 12, 0, 0),
            )
            self.assertEqual(state.presence, "absent")

    def test_partial_when_secondary_absent_without_authoritative_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Group_User_Memberships_20260804-010000.csv",
                [
                    {
                        "UserId": "user-2",
                        "UserPrincipalName": "other@example.com",
                        "GroupId": "g-1",
                        "GroupName": "Finance",
                        "MembershipType": "Member",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                datetime(2026, 8, 4, 12, 0, 0),
            )
            self.assertEqual(state.presence, "partial")

    def test_present_when_one_family_contains_entity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260804-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                datetime(2026, 8, 4, 12, 0, 0),
            )
            self.assertEqual(state.presence, "present")


if __name__ == "__main__":
    unittest.main()
