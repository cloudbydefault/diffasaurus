import unittest
from pathlib import Path


class RoleAssignmentsExporterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.script_text = (
            root / "psscripts" / "_app_ENTRA_Role_Assignments.ps1"
        ).read_text(encoding="utf-8", errors="ignore")

    def test_exporter_emits_stable_identifier_columns(self):
        for column in (
            "UserId",
            "RoleDefinitionId",
            "AssignmentScheduleId",
            "SourcePrincipalId",
            "SourceGroupId",
            "DirectoryScopeId",
            "AppScopeId",
        ):
            with self.subTest(column=column):
                self.assertIn(column, self.script_text)

    def test_exporter_maps_schedule_and_principal_fields(self):
        mappings = {
            "UserId               = $User.id": "effective user object ID",
            "RoleDefinitionId     = $RoleDefinitionId": "role definition ID",
            "AssignmentScheduleId = $Schedule.id": "schedule ID",
            "SourcePrincipalId    = $Schedule.principalId": "schedule principal ID",
            "SourceGroupId        = $GroupId": "group ID for group-derived rows",
            "DirectoryScopeId     = $Schedule.directoryScopeId": "directory scope",
            "AppScopeId           = $Schedule.appScopeId": "app scope",
        }
        for needle, label in mappings.items():
            with self.subTest(label=label):
                self.assertIn(needle, self.script_text)

    def test_exporter_reads_both_schedule_collections(self):
        self.assertIn("roleAssignmentSchedules", self.script_text)
        self.assertIn("roleEligibilitySchedules", self.script_text)

    def test_exporter_preserves_legacy_readable_columns(self):
        for column in (
            "UserPrincipalName",
            "DisplayName",
            "Mail",
            "AccountEnabled",
            "RoleName",
            "RoleState",
            "AssignmentSource",
            "SourceGroup",
        ):
            with self.subTest(column=column):
                self.assertIn(column, self.script_text)
