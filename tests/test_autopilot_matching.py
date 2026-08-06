from __future__ import annotations

import unittest
from datetime import datetime

from diffasaurus.core.entity.autopilot_matching import (
    build_autopilot_snapshot_index,
    build_key_matches,
    enrich_managed_devices_with_autopilot,
    is_non_windows_autopilot_applicable,
    match_device_to_autopilot,
    normalize_guid,
    normalize_serial,
    resolve_cross_key_match,
    resolve_key_match,
    KEY_KIND_AAD,
    KEY_KIND_MANAGED,
    KEY_KIND_SERIAL,
)
from diffasaurus.core.entity.pit_enrichment import (
    AUTOPILOT_FAMILY,
    MANAGED_DEVICES_FAMILY,
    RelatedManagedDevice,
    UserManagedDevicesEnrichment,
)
from diffasaurus.core.entity.pit_presentation import (
    ProvenanceObservation,
    single_provenance,
)
from diffasaurus.core.entity.types import CanonicalEntityKey, SourcedProperty

TS = datetime(2026, 7, 15, 1, 0, 0)
TARGET = datetime(2026, 7, 20, 1, 0, 0)

AAD_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AAD_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
AAD_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
MD_A = "11111111-1111-1111-1111-111111111111"
MD_B = "22222222-2222-2222-2222-222222222222"
MD_C = "33333333-3333-3333-3333-333333333333"


def _managed_prop(name: str, value: str) -> SourcedProperty:
    return SourcedProperty(
        family=MANAGED_DEVICES_FAMILY,
        name=name,
        value=value,
        observed_at=TS,
    )


def _autopilot_prop(name: str, value: str) -> SourcedProperty:
    return SourcedProperty(
        family=AUTOPILOT_FAMILY,
        name=name,
        value=value,
        observed_at=TS,
    )


def _managed_device(
    os_name: str,
    *,
    aad: str = "",
    managed: str = "",
    serial: str = "",
    device_name: str = "",
) -> RelatedManagedDevice:
    props: list[SourcedProperty] = [_managed_prop("OperatingSystem", os_name)]
    if aad:
        props.append(_managed_prop("AzureADDeviceId", aad))
    if managed:
        props.append(_managed_prop("ManagedDeviceId", managed))
    if serial:
        props.append(_managed_prop("SerialNumber", serial))
    if device_name:
        props.append(_managed_prop("DeviceName", device_name))
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
        device_key=CanonicalEntityKey("device", f"aad:{aad or managed or serial}"),
        dedup_key=f"dedup-{aad or managed or serial}",
        properties=tuple(props),
        provenance=provenance,
        link_kind="user_id",
        normalized_link_value="user-1",
        resolution_status="resolved",
        resolved_user_immutable_id="user-1",
        candidate_user_ids=frozenset(),
        diagnostic="",
    )


def _autopilot_row(
    *,
    aad: str = "",
    managed: str = "",
    serial: str = "",
    display: str = "",
) -> tuple[SourcedProperty, ...]:
    props: list[SourcedProperty] = []
    if aad:
        props.append(_autopilot_prop("AzureADDeviceId", aad))
    if managed:
        props.append(_autopilot_prop("ManagedDeviceId", managed))
    if serial:
        props.append(_autopilot_prop("SerialNumber", serial))
    if display:
        props.append(_autopilot_prop("DisplayName", display))
    return tuple(props)


def _index(*rows: tuple[SourcedProperty, ...]):
    return build_autopilot_snapshot_index(list(rows))


class AutopilotNormalizationTests(unittest.TestCase):
    def test_guid_trim_and_casefold(self):
        self.assertEqual(
            normalize_guid("  AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA  "),
            AAD_A,
        )

    def test_guid_rejects_invalid(self):
        self.assertEqual(normalize_guid("aad-1"), "")
        self.assertEqual(normalize_guid("null"), "")

    def test_serial_whitespace_normalization(self):
        self.assertEqual(normalize_serial("  SN-1  "), "sn-1")
        self.assertEqual(normalize_serial("SN\t1"), "sn 1")


class AutopilotCrossKeyTests(unittest.TestCase):
    def test_all_keys_same_row_matched(self):
        index = _index(
            _autopilot_row(aad=AAD_A, managed=MD_A, serial="SN-1"),
        )
        device = _managed_device("Windows", aad=AAD_A, managed=MD_A, serial="SN-1")
        matches = build_key_matches(device, index)
        status, row, _ = resolve_cross_key_match(matches)
        self.assertEqual(status, "matched")
        self.assertEqual(row, 0)

    def test_serial_only_unique_match(self):
        index = _index(_autopilot_row(serial="SN-ONLY"))
        device = _managed_device("Windows", serial="SN-ONLY")
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "matched")
        self.assertEqual(row, 0)

    def test_aad_only_unique_match(self):
        index = _index(_autopilot_row(aad=AAD_A))
        device = _managed_device("Windows", aad=AAD_A)
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "matched")
        self.assertEqual(row, 0)

    def test_managed_only_unique_match(self):
        index = _index(_autopilot_row(managed=MD_A))
        device = _managed_device("Windows", managed=MD_A)
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "matched")
        self.assertEqual(row, 0)

    def test_aad_and_serial_different_rows_ambiguous(self):
        index = _index(
            _autopilot_row(aad=AAD_A, serial="SN-A"),
            _autopilot_row(aad=AAD_B, serial="SN-B"),
        )
        device = _managed_device("Windows", aad=AAD_A, serial="SN-B")
        status, row, diagnostic = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "ambiguous")
        self.assertIsNone(row)
        self.assertIn("Cross-key", diagnostic)

    def test_aad_and_managed_different_rows_ambiguous(self):
        index = _index(
            _autopilot_row(aad=AAD_A, managed=MD_A),
            _autopilot_row(aad=AAD_B, managed=MD_B),
        )
        device = _managed_device("Windows", aad=AAD_A, managed=MD_B)
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "ambiguous")
        self.assertIsNone(row)

    def test_duplicate_serial_with_consistent_unique_aad(self):
        index = _index(
            _autopilot_row(aad=AAD_A, serial="DUP-SN"),
            _autopilot_row(aad=AAD_B, serial="DUP-SN"),
        )
        device = _managed_device("Windows", aad=AAD_A, serial="DUP-SN")
        status, row, diagnostic = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "matched")
        self.assertEqual(row, 0)
        self.assertIn("non-unique", diagnostic.lower())

    def test_duplicate_serial_without_immutable_match_ambiguous(self):
        index = _index(
            _autopilot_row(aad=AAD_A, serial="DUP-SN"),
            _autopilot_row(aad=AAD_B, serial="DUP-SN"),
        )
        device = _managed_device("Windows", aad=AAD_C, serial="DUP-SN")
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "ambiguous")
        self.assertIsNone(row)

    def test_duplicate_aad_ambiguous(self):
        index = _index(
            _autopilot_row(aad=AAD_A, serial="SN-1"),
            _autopilot_row(aad=AAD_A, serial="SN-2"),
        )
        device = _managed_device("Windows", aad=AAD_A)
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "ambiguous")
        self.assertIsNone(row)

    def test_missing_keys_ignored_no_match(self):
        index = _index(_autopilot_row(aad=AAD_A))
        device = _managed_device("Windows")
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "no_match_with_coverage")
        self.assertIsNone(row)

    def test_no_fuzzy_device_name_matching(self):
        index = _index(_autopilot_row(display="Laptop-A", serial="SN-A"))
        device = _managed_device("Windows", device_name="Laptop-A")
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "no_match_with_coverage")
        self.assertIsNone(row)

    def test_only_duplicate_serial_ambiguous(self):
        index = _index(
            _autopilot_row(serial="DUP"),
            _autopilot_row(serial="DUP"),
        )
        device = _managed_device("Windows", serial="DUP")
        status, row, _ = resolve_cross_key_match(build_key_matches(device, index))
        self.assertEqual(status, "ambiguous")
        self.assertIsNone(row)


class AutopilotApplicabilityTests(unittest.TestCase):
    def _match(
        self,
        device: RelatedManagedDevice,
        index,
        *,
        snapshot_exists: bool = True,
    ):
        provenance = single_provenance(
            ProvenanceObservation(
                family=AUTOPILOT_FAMILY,
                observed_at=TS,
                snapshot_at=TS,
                requested_at=TARGET,
                gap=TARGET - TS,
            )
        )
        return match_device_to_autopilot(
            device,
            index,
            autopilot_snapshot_exists=snapshot_exists,
            target=TARGET,
            snapshot_at=TS,
            autopilot_provenance=provenance,
        )

    def test_macos_not_applicable(self):
        device = _managed_device("macOS", aad=AAD_A)
        state = self._match(device, _index())
        self.assertEqual(state.status, "not_applicable")

    def test_ios_not_applicable(self):
        device = _managed_device("iOS", aad=AAD_A)
        state = self._match(device, _index())
        self.assertEqual(state.status, "not_applicable")

    def test_windows_no_coverage_without_snapshot(self):
        device = _managed_device("Windows", aad=AAD_A)
        state = self._match(device, _index(), snapshot_exists=False)
        self.assertEqual(state.status, "no_coverage")

    def test_cloud_pc_model_not_applicable(self):
        device = _managed_device(
            "Windows",
            aad=AAD_A,
            managed=MD_A,
            serial="SN-1",
        )
        device = RelatedManagedDevice(
            device_key=device.device_key,
            dedup_key=device.dedup_key,
            properties=device.properties + (_managed_prop("Model", "Cloud PC Enterprise 4vCPU/16GB/256GB"),),
            provenance=device.provenance,
            link_kind=device.link_kind,
            normalized_link_value=device.normalized_link_value,
            resolution_status=device.resolution_status,
            resolved_user_immutable_id=device.resolved_user_immutable_id,
            candidate_user_ids=device.candidate_user_ids,
            diagnostic=device.diagnostic,
        )
        state = self._match(device, _index(_autopilot_row(aad=AAD_A)))
        self.assertEqual(state.status, "not_applicable")
        self.assertIn("Cloud PC", state.conflict_diagnostic)

    def test_virtual_machine_model_not_applicable(self):
        device = _managed_device("Windows", aad=AAD_A)
        device = RelatedManagedDevice(
            device_key=device.device_key,
            dedup_key=device.dedup_key,
            properties=device.properties + (_managed_prop("Model", "Virtual Machine"),),
            provenance=device.provenance,
            link_kind=device.link_kind,
            normalized_link_value=device.normalized_link_value,
            resolution_status=device.resolution_status,
            resolved_user_immutable_id=device.resolved_user_immutable_id,
            candidate_user_ids=device.candidate_user_ids,
            diagnostic=device.diagnostic,
        )
        state = self._match(device, _index(_autopilot_row(aad=AAD_A)))
        self.assertEqual(state.status, "not_applicable")
        self.assertIn("Virtual machine", state.conflict_diagnostic)

    def test_physical_windows_without_autopilot_row_stays_no_match(self):
        device = _managed_device("Windows", aad=AAD_A)
        device = RelatedManagedDevice(
            device_key=device.device_key,
            dedup_key=device.dedup_key,
            properties=device.properties + (_managed_prop("Model", "Latitude 7450"),),
            provenance=device.provenance,
            link_kind=device.link_kind,
            normalized_link_value=device.normalized_link_value,
            resolution_status=device.resolution_status,
            resolved_user_immutable_id=device.resolved_user_immutable_id,
            candidate_user_ids=device.candidate_user_ids,
            diagnostic=device.diagnostic,
        )
        state = self._match(device, _index(_autopilot_row(aad=AAD_B)))
        self.assertEqual(state.status, "no_match_with_coverage")

    def test_real_world_shape_all_keys_matched(self):
        index = _index(
            _autopilot_row(aad=AAD_A, managed=MD_A, serial="SN-REAL"),
        )
        device = _managed_device("Windows", aad=AAD_A, managed=MD_A, serial="SN-REAL")
        device = RelatedManagedDevice(
            device_key=device.device_key,
            dedup_key=device.dedup_key,
            properties=device.properties + (_managed_prop("Model", "Latitude 7450"),),
            provenance=device.provenance,
            link_kind=device.link_kind,
            normalized_link_value=device.normalized_link_value,
            resolution_status=device.resolution_status,
            resolved_user_immutable_id=device.resolved_user_immutable_id,
            candidate_user_ids=device.candidate_user_ids,
            diagnostic=device.diagnostic,
        )
        state = self._match(device, index)
        self.assertEqual(state.status, "matched")
        self.assertEqual(
            tuple(k.key_kind for k in state.key_matches if k.resolution_status == "unique"),
            ("azure_ad_device_id", "managed_device_id", "serial_number"),
        )

    def test_windows_no_match_with_coverage(self):
        index = _index(_autopilot_row(aad=AAD_B))
        device = _managed_device("Windows", aad=AAD_A)
        state = self._match(device, index)
        self.assertEqual(state.status, "no_match_with_coverage")

    def test_non_windows_helper(self):
        self.assertTrue(is_non_windows_autopilot_applicable((_managed_prop("OperatingSystem", "macOS"),)))
        self.assertTrue(is_non_windows_autopilot_applicable((_managed_prop("OperatingSystem", "iOS"),)))


class AutopilotKeyMatchTests(unittest.TestCase):
    def test_resolve_key_match_absent_empty(self):
        index = _index(_autopilot_row(aad=AAD_A))
        match = resolve_key_match(KEY_KIND_AAD, "", index)
        self.assertEqual(match.resolution_status, "absent")

    def test_resolve_key_match_invalid_guid(self):
        index = _index(_autopilot_row(aad=AAD_A))
        match = resolve_key_match(KEY_KIND_AAD, "not-a-guid", index)
        self.assertEqual(match.resolution_status, "invalid")


if __name__ == "__main__":
    unittest.main()
