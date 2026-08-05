from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from diffasaurus.core.entity.adapters import USER_ACTIVITY, USER_AUTH_METHODS, USER_PROPERTIES
from diffasaurus.core.entity.history import reconstruct_entity_state
from diffasaurus.core.entity.pit_field_registry import is_scalar_excluded, lookup_property_binding
from diffasaurus.core.entity.pit_presentation import build_point_in_time_card
from diffasaurus.core.entity.snapshots import clear_parse_cache
from diffasaurus.core.entity.types import CanonicalEntityKey, EntityState, FamilyCoverage, SourcedProperty
from diffasaurus.core.report_history import scan_report_history


def write_report(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _field(model, key: str):
    for section in model.sections:
        for field in section.fields:
            if field.normalized_key == key:
                return field
    return None


def _section(model, section_id: str):
    for section in model.sections:
        if section.section_id == section_id:
            return section
    return None


class PitUserFieldCoverageTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def test_card_properties_includes_explicit_canonical_columns(self):
        row = {"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}
        props = dict(USER_PROPERTIES.card_properties(row, datetime(2026, 8, 1)))
        self.assertEqual(props["Id"], "user-1")
        activity = dict(USER_ACTIVITY.card_properties({**row, "UserId": "user-1"}, datetime(2026, 8, 1)))
        self.assertEqual(activity["UserId"], "user-1")

    def test_reconstructed_user_state_includes_expanded_scalar_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "CreatedDateTime": "2024-01-02 10:00:00",
                        "ManagerDisplayName": "Bob",
                        "Country": "NO",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Activity_20260801-010000.csv",
                [
                    {
                        "UserId": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "LastInteractiveSignInDateTime": "2026-07-01 08:00:00",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260801-010000.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "IsMfaRegistered": "True",
                        "AuthenticationMethods": "microsoftAuthenticator ; email",
                        "DefaultMfaMethod": "microsoftAuthenticator",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                scan_report_history(root),
                datetime(2026, 8, 1, 12, 0, 0),
            )
            prop_names = {prop.name for prop in state.scalar_properties_by_family["Entra_Users_Properties"]}
            activity_names = {prop.name for prop in state.scalar_properties_by_family["Entra_Users_Activity"]}
            auth_names = {prop.name for prop in state.scalar_properties_by_family["Entra_Users_AuthenticationMethods"]}
            self.assertIn("Id", prop_names)
            self.assertIn("CreatedDateTime", prop_names)
            self.assertIn("ManagerDisplayName", prop_names)
            self.assertIn("LastInteractiveSignInDateTime", activity_names)
            self.assertIn("IsMfaRegistered", auth_names)
            self.assertIn("AuthenticationMethods", auth_names)

    def test_primary_card_shows_identity_activity_and_auth_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "CreatedDateTime": "2024-01-02 10:00:00",
                        "ManagerDisplayName": "Bob",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Activity_20260801-010000.csv",
                [
                    {
                        "UserId": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "LastInteractiveSignInDateTime": "2026-07-01 08:00:00",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260801-010000.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "IsMfaRegistered": "True",
                        "AuthenticationMethods": "microsoftAuthenticator",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                scan_report_history(root),
                datetime(2026, 8, 1, 12, 0, 0),
            )
            model = build_point_in_time_card(state)
            self.assertIsNotNone(_field(model, "user_immutable_id"))
            self.assertIsNotNone(_field(model, "created_date"))
            self.assertIsNotNone(_field(model, "manager_display_name"))
            self.assertIsNotNone(_field(model, "last_interactive_sign_in"))
            self.assertIsNotNone(_field(model, "mfa_registered"))
            self.assertIsNotNone(_field(model, "authentication_methods"))
            auth_section = _section(model, "authentication")
            self.assertIsNotNone(auth_section)
            self.assertEqual(auth_section.title, "Authentication and activity")

    def test_unmapped_scalar_uses_additional_details(self):
        state = EntityState(
            as_of=datetime(2026, 8, 1, 12, 0, 0),
            key=CanonicalEntityKey("user", "user-1"),
            properties_by_family={},
            family_coverage={},
            coverage=(
                FamilyCoverage(
                    family="Entra_Users_Properties",
                    status="snapshot_used",
                    requested_at=datetime(2026, 8, 1, 12, 0, 0),
                    snapshot_at=datetime(2026, 8, 1, 10, 0, 0),
                    gap=None,
                    entity_present=True,
                ),
            ),
            presence="present",
            scalar_properties_by_family={
                "Entra_Users_Properties": (
                    SourcedProperty(
                        family="Entra_Users_Properties",
                        name="FutureField",
                        value="future-value",
                        observed_at=datetime(2026, 8, 1, 10, 0, 0),
                    ),
                ),
            },
        )
        model = build_point_in_time_card(state)
        additional = _section(model, "additional_details")
        self.assertIsNotNone(additional)
        keys = {field.normalized_key for field in additional.fields}
        self.assertIn("additional:Entra_Users_Properties:FutureField", keys)

    def test_join_keys_excluded_from_card_and_fallback(self):
        self.assertTrue(
            is_scalar_excluded("user", "Entra_Group_User_Memberships", "UserId")
        )
        self.assertIsNone(
            lookup_property_binding("user", "Entra_Group_User_Memberships", "UserId")
        )

    def test_duplicate_join_keys_not_repeated_in_additional_details(self):
        state = EntityState(
            as_of=datetime(2026, 8, 1, 12, 0, 0),
            key=CanonicalEntityKey("user", "user-1"),
            properties_by_family={},
            family_coverage={},
            coverage=(),
            presence="present",
            scalar_properties_by_family={
                "Entra_Users_Properties": (
                    SourcedProperty("Entra_Users_Properties", "UPN", "ada@example.com", datetime(2026, 8, 1)),
                    SourcedProperty("Entra_Users_Properties", "DisplayName", "Ada", datetime(2026, 8, 1)),
                ),
            },
        )
        model = build_point_in_time_card(state)
        self.assertIsNone(_section(model, "additional_details"))
        self.assertEqual(_field(model, "upn").display_value, "ada@example.com")

    def test_auth_methods_are_scalar_not_relationships(self):
        self.assertFalse(USER_AUTH_METHODS.row_scope_columns)
        row = {
            "UPN": "ada@example.com",
            "DisplayName": "Ada",
            "AuthenticationMethods": "email",
        }
        props = dict(USER_AUTH_METHODS.card_properties(row, datetime(2026, 8, 1)))
        self.assertIn("AuthenticationMethods", props)


if __name__ == "__main__":
    unittest.main()
