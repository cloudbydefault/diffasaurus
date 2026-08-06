from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from diffasaurus.core.entity.history import reconstruct_entity_state
from diffasaurus.core.entity.pit_auth_methods import (
    AUTH_METHODS_FAMILY,
    build_parsed_auth_methods,
    parse_auth_method_tokens,
)
from diffasaurus.core.entity.pit_presentation import build_point_in_time_card
from diffasaurus.core.entity.snapshots import clear_parse_cache
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    EntityState,
    FamilyCoverage,
    ScopedRelationship,
    SourcedProperty,
)
from diffasaurus.core.report_history import scan_report_history


def write_report(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _prop(
    family: str,
    name: str,
    value: str,
    observed_at: datetime | None = None,
) -> SourcedProperty:
    return SourcedProperty(
        family=family,
        name=name,
        value=value,
        observed_at=observed_at or datetime(2026, 8, 1, 10, 0, 0),
    )


def _coverage(
    family: str,
    status: str,
    *,
    snapshot_at: datetime | None = datetime(2026, 8, 1, 10, 0, 0),
    gap: timedelta | None = timedelta(hours=2),
    entity_present: bool = True,
) -> FamilyCoverage:
    return FamilyCoverage(
        family=family,
        status=status,
        requested_at=datetime(2026, 8, 1, 12, 0, 0),
        snapshot_at=snapshot_at,
        gap=gap,
        entity_present=entity_present,
    )


def _user_state(**kwargs) -> EntityState:
    defaults = {
        "as_of": datetime(2026, 8, 1, 12, 0, 0),
        "key": CanonicalEntityKey("user", "user-1"),
        "properties_by_family": {},
        "family_coverage": {},
        "coverage": (),
        "presence": "present",
        "scalar_properties_by_family": {},
        "relationships_by_family": {},
    }
    defaults.update(kwargs)
    return EntityState(**defaults)


def _field(model, key: str):
    for section in model.sections:
        for field in section.fields:
            if field.normalized_key == key:
                return field
    return None


def _collection(model, collection_id: str):
    for section in model.sections:
        for collection in section.collections:
            if collection.collection_id == collection_id:
                return collection
    return None


class ParseAuthMethodTokensTests(unittest.TestCase):
    def test_semicolon_no_spaces(self):
        self.assertEqual(
            parse_auth_method_tokens("Authenticator;FIDO2;TAP"),
            ("Authenticator", "FIDO2", "TAP"),
        )

    def test_spaces_around_delimiters(self):
        self.assertEqual(
            parse_auth_method_tokens("Authenticator ; FIDO2 ; TAP"),
            ("Authenticator", "FIDO2", "TAP"),
        )

    def test_mixed_spacing(self):
        self.assertEqual(
            parse_auth_method_tokens("Authenticator; FIDO2; TAP"),
            ("Authenticator", "FIDO2", "TAP"),
        )

    def test_blank_entries_omitted(self):
        self.assertEqual(parse_auth_method_tokens("A;;B; ;C"), ("A", "B", "C"))

    def test_case_insensitive_dedupe_preserves_first_spelling(self):
        self.assertEqual(
            parse_auth_method_tokens("FIDO2;fido2;FIDO2 Security Key"),
            ("FIDO2", "FIDO2 Security Key"),
        )

    def test_order_preserved(self):
        self.assertEqual(
            parse_auth_method_tokens("TAP;Authenticator;FIDO2"),
            ("TAP", "Authenticator", "FIDO2"),
        )


class ParsedAuthMethodsTests(unittest.TestCase):
    def test_equivalent_sources_merge(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "AuthenticationMethods", "email; phone"),
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "email; phone"),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        parsed = build_parsed_auth_methods(state)
        assert parsed is not None
        self.assertEqual(parsed.coverage, "populated")
        self.assertEqual(parsed.methods, ("email", "phone"))
        self.assertFalse(parsed.has_conflict)
        self.assertEqual(len(parsed.sources), 2)

    def test_conflicting_sources_retain_metadata(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "AuthenticationMethods", "email"),
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "phone"),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        parsed = build_parsed_auth_methods(state)
        assert parsed is not None
        self.assertTrue(parsed.has_conflict)
        assert parsed.conflict is not None
        self.assertEqual(parsed.conflict.authoritative_property, "MethodsRegistered")
        self.assertEqual(parsed.conflict.authoritative_methods, ("phone",))
        self.assertEqual(parsed.methods, ("phone",))
        self.assertEqual(len(parsed.conflict.alternates), 1)
        self.assertEqual(parsed.conflict.alternates[0].property_name, "AuthenticationMethods")

    def test_known_empty(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "AuthenticationMethods", "   "),
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", ""),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        parsed = build_parsed_auth_methods(state)
        assert parsed is not None
        self.assertEqual(parsed.coverage, "known_empty")
        self.assertEqual(parsed.methods, ())

    def test_no_coverage(self):
        state = _user_state(
            coverage=(
                _coverage(
                    AUTH_METHODS_FAMILY,
                    "no_snapshot",
                    snapshot_at=None,
                    gap=None,
                    entity_present=False,
                ),
            ),
        )
        parsed = build_parsed_auth_methods(state)
        assert parsed is not None
        self.assertEqual(parsed.coverage, "no_coverage")

    def test_unknown_when_entity_absent(self):
        state = _user_state(
            coverage=(
                _coverage(
                    AUTH_METHODS_FAMILY,
                    "entity_absent",
                    entity_present=False,
                ),
            ),
        )
        parsed = build_parsed_auth_methods(state)
        assert parsed is not None
        self.assertEqual(parsed.coverage, "unknown")

    def test_sources_retain_raw_and_provenance(self):
        observed = datetime(2026, 8, 1, 10, 0, 0)
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "email", observed),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used", snapshot_at=observed),),
        )
        parsed = build_parsed_auth_methods(state)
        assert parsed is not None
        self.assertEqual(len(parsed.sources), 1)
        self.assertEqual(parsed.sources[0].raw_value, "email")
        self.assertEqual(parsed.sources[0].property_name, "MethodsRegistered")
        self.assertEqual(len(parsed.sources[0].provenance.observations), 1)


class AuthMethodsPresentationTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def test_collection_shows_three_methods(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "Authenticator;FIDO2;TAP"),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "authentication_methods")
        self.assertIsNotNone(collection)
        assert collection is not None
        self.assertEqual(collection.coverage, "populated")
        self.assertEqual(len(collection.items), 3)
        labels = [item.primary_label for item in collection.items]
        self.assertEqual(labels, ["Authenticator", "FIDO2", "TAP"])

    def test_no_duplicate_scalar_on_main_card(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "AuthenticationMethods", "email; phone"),
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "email; phone"),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        self.assertIsNone(_field(model, "authentication_methods"))
        collection = _collection(model, "authentication_methods")
        self.assertIsNotNone(collection)

    def test_known_empty_collection_count_zero(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", ""),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "authentication_methods")
        self.assertIsNotNone(collection)
        assert collection is not None
        self.assertEqual(collection.coverage, "known_empty")
        self.assertEqual(len(collection.items), 0)

    def test_no_coverage_omits_collection(self):
        state = _user_state(
            coverage=(
                _coverage(
                    AUTH_METHODS_FAMILY,
                    "no_snapshot",
                    snapshot_at=None,
                    gap=None,
                    entity_present=False,
                ),
            ),
        )
        model = build_point_in_time_card(state)
        self.assertIsNone(_collection(model, "authentication_methods"))

    def test_unknown_omits_collection(self):
        state = _user_state(
            coverage=(_coverage(AUTH_METHODS_FAMILY, "entity_absent", entity_present=False),),
        )
        model = build_point_in_time_card(state)
        self.assertIsNone(_collection(model, "authentication_methods"))

    def test_mfa_sspr_scalars_remain_visible(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "IsMfaRegistered", "True"),
                    _prop(AUTH_METHODS_FAMILY, "IsMfaCapable", "True"),
                    _prop(AUTH_METHODS_FAMILY, "IsPasswordlessCapable", "False"),
                    _prop(AUTH_METHODS_FAMILY, "IsSsprRegistered", "True"),
                    _prop(AUTH_METHODS_FAMILY, "IsSsprEnabled", "True"),
                    _prop(AUTH_METHODS_FAMILY, "IsSsprCapable", "True"),
                    _prop(AUTH_METHODS_FAMILY, "DefaultMfaMethod", "microsoftAuthenticator"),
                    _prop(
                        AUTH_METHODS_FAMILY,
                        "UserPreferredMethodForSecondaryAuthentication",
                        "phone",
                    ),
                    _prop(AUTH_METHODS_FAMILY, "SystemPreferredAuthenticationMethod", "push"),
                    _prop(AUTH_METHODS_FAMILY, "LastUpdatedDateTime", "2026-08-01 10:00:00"),
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "email"),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        self.assertIsNotNone(_field(model, "mfa_registered"))
        self.assertIsNotNone(_field(model, "mfa_capable"))
        self.assertIsNotNone(_field(model, "passwordless_capable"))
        self.assertIsNotNone(_field(model, "sspr_registered"))
        self.assertIsNotNone(_field(model, "sspr_enabled"))
        self.assertIsNotNone(_field(model, "sspr_capable"))
        self.assertIsNotNone(_field(model, "default_mfa_method"))
        self.assertIsNotNone(_field(model, "user_preferred_secondary_auth"))
        self.assertIsNotNone(_field(model, "system_preferred_auth_method"))
        self.assertIsNotNone(_field(model, "auth_report_last_updated"))

    def test_source_details_retain_raw_observations(self):
        state = _user_state(
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "AuthenticationMethods", "email"),
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "phone"),
                ),
            },
            coverage=(_coverage(AUTH_METHODS_FAMILY, "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        auth_props = model.source_details.scalar_properties_by_family.get(AUTH_METHODS_FAMILY, ())
        names = {prop.name for prop in auth_props}
        self.assertIn("AuthenticationMethods", names)
        self.assertIn("MethodsRegistered", names)
        parsed = model.source_details.auth_methods
        assert parsed is not None
        self.assertTrue(parsed.has_conflict)
        self.assertEqual(len(parsed.sources), 2)

    def test_roles_groups_access_packages_unchanged(self):
        roles = _user_state(
            relationships_by_family={
                "Entra_Role_Assignments": (
                    ScopedRelationship(
                        family="Entra_Role_Assignments",
                        row_scope="RoleName: Admin",
                        properties=(_prop("Entra_Role_Assignments", "RoleName", "Admin"),),
                        observed_at=datetime(2026, 8, 1, 10, 0, 0),
                    ),
                ),
                "Entra_Group_User_Memberships": (
                    ScopedRelationship(
                        family="Entra_Group_User_Memberships",
                        row_scope="GroupId: g-1",
                        properties=(_prop("Entra_Group_User_Memberships", "GroupName", "Finance"),),
                        observed_at=datetime(2026, 8, 1, 10, 0, 0),
                    ),
                ),
                "Entra_AccessPackage_User_Assignments": (
                    ScopedRelationship(
                        family="Entra_AccessPackage_User_Assignments",
                        row_scope="AccessPackageId: ap-1",
                        properties=(
                            _prop("Entra_AccessPackage_User_Assignments", "AccessPackageName", "Pack"),
                        ),
                        observed_at=datetime(2026, 8, 1, 10, 0, 0),
                    ),
                ),
            },
            scalar_properties_by_family={
                AUTH_METHODS_FAMILY: (
                    _prop(AUTH_METHODS_FAMILY, "MethodsRegistered", "email"),
                ),
            },
            coverage=(
                _coverage(AUTH_METHODS_FAMILY, "snapshot_used"),
                _coverage("Entra_Role_Assignments", "snapshot_used"),
                _coverage("Entra_Group_User_Memberships", "snapshot_used"),
                _coverage("Entra_AccessPackage_User_Assignments", "snapshot_used"),
            ),
        )
        model = build_point_in_time_card(roles)
        self.assertIsNotNone(_collection(model, "roles"))
        self.assertIsNotNone(_collection(model, "groups"))
        self.assertIsNotNone(_collection(model, "access_packages"))
        self.assertIsNotNone(_collection(model, "authentication_methods"))

    def test_backward_compatible_without_auth_family(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (
                    _prop("Entra_Users_Properties", "UPN", "ada@example.com"),
                ),
            },
            coverage=(_coverage("Entra_Users_Properties", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        self.assertIsNotNone(_field(model, "upn"))
        self.assertIsNone(_collection(model, "authentication_methods"))
        assert model.source_details.auth_methods is not None
        self.assertEqual(model.source_details.auth_methods.coverage, "no_coverage")

    def test_end_to_end_from_reports(self):
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
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260801-010000.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "IsMfaRegistered": "True",
                        "AuthenticationMethods": "microsoftAuthenticator ; email",
                        "MethodsRegistered": "microsoftAuthenticator ; email",
                        "DefaultMfaMethod": "microsoftAuthenticator",
                    }
                ],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                scan_report_history(root),
                datetime(2026, 8, 1, 12, 0, 0),
            )
            model = build_point_in_time_card(state)
            collection = _collection(model, "authentication_methods")
            self.assertIsNotNone(collection)
            assert collection is not None
            self.assertEqual(len(collection.items), 2)
            self.assertIsNotNone(_field(model, "mfa_registered"))


if __name__ == "__main__":
    unittest.main()
