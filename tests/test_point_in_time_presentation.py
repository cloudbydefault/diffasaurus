from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from diffasaurus.core.entity.pit_presentation import (
    build_point_in_time_card,
    merge_provenance,
    provenance_observation_sort_key,
    single_provenance,
    ProvenanceObservation,
    SourceProvenance,
)
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    EntityState,
    FamilyCoverage,
    ScopedRelationship,
    SourcedProperty,
)


def _prop(family: str, name: str, value: str, observed_at: datetime | None = None) -> SourcedProperty:
    return SourcedProperty(
        family=family,
        name=name,
        value=value,
        observed_at=observed_at or datetime(2026, 8, 1, 12, 0, 0),
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


def _relationship(
    family: str,
    properties: dict[str, str],
    *,
    row_scope: str = "",
    observed_at: datetime | None = None,
) -> ScopedRelationship:
    props = tuple(
        _prop(family, name, value, observed_at or datetime(2026, 8, 1, 12, 0, 0))
        for name, value in properties.items()
    )
    return ScopedRelationship(
        family=family,
        row_scope=row_scope,
        properties=props,
        observed_at=observed_at or datetime(2026, 8, 1, 12, 0, 0),
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


class PointInTimePresentationTests(unittest.TestCase):
    def test_duplicate_upn_and_display_name_merge(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (
                    _prop("Entra_Users_Properties", "UPN", "ada@example.com"),
                    _prop("Entra_Users_Properties", "DisplayName", "Ada"),
                ),
                "Entra_Users_Activity": (
                    _prop("Entra_Users_Activity", "UPN", "ada@example.com"),
                    _prop("Entra_Users_Activity", "DisplayName", "Ada"),
                ),
            },
            coverage=(
                _coverage("Entra_Users_Properties", "snapshot_used"),
                _coverage("Entra_Users_Activity", "snapshot_used"),
            ),
        )
        model = build_point_in_time_card(state)
        upn = _field(model, "upn")
        display = _field(model, "display_name")
        self.assertIsNotNone(upn)
        self.assertIsNotNone(display)
        self.assertEqual(upn.display_value, "ada@example.com")
        self.assertEqual(display.display_value, "Ada")
        self.assertEqual(len(upn.provenance.observations), 2)

    def test_multi_family_same_value_separate_provenance_observations(self):
        t1 = datetime(2026, 8, 1, 10, 0, 0)
        t2 = datetime(2026, 8, 1, 11, 0, 0)
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (
                    _prop("Entra_Users_Properties", "UPN", "ada@example.com", t1),
                ),
                "Entra_Users_Activity": (
                    _prop("Entra_Users_Activity", "UPN", "ada@example.com", t2),
                ),
            },
            coverage=(
                _coverage("Entra_Users_Properties", "snapshot_used", snapshot_at=t1),
                _coverage("Entra_Users_Activity", "snapshot_used", snapshot_at=t2),
            ),
        )
        model = build_point_in_time_card(state)
        upn = _field(model, "upn")
        self.assertEqual(len(upn.provenance.observations), 2)
        families = {obs.family for obs in upn.provenance.observations}
        self.assertEqual(families, {"Entra_Users_Properties", "Entra_Users_Activity"})
        sorted_obs = sorted(upn.provenance.observations, key=provenance_observation_sort_key)
        self.assertEqual(upn.provenance.observations, tuple(sorted_obs))

    def test_authority_properties_wins_over_activity_for_department(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (_prop("Entra_Users_Properties", "Department", "R&D"),),
                "Entra_Users_Activity": (_prop("Entra_Users_Activity", "Department", "IT"),),
            },
            coverage=(
                _coverage("Entra_Users_Properties", "snapshot_used"),
                _coverage("Entra_Users_Activity", "snapshot_used"),
            ),
        )
        model = build_point_in_time_card(state)
        department = _field(model, "department")
        self.assertEqual(department.display_value, "R&D")
        self.assertIsNotNone(department.conflict)
        self.assertEqual(department.conflict.alternates[0].value, "IT")

    def test_conflicting_values_create_field_conflict(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (_prop("Entra_Users_Properties", "Department", "R&D"),),
                "Entra_Users_Activity": (_prop("Entra_Users_Activity", "Department", "IT"),),
            },
            coverage=(
                _coverage("Entra_Users_Properties", "snapshot_used"),
                _coverage("Entra_Users_Activity", "snapshot_used"),
            ),
        )
        model = build_point_in_time_card(state)
        department = _field(model, "department")
        self.assertEqual(len(department.conflict.alternates), 1)
        self.assertEqual(department.conflict.alternates[0].observation.family, "Entra_Users_Activity")

    def test_empty_scalars_omitted(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (
                    _prop("Entra_Users_Properties", "UPN", "   "),
                    _prop("Entra_Users_Properties", "DisplayName", "Ada"),
                ),
            },
            coverage=(_coverage("Entra_Users_Properties", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        self.assertIsNone(_field(model, "upn"))
        self.assertIsNotNone(_field(model, "display_name"))

    def test_membership_user_id_does_not_merge_with_user_immutable_id(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (_prop("Entra_Users_Properties", "Id", "user-1"),),
                "Entra_Group_User_Memberships": (
                    _prop("Entra_Group_User_Memberships", "UserId", "other-id"),
                ),
            },
            coverage=(_coverage("Entra_Users_Properties", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        self.assertIsNotNone(_field(model, "user_immutable_id"))
        self.assertEqual(_field(model, "user_immutable_id").display_value, "user-1")

    def test_display_name_from_unrelated_families_does_not_merge_without_binding(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (_prop("Entra_Users_Properties", "DisplayName", "Ada"),),
            },
            coverage=(_coverage("Entra_Users_Properties", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        display = _field(model, "display_name")
        self.assertEqual(display.display_value, "Ada")
        self.assertEqual(len(display.provenance.observations), 1)

    def test_three_role_relationships(self):
        roles = tuple(
            _relationship(
                "Entra_Role_Assignments",
                {"RoleName": f"Role {index}", "RoleState": "Active"},
            )
            for index in range(3)
        )
        state = _user_state(
            relationships_by_family={"Entra_Role_Assignments": roles},
            coverage=(_coverage("Entra_Role_Assignments", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "roles")
        self.assertEqual(len(collection.items), 3)
        self.assertEqual(collection.items[0].primary_label, "Role 0")
        self.assertEqual(collection.items[0].secondary_label, "Active")

    def test_duplicate_role_rows_deduped(self):
        roles = (
            _relationship("Entra_Role_Assignments", {"RoleName": "Admin", "RoleState": "Active"}),
            _relationship("Entra_Role_Assignments", {"RoleName": "Admin", "RoleState": "Eligible"}),
        )
        state = _user_state(
            relationships_by_family={"Entra_Role_Assignments": roles},
            coverage=(_coverage("Entra_Role_Assignments", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "roles")
        self.assertEqual(len(collection.items), 1)
        self.assertEqual(collection.items[0].secondary_label, "Active")

    def test_three_membership_rows_populated(self):
        memberships = tuple(
            _relationship(
                "Entra_Group_User_Memberships",
                {"GroupName": f"Group {index}", "MembershipType": "Member"},
                row_scope=f"GroupId: g-{index} / GroupName: Group {index}",
            )
            for index in range(3)
        )
        state = _user_state(
            relationships_by_family={"Entra_Group_User_Memberships": memberships},
            coverage=(_coverage("Entra_Group_User_Memberships", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "groups")
        self.assertEqual(collection.coverage, "populated")
        self.assertEqual(len(collection.items), 3)

    def test_entity_absent_with_snapshot_is_known_empty(self):
        state = _user_state(
            relationships_by_family={},
            coverage=(
                _coverage(
                    "Entra_Group_User_Memberships",
                    "entity_absent",
                    snapshot_at=datetime(2026, 8, 1, 10, 0, 0),
                ),
            ),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "groups")
        self.assertEqual(collection.coverage, "known_empty")
        self.assertEqual(len(collection.items), 0)

    def test_no_snapshot_is_no_coverage(self):
        state = _user_state(
            relationships_by_family={},
            coverage=(
                _coverage(
                    "Entra_Group_User_Memberships",
                    "no_snapshot",
                    snapshot_at=None,
                    gap=None,
                ),
            ),
        )
        model = build_point_in_time_card(state)
        self.assertIsNone(_collection(model, "groups"))

    def test_snapshot_at_none_is_no_coverage(self):
        state = _user_state(
            relationships_by_family={},
            coverage=(
                _coverage(
                    "Entra_Group_User_Memberships",
                    "snapshot_used",
                    snapshot_at=None,
                ),
            ),
        )
        model = build_point_in_time_card(state)
        self.assertIsNone(_collection(model, "groups"))

    def test_missing_family_coverage_is_unknown(self):
        state = _user_state(relationships_by_family={}, coverage=())
        model = build_point_in_time_card(state)
        self.assertIsNone(_collection(model, "groups"))

    def test_group_dedup_prefers_group_id(self):
        memberships = (
            _relationship(
                "Entra_Group_User_Memberships",
                {"GroupName": "Finance", "MembershipType": "Member"},
                row_scope="GroupId: g-1 / GroupName: Finance",
            ),
            _relationship(
                "Entra_Group_User_Memberships",
                {"GroupName": "Finance Duplicate", "MembershipType": "Member"},
                row_scope="GroupId: g-1 / GroupName: Finance Duplicate",
            ),
        )
        state = _user_state(
            relationships_by_family={"Entra_Group_User_Memberships": memberships},
            coverage=(_coverage("Entra_Group_User_Memberships", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "groups")
        self.assertEqual(len(collection.items), 1)
        self.assertEqual(collection.items[0].detail, "g-1")

    def test_groups_only_from_membership_family(self):
        state = _user_state(
            relationships_by_family={
                "Entra_Group_User_Memberships": (
                    _relationship(
                        "Entra_Group_User_Memberships",
                        {"GroupName": "Finance", "MembershipType": "Member"},
                        row_scope="GroupId: g-1 / GroupName: Finance",
                    ),
                ),
                "Entra_Role_Assignments": (
                    _relationship(
                        "Entra_Role_Assignments",
                        {"RoleName": "Admin", "RoleState": "Active"},
                    ),
                ),
            },
            coverage=(
                _coverage("Entra_Group_User_Memberships", "snapshot_used"),
                _coverage("Entra_Role_Assignments", "snapshot_used"),
            ),
        )
        model = build_point_in_time_card(state)
        groups = _collection(model, "groups")
        roles = _collection(model, "roles")
        self.assertEqual(len(groups.items), 1)
        self.assertEqual(groups.items[0].primary_label, "Finance")
        self.assertEqual(len(roles.items), 1)

    def test_roles_and_access_packages_separate_collections(self):
        state = _user_state(
            relationships_by_family={
                "Entra_Role_Assignments": (
                    _relationship("Entra_Role_Assignments", {"RoleName": "Admin", "RoleState": "Active"}),
                ),
                "Entra_AccessPackage_User_Assignments": (
                    _relationship(
                        "Entra_AccessPackage_User_Assignments",
                        {"AccessPackageName": "Pkg", "AssignmentState": "Assigned"},
                        row_scope="AccessPackageId: ap-1 / AccessPackageName: Pkg",
                    ),
                ),
            },
            coverage=(
                _coverage("Entra_Role_Assignments", "snapshot_used"),
                _coverage("Entra_AccessPackage_User_Assignments", "snapshot_used"),
            ),
        )
        model = build_point_in_time_card(state)
        self.assertIsNotNone(_collection(model, "roles"))
        self.assertIsNotNone(_collection(model, "access_packages"))

    def test_source_details_retains_raw_maps(self):
        state = _user_state(
            scalar_properties_by_family={
                "Entra_Users_Properties": (_prop("Entra_Users_Properties", "UPN", "ada@example.com"),),
            },
            relationships_by_family={
                "Entra_Role_Assignments": (
                    _relationship("Entra_Role_Assignments", {"RoleName": "Admin", "RoleState": "Active"}),
                ),
            },
            coverage=(_coverage("Entra_Users_Properties", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        self.assertIn("Entra_Users_Properties", model.source_details.scalar_properties_by_family)
        self.assertIn("Entra_Role_Assignments", model.source_details.relationships_by_family)
        self.assertEqual(len(model.source_details.coverage), 1)

    def test_entity_types_have_different_sections(self):
        user_model = build_point_in_time_card(
            _user_state(
                scalar_properties_by_family={
                    "Entra_Users_Properties": (_prop("Entra_Users_Properties", "UPN", "ada@example.com"),),
                },
                relationships_by_family={
                    "Entra_Role_Assignments": (
                        _relationship("Entra_Role_Assignments", {"RoleName": "Admin", "RoleState": "Active"}),
                    ),
                },
                coverage=(
                    _coverage("Entra_Users_Properties", "snapshot_used"),
                    _coverage("Entra_Role_Assignments", "snapshot_used"),
                ),
            )
        )
        device_model = build_point_in_time_card(
            EntityState(
                as_of=datetime(2026, 8, 1, 12, 0, 0),
                key=CanonicalEntityKey("device", "aad:device-1"),
                properties_by_family={},
                family_coverage={},
                coverage=(_coverage("Intune_ManagedDevices_Compliance", "snapshot_used"),),
                presence="present",
                scalar_properties_by_family={
                    "Intune_ManagedDevices_Compliance": (
                        _prop("Intune_ManagedDevices_Compliance", "DeviceName", "Laptop"),
                    ),
                },
            )
        )
        mailbox_model = build_point_in_time_card(
            EntityState(
                as_of=datetime(2026, 8, 1, 12, 0, 0),
                key=CanonicalEntityKey("shared_mailbox", "mb-1"),
                properties_by_family={},
                family_coverage={},
                coverage=(_coverage("Exchange_SharedMailboxes", "snapshot_used"),),
                presence="present",
                scalar_properties_by_family={
                    "Exchange_SharedMailboxes": (
                        _prop("Exchange_SharedMailboxes", "DisplayName", "Support"),
                    ),
                },
            )
        )
        user_sections = {section.section_id for section in user_model.sections}
        device_sections = {section.section_id for section in device_model.sections}
        mailbox_sections = {section.section_id for section in mailbox_model.sections}
        self.assertIn("roles", user_sections)
        self.assertNotIn("roles", device_sections)
        self.assertNotIn("roles", mailbox_sections)

    def test_fifty_group_model_sorted_and_deduped(self):
        memberships = []
        for index in range(55):
            group_id = f"g-{index % 50}"
            memberships.append(
                _relationship(
                    "Entra_Group_User_Memberships",
                    {"GroupName": f"Group {index:02d}", "MembershipType": "Member"},
                    row_scope=f"GroupId: {group_id} / GroupName: Group {index:02d}",
                )
            )
        state = _user_state(
            relationships_by_family={"Entra_Group_User_Memberships": tuple(memberships)},
            coverage=(_coverage("Entra_Group_User_Memberships", "snapshot_used"),),
        )
        model = build_point_in_time_card(state)
        collection = _collection(model, "groups")
        self.assertEqual(len(collection.items), 50)
        names = [item.primary_label for item in collection.items]
        self.assertEqual(names, sorted(names, key=str.casefold))

    def test_merge_provenance_dedupes_identical_observations(self):
        requested = datetime(2026, 8, 1, 12, 0, 0)
        obs = ProvenanceObservation(
            family="Entra_Users_Properties",
            observed_at=requested,
            snapshot_at=requested,
            requested_at=requested,
            gap=timedelta(hours=1),
        )
        merged = merge_provenance(
            SourceProvenance(observations=(obs,)),
            SourceProvenance(observations=(obs,)),
        )
        self.assertEqual(len(merged.observations), 1)


if __name__ == "__main__":
    unittest.main()
