from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.entity.history import reconstruct_point_in_time_with_enrichment
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.pit_enrichment import (
    AUTOPILOT_FAMILY,
    EnrichedManagedDevice,
    MANAGED_DEVICES_FAMILY,
    RelatedAutopilotState,
    RelatedManagedDevice,
    UserManagedDevicesEnrichment,
    UserPointInTimeEnrichment,
)
from diffasaurus.core.entity.pit_managed_devices_presentation import build_managed_devices_section
from diffasaurus.core.entity.pit_presentation import (
    build_point_in_time_card,
    PointInTimeReconstructionResult,
    single_provenance,
    ProvenanceObservation,
)
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    EntityState,
    FamilyCoverage,
    SourcedProperty,
)
from tests.fixtures.entity_index_generator import write_report

TS = datetime(2026, 7, 30, 5, 2, 0)
TARGET = datetime(2026, 7, 30, 18, 45, 0)
AAD = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MD = "11111111-1111-1111-1111-111111111111"


def _prop(name: str, value: str, family: str = MANAGED_DEVICES_FAMILY) -> SourcedProperty:
    return SourcedProperty(family=family, name=name, value=value, observed_at=TS)


def _managed_device(**fields: str) -> RelatedManagedDevice:
    props = tuple(_prop(k, v) for k, v in fields.items())
    provenance = single_provenance(
        ProvenanceObservation(
            family=MANAGED_DEVICES_FAMILY,
            observed_at=TS,
            snapshot_at=TS,
            requested_at=TARGET,
            gap=TARGET - TS,
        )
    )
    return RelatedManagedDevice(
        device_key=CanonicalEntityKey("device", f"aad:{AAD}"),
        dedup_key="dedup-1",
        properties=props,
        provenance=provenance,
        link_kind="user_id",
        normalized_link_value="user-1",
        resolution_status="resolved",
        resolved_user_immutable_id="user-1",
        candidate_user_ids=frozenset(),
        diagnostic="",
    )


def _enrichment(
    coverage: str,
    devices: tuple[RelatedManagedDevice, ...] = (),
    enriched: tuple[EnrichedManagedDevice, ...] = (),
) -> UserManagedDevicesEnrichment:
    return UserManagedDevicesEnrichment(
        devices=devices,
        coverage=coverage,
        family_coverage=FamilyCoverage(
            family=MANAGED_DEVICES_FAMILY,
            status="snapshot_used",
            requested_at=TARGET,
            snapshot_at=TS,
            gap=TARGET - TS,
            entity_present=True,
            source_relative_path="managed.csv",
            source_report_family=MANAGED_DEVICES_FAMILY,
        ),
        unresolved_observations=(),
        snapshot_at=TS,
        snapshot_file_id=1,
        source_relative_path="managed.csv",
        enriched_devices=enriched,
        autopilot_family_coverage=FamilyCoverage(
            family=AUTOPILOT_FAMILY,
            status="snapshot_used",
            requested_at=TARGET,
            snapshot_at=TS - timedelta(hours=1),
            gap=TARGET - (TS - timedelta(hours=1)),
            entity_present=True,
            source_relative_path="autopilot.csv",
            source_report_family=AUTOPILOT_FAMILY,
        ),
    )


def _user_state() -> EntityState:
    return EntityState(
        as_of=TARGET,
        key=CanonicalEntityKey("user", "user-1"),
        properties_by_family={},
        family_coverage={},
        coverage=(),
        presence="present",
        scalar_properties_by_family={
            "Entra_Users_Properties": (
                _prop("DisplayName", "Ada", "Entra_Users_Properties"),
            ),
        },
        relationships_by_family={},
    )


class ManagedDevicesPresentationTests(unittest.TestCase):
    def test_three_devices_section_count(self):
        devices = tuple(
            _managed_device(DeviceName=f"Laptop-{i}", OperatingSystem="Windows")
            for i in range(3)
        )
        enriched = tuple(
            EnrichedManagedDevice(
                device=d,
                autopilot=RelatedAutopilotState(
                    status="not_applicable",
                    properties=(),
                    provenance=None,
                    key_matches=(),
                    matched_row_index=None,
                    conflict_diagnostic="Non-Windows device",
                ),
            )
            for d in devices
        )
        section = build_managed_devices_section(_enrichment("populated", devices, enriched))
        assert section is not None
        self.assertEqual(section.device_count, 3)
        self.assertEqual(len(section.devices), 3)

    def test_section_order_between_auth_and_roles(self):
        state = _user_state()
        device = _managed_device(DeviceName="Laptop", OperatingSystem="Windows")
        enrichment = UserPointInTimeEnrichment(
            managed_devices=_enrichment(
                "populated",
                (device,),
                (
                    EnrichedManagedDevice(
                        device=device,
                        autopilot=RelatedAutopilotState(
                            status="not_applicable",
                            properties=(),
                            provenance=None,
                            key_matches=(),
                            matched_row_index=None,
                            conflict_diagnostic="",
                        ),
                    ),
                ),
            )
        )
        model = build_point_in_time_card(state, enrichment=enrichment)
        self.assertIsNotNone(model.managed_devices)
        section_ids = [s.section_id for s in model.sections]
        if "roles" in section_ids:
            roles_index = section_ids.index("roles")
            auth_index = section_ids.index("authentication") if "authentication" in section_ids else -1
            if auth_index >= 0:
                self.assertLess(auth_index, roles_index)

    def test_backward_compatible_without_enrichment(self):
        model = build_point_in_time_card(_user_state())
        self.assertIsNone(model.managed_devices)

    def test_device_entity_has_no_managed_section(self):
        state = EntityState(
            as_of=TARGET,
            key=CanonicalEntityKey("device", "aad:1"),
            properties_by_family={},
            family_coverage={},
            coverage=(),
            presence="present",
            scalar_properties_by_family={},
            relationships_by_family={},
        )
        model = build_point_in_time_card(
            state,
            enrichment=UserPointInTimeEnrichment(managed_devices=_enrichment("populated")),
        )
        self.assertIsNone(model.managed_devices)

    def test_known_zero_message(self):
        section = build_managed_devices_section(_enrichment("known_zero"))
        assert section is not None
        self.assertIn("no associated managed devices", section.message.lower())

    def test_no_coverage_not_zero(self):
        section = build_managed_devices_section(
            UserManagedDevicesEnrichment(
                devices=(),
                coverage="no_coverage",
                family_coverage=None,
                unresolved_observations=(),
                snapshot_at=None,
                snapshot_file_id=None,
            )
        )
        assert section is not None
        self.assertIn("no managed-device snapshot", section.message.lower())
        self.assertNotEqual(section.coverage, "known_zero")

    def test_ambiguous_association_not_zero(self):
        section = build_managed_devices_section(_enrichment("ambiguous_association"))
        assert section is not None
        self.assertEqual(section.coverage, "ambiguous_association")

    def test_enrichment_error_section(self):
        section = build_managed_devices_section(None, enrichment_error="db locked")
        assert section is not None
        self.assertEqual(section.coverage, "enrichment_error")
        self.assertIn("unavailable", section.message.lower())

    def test_enrichment_failure_preserves_base_card(self):
        model = build_point_in_time_card(
            _user_state(),
            enrichment_error="worker failed",
        )
        self.assertTrue(model.sections)
        self.assertIsNotNone(model.managed_devices)
        self.assertEqual(model.managed_devices.coverage, "enrichment_error")

    def test_device_field_groups(self):
        device = _managed_device(
            DeviceName="Laptop",
            OperatingSystem="Windows",
            ComplianceState="Compliant",
            SerialNumber="SN-1",
        )
        enriched = EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="matched",
                properties=(_prop("EnrollmentState", "enrolled", AUTOPILOT_FAMILY),),
                provenance=single_provenance(
                    ProvenanceObservation(
                        family=AUTOPILOT_FAMILY,
                        observed_at=TS,
                        snapshot_at=TS,
                        requested_at=TARGET,
                        gap=TARGET - TS,
                    )
                ),
                key_matches=(),
                matched_row_index=0,
                conflict_diagnostic="",
            ),
        )
        section = build_managed_devices_section(
            _enrichment("populated", (device,), (enriched,))
        )
        card = section.devices[0]
        group_ids = [g.group_id for g in card.management_groups]
        self.assertIn("management", group_ids)
        self.assertIn("hardware", group_ids)

    def test_empty_fields_omitted(self):
        device = _managed_device(DeviceName="Laptop", OperatingSystem="Windows")
        enriched = EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="not_applicable",
                properties=(),
                provenance=None,
                key_matches=(),
                matched_row_index=None,
                conflict_diagnostic="",
            ),
        )
        section = build_managed_devices_section(
            _enrichment("populated", (device,), (enriched,))
        )
        all_fields = [
            f.label
            for card in section.devices
            for group in card.management_groups
            for f in group.fields
        ]
        self.assertNotIn("Phone number", all_fields)

    def test_device_ordering_deterministic(self):
        devices = (
            _managed_device(DeviceName="Bravo", OperatingSystem="Windows"),
            _managed_device(DeviceName="Alpha", OperatingSystem="Windows"),
        )
        enriched = tuple(
            EnrichedManagedDevice(
                device=d,
                autopilot=RelatedAutopilotState(
                    status="not_applicable",
                    properties=(),
                    provenance=None,
                    key_matches=(),
                    matched_row_index=None,
                    conflict_diagnostic="",
                ),
            )
            for d in devices
        )
        section = build_managed_devices_section(_enrichment("populated", devices, enriched))
        labels = [card.primary_label for card in section.devices]
        self.assertEqual(labels, ["Alpha", "Bravo"])

    def test_matched_autopilot_fields(self):
        device = _managed_device(DeviceName="Laptop", OperatingSystem="Windows", Model="Latitude 7450")
        enriched = EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="matched",
                properties=(
                    _prop("EnrollmentState", "enrolled", AUTOPILOT_FAMILY),
                    _prop("AssignmentStatus", "Assigned", AUTOPILOT_FAMILY),
                ),
                provenance=single_provenance(
                    ProvenanceObservation(
                        family=AUTOPILOT_FAMILY,
                        observed_at=TS,
                        snapshot_at=TS,
                        requested_at=TARGET,
                        gap=TARGET - TS,
                    )
                ),
                key_matches=(),
                matched_row_index=0,
                conflict_diagnostic="",
            ),
        )
        section = build_managed_devices_section(_enrichment("populated", (device,), (enriched,)))
        autopilot = section.devices[0].autopilot
        assert autopilot is not None
        self.assertEqual(autopilot.status, "matched")
        labels = {f.label for f in autopilot.fields}
        self.assertIn("Enrollment state", labels)

    def test_no_match_warning(self):
        device = _managed_device(DeviceName="Laptop", OperatingSystem="Windows", Model="Latitude 7450")
        enriched = EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="no_match_with_coverage",
                properties=(),
                provenance=single_provenance(
                    ProvenanceObservation(
                        family=AUTOPILOT_FAMILY,
                        observed_at=TS,
                        snapshot_at=TS,
                        requested_at=TARGET,
                        gap=TARGET - TS,
                    )
                ),
                key_matches=(),
                matched_row_index=None,
                conflict_diagnostic="",
            ),
        )
        section = build_managed_devices_section(_enrichment("populated", (device,), (enriched,)))
        autopilot = section.devices[0].autopilot
        assert autopilot is not None
        self.assertTrue(autopilot.show_warning)
        self.assertIn("No matching Autopilot", autopilot.warning_message)

    def test_cloud_pc_no_warning(self):
        device = _managed_device(
            DeviceName="CPC",
            OperatingSystem="Windows",
            Model="Cloud PC Enterprise 4vCPU/16GB/256GB",
        )
        enriched = EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="not_applicable",
                properties=(),
                provenance=None,
                key_matches=(),
                matched_row_index=None,
                conflict_diagnostic="Cloud PC",
            ),
        )
        section = build_managed_devices_section(_enrichment("populated", (device,), (enriched,)))
        self.assertIsNone(section.devices[0].autopilot)

    def test_separate_provenance_in_audit(self):
        device = _managed_device(DeviceName="Laptop", OperatingSystem="Windows")
        enriched = EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="matched",
                properties=(_prop("EnrollmentState", "enrolled", AUTOPILOT_FAMILY),),
                provenance=single_provenance(
                    ProvenanceObservation(
                        family=AUTOPILOT_FAMILY,
                        observed_at=TS - timedelta(hours=1),
                        snapshot_at=TS - timedelta(hours=1),
                        requested_at=TARGET,
                        gap=timedelta(hours=1),
                    )
                ),
                key_matches=(),
                matched_row_index=0,
                conflict_diagnostic="",
            ),
        )
        model = build_point_in_time_card(
            _user_state(),
            enrichment=UserPointInTimeEnrichment(
                managed_devices=_enrichment("populated", (device,), (enriched,))
            ),
        )
        audit = model.source_details.managed_devices_audit
        assert audit is not None
        self.assertNotEqual(audit.snapshot_at, audit.autopilot_snapshot_at)


class PointInTimeWorkerTests(unittest.TestCase):
    def _build(self, root: Path) -> EntityIndexRepository:
        os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
        run_sync(root, cold=True)
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
        repo = EntityIndexRepository.open(root)
        assert repo is not None
        return repo

    def test_repository_reconstruct_includes_enrichment_for_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260730-050200.csv",
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": AAD,
                        "ManagedDeviceId": MD,
                        "DeviceName": "Laptop",
                        "OperatingSystem": "Windows",
                    }
                ],
            )
            repo = self._build(root)
            result = repo.reconstruct_point_in_time(
                CanonicalEntityKey("user", "user-1"),
                TARGET,
            )
            self.assertIsNotNone(result.enrichment)
            self.assertEqual(len(result.enrichment.managed_devices.devices), 1)
            repo.close()

    def test_legacy_reconstruct_includes_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260730-050200.csv",
                [
                    {
                        "UserId": "user-1",
                        "AzureADDeviceId": AAD,
                        "ManagedDeviceId": MD,
                        "DeviceName": "Laptop",
                        "OperatingSystem": "Windows",
                    }
                ],
            )
            from diffasaurus.core.report_history import scan_report_history

            families = scan_report_history(root)
            result = reconstruct_point_in_time_with_enrichment(
                CanonicalEntityKey("user", "user-1"),
                families,
                TARGET,
            )
            self.assertIsNotNone(result.enrichment)

    def test_device_entity_skips_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260730-050200.csv",
                [
                    {
                        "AzureADDeviceId": AAD,
                        "ManagedDeviceId": MD,
                        "DeviceName": "Laptop",
                    }
                ],
            )
            repo = self._build(root)
            result = repo.reconstruct_point_in_time(
                CanonicalEntityKey("device", f"aad:{AAD}"),
                TARGET,
            )
            self.assertIsNone(result.enrichment)
            repo.close()

    def test_enrichment_failure_preserves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            repo = self._build(root)
            with patch.object(
                repo,
                "enrich_user_point_in_time",
                side_effect=RuntimeError("enrichment failed"),
            ):
                result = repo.reconstruct_point_in_time(
                    CanonicalEntityKey("user", "user-1"),
                    TARGET,
                )
            self.assertIsNone(result.enrichment)
            self.assertIn("enrichment failed", result.enrichment_error or "")
            self.assertEqual(result.state.key.primary_id, "user-1")
            repo.close()


if __name__ == "__main__":
    unittest.main()
