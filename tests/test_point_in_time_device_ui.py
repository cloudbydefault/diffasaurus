from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from diffasaurus.core.entity.pit_enrichment import (
    EnrichedManagedDevice,
    RelatedAutopilotState,
    RelatedManagedDevice,
    UserManagedDevicesEnrichment,
    UserPointInTimeEnrichment,
)
from diffasaurus.core.entity.pit_presentation import (
    build_point_in_time_card,
    ManagedDevicesSectionModel,
    PointInTimeReconstructionResult,
    single_provenance,
    ProvenanceObservation,
)
from diffasaurus.core.entity.types import CanonicalEntityKey, EntityState, FamilyCoverage, SourcedProperty
from diffasaurus.ui.point_in_time_card import EntityIdentityCardView
from diffasaurus.ui.point_in_time_device_card import ManagedDeviceCardWidget, ManagedDevicesSectionWidget
from diffasaurus.ui.point_in_time_styles import COLLECTION_FILTER_THRESHOLD

TS = datetime(2026, 7, 30, 5, 2, 0)
TARGET = datetime(2026, 7, 30, 18, 45, 0)


def _device(name: str, os_name: str = "Windows") -> RelatedManagedDevice:
    from diffasaurus.core.entity.pit_enrichment import MANAGED_DEVICES_FAMILY

    props = (
        SourcedProperty(
            family=MANAGED_DEVICES_FAMILY,
            name="DeviceName",
            value=name,
            observed_at=TS,
        ),
        SourcedProperty(
            family=MANAGED_DEVICES_FAMILY,
            name="OperatingSystem",
            value=os_name,
            observed_at=TS,
        ),
        SourcedProperty(
            family=MANAGED_DEVICES_FAMILY,
            name="SerialNumber",
            value=f"SN-{name}",
            observed_at=TS,
        ),
        SourcedProperty(
            family=MANAGED_DEVICES_FAMILY,
            name="Manufacturer",
            value="Dell",
            observed_at=TS,
        ),
        SourcedProperty(
            family=MANAGED_DEVICES_FAMILY,
            name="Model",
            value="XPS",
            observed_at=TS,
        ),
    )
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
        device_key=CanonicalEntityKey("device", f"dev:{name}"),
        dedup_key=f"dedup-{name}",
        properties=props,
        provenance=provenance,
        link_kind="user_id",
        normalized_link_value="user-1",
        resolution_status="resolved",
        resolved_user_immutable_id="user-1",
        candidate_user_ids=frozenset(),
        diagnostic="",
    )


class PointInTimeDeviceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_device_row_starts_collapsed(self):
        from diffasaurus.core.entity.pit_managed_devices_presentation import build_managed_devices_section

        device = _device("Laptop")
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
        enrichment = UserManagedDevicesEnrichment(
            devices=(device,),
            coverage="populated",
            family_coverage=None,
            unresolved_observations=(),
            snapshot_at=TS,
            snapshot_file_id=1,
            enriched_devices=(enriched,),
        )
        section = build_managed_devices_section(enrichment)
        assert section is not None
        widget = ManagedDeviceCardWidget(section.devices[0])
        self.assertFalse(widget.details_host.isVisible())

    def test_expand_shows_details(self):
        from diffasaurus.core.entity.pit_managed_devices_presentation import build_managed_devices_section

        device = _device("Laptop")
        enriched = EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="matched",
                properties=(
                    SourcedProperty(
                        family="Intune_Devices_Autopilot",
                        name="EnrollmentState",
                        value="enrolled",
                        observed_at=TS,
                    ),
                ),
                provenance=single_provenance(
                    ProvenanceObservation(
                        family="Intune_Devices_Autopilot",
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
        enrichment = UserManagedDevicesEnrichment(
            devices=(device,),
            coverage="populated",
            family_coverage=None,
            unresolved_observations=(),
            snapshot_at=TS,
            snapshot_file_id=1,
            enriched_devices=(enriched,),
        )
        section = build_managed_devices_section(enrichment)
        assert section is not None
        widget = ManagedDeviceCardWidget(section.devices[0])
        widget.show()
        widget._toggle()
        self.assertTrue(widget._expanded)
        self.assertIsNotNone(widget._details)
        widget._toggle()
        self.assertFalse(widget._expanded)

    def test_large_list_shows_filter(self):
        from diffasaurus.core.entity.pit_managed_devices_presentation import build_managed_devices_section

        devices = tuple(_device(f"Device-{i}") for i in range(COLLECTION_FILTER_THRESHOLD + 1))
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
        enrichment = UserManagedDevicesEnrichment(
            devices=devices,
            coverage="populated",
            family_coverage=None,
            unresolved_observations=(),
            snapshot_at=TS,
            snapshot_file_id=1,
            enriched_devices=enriched,
        )
        section = build_managed_devices_section(enrichment)
        assert section is not None
        widget = ManagedDevicesSectionWidget(section)
        self.assertTrue(widget.filter_input.isVisibleTo(widget))

    def test_filter_matches_serial(self):
        from diffasaurus.core.entity.pit_managed_devices_presentation import build_managed_devices_section

        devices = (_device("Alpha"), _device("Bravo"))
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
        enrichment = UserManagedDevicesEnrichment(
            devices=devices,
            coverage="populated",
            family_coverage=None,
            unresolved_observations=(),
            snapshot_at=TS,
            snapshot_file_id=1,
            enriched_devices=enriched,
        )
        section = build_managed_devices_section(enrichment)
        assert section is not None
        widget = ManagedDevicesSectionWidget(section)
        widget.filter_input.setText("SN-Bravo")
        widget._on_filter_changed("SN-Bravo")
        self.assertEqual(widget.devices_host.count(), 1)

    def test_identity_card_includes_managed_section(self):
        state = EntityState(
            as_of=TARGET,
            key=CanonicalEntityKey("user", "user-1"),
            properties_by_family={},
            family_coverage={},
            coverage=(),
            presence="present",
            scalar_properties_by_family={},
            relationships_by_family={},
        )
        device = _device("Laptop")
        enrichment = UserPointInTimeEnrichment(
            managed_devices=UserManagedDevicesEnrichment(
                devices=(device,),
                coverage="populated",
                family_coverage=None,
                unresolved_observations=(),
                snapshot_at=TS,
                snapshot_file_id=1,
                enriched_devices=(
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
        view = EntityIdentityCardView()
        view.set_model(model)
        managed_widgets = view.findChildren(ManagedDevicesSectionWidget)
        self.assertEqual(len(managed_widgets), 1)


if __name__ == "__main__":
    unittest.main()
