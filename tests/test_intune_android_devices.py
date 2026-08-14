from __future__ import annotations

import csv
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from diffasaurus.core.dashboard_builders.autopilot_devices_builder import (
    looks_like_autopilot_devices_report,
)
from diffasaurus.core.dashboard_builders.devices_builder import looks_like_devices_report
from diffasaurus.core.dashboard_builders.intune_android_devices_builder import (
    build_intune_android_devices_stats,
    looks_like_intune_android_devices_report,
)
from diffasaurus.core.dashboard_builders.intune_ios_devices_builder import (
    looks_like_intune_ios_devices_report,
)
from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.core.entity.adapters import DEVICE_ANDROID
from diffasaurus.core.entity.history import (
    build_entity_period_changes,
    enrich_user_managed_devices,
    reconstruct_entity_state,
)
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.pit_field_registry import (
    AUTHORITY_ORDER,
    lookup_property_binding,
)
from diffasaurus.core.entity.registry import ADAPTERS_BY_FAMILY
from diffasaurus.core.entity.resolution import build_entity_resolver
from diffasaurus.core.entity.snapshots import clear_parse_cache
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import (
    compare_snapshots,
    comparison_summary_unit,
    report_family,
    report_timestamp,
    scan_report_history,
    suggested_key,
)
from diffasaurus.models.csv_model import CsvTableModel
from diffasaurus.ui.report_runner import CATALOG_FAMILY_ORDER, REPORT_CATALOG


ANDROID_HEADERS = (
    "DeviceName",
    "ManagementName",
    "IntuneDeviceId",
    "EntraDeviceId",
    "SerialNumber",
    "Manufacturer",
    "Model",
    "OperatingSystem",
    "OSVersion",
    "AndroidSecurityPatchLevel",
    "UserDisplayName",
    "UserPrincipalName",
    "EmailAddress",
    "PhoneNumber",
    "IMEI",
    "MEID",
    "ICCID",
    "SubscriberCarrier",
    "WiFiMacAddress",
    "OwnerType",
    "ManagementAgent",
    "DeviceEnrollmentType",
    "EnrollmentProfileName",
    "DeviceRegistrationState",
    "EnrolledDateTime",
    "ManagementCertificateExpiration",
    "LastSyncDateTime",
    "DaysSinceLastSync",
    "DeviceActivityStatus",
    "ComplianceState",
    "ComplianceGracePeriodExpiration",
    "AzureADRegistered",
    "IsEncrypted",
    "Rooted",
    "PartnerReportedThreatState",
    "EASActivated",
    "EASDeviceId",
    "EASActivationDateTime",
    "TotalStorageGB",
    "FreeStorageGB",
)

AAD_ONE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AAD_TWO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
INTUNE_ONE = "11111111-1111-1111-1111-111111111111"
INTUNE_TWO = "22222222-2222-2222-2222-222222222222"


def write_report(path: Path, rows: list[dict]) -> None:
    fieldnames = list(rows[0].keys()) if rows else list(ANDROID_HEADERS)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def android_row(
    *,
    entra_id: str = AAD_ONE,
    intune_id: str = INTUNE_ONE,
    device_name: str = "Pixel-7",
    serial: str = "SN-ANDROID-1",
    os_version: str = "14",
    patch: str = "2026-07-01",
    compliance: str = "compliant",
    encrypted: str = "True",
    rooted: str = "False",
    owner: str = "company",
    activity: str = "Active <=30d",
    user_upn: str = "ada@example.com",
    manufacturer: str = "Google",
    model: str = "Pixel 7",
    imei: str = "",
    phone: str = "",
    iccid: str = "",
    carrier: str = "",
    threat: str = "",
) -> dict[str, str]:
    return {
        "DeviceName": device_name,
        "ManagementName": device_name,
        "IntuneDeviceId": intune_id,
        "EntraDeviceId": entra_id,
        "SerialNumber": serial,
        "Manufacturer": manufacturer,
        "Model": model,
        "OperatingSystem": "Android",
        "OSVersion": os_version,
        "AndroidSecurityPatchLevel": patch,
        "UserDisplayName": "Ada",
        "UserPrincipalName": user_upn,
        "EmailAddress": user_upn,
        "PhoneNumber": phone,
        "IMEI": imei,
        "MEID": "",
        "ICCID": iccid,
        "SubscriberCarrier": carrier,
        "WiFiMacAddress": "",
        "OwnerType": owner,
        "ManagementAgent": "mdm",
        "DeviceEnrollmentType": "androidEnterpriseFullyManaged",
        "EnrollmentProfileName": "Corporate Android",
        "DeviceRegistrationState": "registered",
        "EnrolledDateTime": "2026-01-01T00:00:00Z",
        "ManagementCertificateExpiration": "",
        "LastSyncDateTime": "2026-08-01T00:00:00Z",
        "DaysSinceLastSync": "5",
        "DeviceActivityStatus": activity,
        "ComplianceState": compliance,
        "ComplianceGracePeriodExpiration": "",
        "AzureADRegistered": "True",
        "IsEncrypted": encrypted,
        "Rooted": rooted,
        "PartnerReportedThreatState": threat,
        "EASActivated": "False",
        "EASDeviceId": "",
        "EASActivationDateTime": "",
        "TotalStorageGB": "128",
        "FreeStorageGB": "64",
    }


def android_model(*rows: dict[str, str]) -> CsvTableModel:
    data = [list(row[column] for column in ANDROID_HEADERS) for row in rows]
    return CsvTableModel(list(ANDROID_HEADERS), data)


class AndroidCatalogTests(unittest.TestCase):
    def test_android_script_in_report_catalog(self):
        self.assertIn("_app_INTUNE_Android_Devices.ps1", REPORT_CATALOG)
        icon, title, description, family = REPORT_CATALOG["_app_INTUNE_Android_Devices.ps1"]
        self.assertEqual(family, "Intune_Android_Devices")
        self.assertIn("Android", title)
        self.assertIn("Android", description)

    def test_catalog_family_order_includes_android_near_ios(self):
        ios_index = CATALOG_FAMILY_ORDER.index("Intune_iOS_Devices")
        android_index = CATALOG_FAMILY_ORDER.index("Intune_Android_Devices")
        self.assertEqual(android_index, ios_index + 1)

    def test_script_uses_reports_dir_convention(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "psscripts" / "_app_INTUNE_Android_Devices.ps1").read_text(
            encoding="utf-8",
            errors="ignore",
        )
        self.assertIn("$env:REPORTS_DIR", text)
        self.assertIn("Intune_Android_Devices_$Timestamp.csv", text)
        self.assertNotRegex(text, r"\[https://")

    def test_graph_scope_detectable(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "psscripts" / "_app_INTUNE_Android_Devices.ps1").read_text(
            encoding="utf-8",
            errors="ignore",
        )
        self.assertIn("DeviceManagementManagedDevices.Read.All", text)
        self.assertIn("graph.microsoft.com/v1.0/", text)
        self.assertIn("$batch", text)


class AndroidReportHistoryTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def test_filename_parsing(self):
        path = Path("Intune_Android_Devices_20260812-170000.csv")
        self.assertEqual(report_family(path), "Intune_Android_Devices")
        self.assertEqual(
            report_timestamp(path),
            datetime(2026, 8, 12, 17, 0, 0),
        )

    def test_empty_header_only_snapshot_is_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Intune_Android_Devices_20260812-170000.csv"
            write_report(path, [])
            families = scan_report_history(root)
            snapshots = families["Intune_Android_Devices"]
            self.assertEqual(len(snapshots), 1)
            snapshot = snapshots[0]
            self.assertEqual(snapshot.row_count, 0)
            self.assertIn("DeviceName", snapshot.headers)

    def test_suggested_key_prefers_entra_device_id(self):
        headers = list(ANDROID_HEADERS)
        self.assertEqual(
            suggested_key(headers, "Intune_Android_Devices"),
            "EntraDeviceId",
        )

    def test_device_name_rename_is_changed_not_remove_add(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_Android_Devices_20260801-010000.csv",
                [android_row(device_name="Old Pixel Name")],
            )
            write_report(
                root / "Intune_Android_Devices_20260804-010000.csv",
                [android_row(device_name="New Pixel Name")],
            )
            families = scan_report_history(root)
            snapshots = families["Intune_Android_Devices"]
            result = compare_snapshots(
                snapshots[0],
                snapshots[1],
                suggested_key(snapshots[0].headers, "Intune_Android_Devices"),
                family="Intune_Android_Devices",
            )
            self.assertEqual(result.added, 0)
            self.assertEqual(result.removed, 0)
            self.assertEqual(result.changed, 1)
            changed = next(detail for detail in result.details if detail["change"] == "Changed")
            self.assertEqual(changed.get("column"), "DeviceName")
            self.assertEqual(changed.get("identity"), "New Pixel Name · SN-ANDROID-1")

    def test_report_family_parses_report_suffix(self):
        path = Path("Intune_Android_Devices_Report_20260812-170000.csv")
        self.assertEqual(report_family(path), "Intune_Android_Devices_Report")
        self.assertEqual(comparison_summary_unit("Intune_Android_Devices"), "devices")
        self.assertEqual(comparison_summary_unit("Intune_Android_Devices_Report"), "devices")


class AndroidDashboardTests(unittest.TestCase):
    def test_detector_matches_android_schema(self):
        self.assertTrue(looks_like_intune_android_devices_report(list(ANDROID_HEADERS)))

    def test_detector_does_not_claim_ios(self):
        ios_headers = list(ANDROID_HEADERS) + ["UDID", "IsSupervised"]
        self.assertFalse(looks_like_intune_android_devices_report(ios_headers))

    def test_detector_does_not_claim_managed_devices(self):
        managed_headers = [
            "DeviceName",
            "OperatingSystem",
            "ComplianceState",
            "ManagedDeviceId",
            "AzureADDeviceId",
            "JailBroken",
            "UserId",
        ]
        self.assertFalse(looks_like_intune_android_devices_report(managed_headers))
        self.assertTrue(looks_like_devices_report(managed_headers))

    def test_detector_does_not_claim_autopilot(self):
        autopilot_headers = [
            "DisplayName",
            "SerialNumber",
            "AutopilotObjectId",
            "EnrollmentState",
            "Manufacturer",
            "Model",
            "GroupTag",
            "AssignmentStatus",
        ]
        self.assertFalse(looks_like_intune_android_devices_report(autopilot_headers))
        self.assertTrue(looks_like_autopilot_devices_report(autopilot_headers))

    def test_dashboard_statistics_and_filters(self):
        model = android_model(
            android_row(
                compliance="compliant",
                encrypted="True",
                rooted="False",
                owner="company",
                activity="Active <=30d",
                imei="imei-1",
                phone="555-0100",
                iccid="iccid-1",
                carrier="CarrierA",
            ),
            android_row(
                entra_id=AAD_TWO,
                intune_id=INTUNE_TWO,
                device_name="Galaxy-S24",
                serial="SN-ANDROID-2",
                compliance="noncompliant",
                encrypted="False",
                rooted="True",
                owner="personal",
                activity="Inactive >90d",
                user_upn="bob@example.com",
                manufacturer="Samsung",
                model="Galaxy S24",
            ),
        )
        title, stats = get_dashboard_definition(model, list(ANDROID_HEADERS))
        self.assertEqual(title, "Intune Android Devices")
        by_title = {card["title"]: card for card in stats}
        self.assertEqual(by_title["Android Devices"]["value"], 2)
        self.assertEqual(by_title["Users"]["value"], 2)
        self.assertEqual(by_title["Manufacturers"]["value"], 2)
        self.assertEqual(by_title["Models"]["value"], 2)
        self.assertEqual(by_title["Compliant"]["value"], 1)
        self.assertEqual(by_title["Non-compliant"]["value"], 1)
        self.assertEqual(by_title["Encrypted"]["value"], 1)
        self.assertEqual(by_title["Not encrypted"]["value"], 1)
        self.assertEqual(by_title["Rooted"]["value"], 1)
        self.assertEqual(by_title["Corporate"]["value"], 1)
        self.assertEqual(by_title["Personal"]["value"], 1)
        self.assertEqual(by_title["Active <=30d"]["value"], 1)
        self.assertEqual(by_title["Inactive >90d"]["value"], 1)
        self.assertEqual(by_title["IMEI available"]["value"], 1)
        self.assertEqual(by_title["Phone number available"]["value"], 1)
        self.assertEqual(by_title["ICCID available"]["value"], 1)
        self.assertEqual(by_title["Carriers"]["value"], 1)
        self.assertIn("filter_spec", by_title["Compliant"])


class AndroidEntityAdapterTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def test_adapter_registered(self):
        self.assertIs(ADAPTERS_BY_FAMILY["Intune_Android_Devices"], DEVICE_ANDROID)

    def test_adapter_supports_headers(self):
        self.assertTrue(DEVICE_ANDROID.headers_supported(ANDROID_HEADERS))

    def test_entra_device_id_canonical_identity(self):
        row = android_row()
        key = DEVICE_ANDROID.build_key(row)
        assert key is not None
        self.assertEqual(key.primary_id, f"aad:{AAD_ONE}")

    def test_intune_device_id_fallback(self):
        row = android_row(entra_id="")
        key = DEVICE_ANDROID.build_key(row)
        assert key is not None
        self.assertEqual(key.primary_id, f"android_intune:{INTUNE_ONE}")

    def test_card_properties_include_android_fields(self):
        row = android_row(imei="imei-hidden", phone="phone-hidden")
        props = dict(DEVICE_ANDROID.card_properties(row, datetime(2026, 8, 1)))
        self.assertIn("AndroidSecurityPatchLevel", props)
        self.assertIn("Rooted", props)
        self.assertIn("IMEI", props)

    def test_same_entra_id_across_managed_and_android_resolves_to_one_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_ManagedDevices_Compliance_20260801-010000.csv",
                [
                    {
                        "AzureADDeviceId": AAD_ONE,
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Pixel-7",
                        "SerialNumber": "SN-ANDROID-1",
                        "ComplianceState": "compliant",
                        "OperatingSystem": "Android",
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                    }
                ],
            )
            write_report(
                root / "Intune_Android_Devices_20260801-010000.csv",
                [android_row()],
            )
            resolver = build_entity_resolver(scan_report_history(root))
            merged = resolver.get(CanonicalEntityKey("device", f"aad:{AAD_ONE}"))
            self.assertIsNotNone(merged)
            assert merged is not None
            self.assertIn("Intune_ManagedDevices_Compliance", merged.source_families)
            self.assertIn("Intune_Android_Devices", merged.source_families)

    def test_same_device_name_different_entra_ids_stay_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_Android_Devices_20260801-010000.csv",
                [
                    android_row(entra_id=AAD_ONE, device_name="Shared Name"),
                    android_row(
                        entra_id=AAD_TWO,
                        intune_id=INTUNE_TWO,
                        device_name="Shared Name",
                        serial="SN-ANDROID-2",
                    ),
                ],
            )
            resolver = build_entity_resolver(scan_report_history(root))
            by_name = resolver.search("Shared Name", "device")
            self.assertEqual(len(by_name.matches), 2)


class AndroidEntityHistoryTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def test_android_snapshots_contribute_to_device_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 12, 0, 0)
            write_report(
                root / "Intune_Android_Devices_20260801-010000.csv",
                [android_row(os_version="13", patch="2026-01-01")],
            )
            write_report(
                root / "Intune_Android_Devices_20260804-010000.csv",
                [android_row(os_version="14", patch="2026-07-01")],
            )
            changes = build_entity_period_changes(
                CanonicalEntityKey("device", f"aad:{AAD_ONE}"),
                scan_report_history(root),
                timedelta(days=3),
                reference=reference,
            )
            os_changes = [event for event in changes.events if event.property == "OSVersion"]
            patch_changes = [
                event
                for event in changes.events
                if event.property == "AndroidSecurityPatchLevel"
            ]
            self.assertTrue(os_changes or patch_changes)


class AndroidPointInTimeTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def test_android_device_reconstructs_at_target_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 2, 12, 0, 0)
            write_report(
                root / "Intune_Android_Devices_20260801-010000.csv",
                [android_row(os_version="14", patch="2026-07-01")],
            )
            write_report(
                root / "Intune_Android_Devices_20260810-010000.csv",
                [android_row(os_version="15", patch="2026-09-01")],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("device", f"aad:{AAD_ONE}"),
                scan_report_history(root),
                target,
            )
            props = {
                prop.name: prop.value
                for props in state.scalar_properties_by_family.values()
                for prop in props
            }
            self.assertEqual(props["OSVersion"], "14")
            self.assertEqual(props["AndroidSecurityPatchLevel"], "2026-07-01")

    def test_future_android_snapshot_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 1, 12, 0, 0)
            write_report(
                root / "Intune_Android_Devices_20260810-010000.csv",
                [android_row(os_version="15")],
            )
            state = reconstruct_entity_state(
                CanonicalEntityKey("device", f"aad:{AAD_ONE}"),
                scan_report_history(root),
                target,
            )
            android_coverage = state.family_coverage.get("Intune_Android_Devices")
            self.assertEqual(android_coverage, "No snapshot at or before target")

    def test_known_zero_header_only_snapshot_has_zero_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Intune_Android_Devices_20260801-010000.csv",
                [],
            )
            snapshots = scan_report_history(root)["Intune_Android_Devices"]
            self.assertEqual(snapshots[0].row_count, 0)
            self.assertGreater(len(snapshots[0].headers), 0)

    def test_android_specific_field_authority(self):
        self.assertEqual(
            AUTHORITY_ORDER[("device", "android_security_patch")],
            ("Intune_Android_Devices",),
        )
        self.assertEqual(
            AUTHORITY_ORDER[("device", "rooted")],
            ("Intune_Android_Devices",),
        )

    def test_common_field_authority_keeps_managed_devices_first(self):
        self.assertEqual(
            AUTHORITY_ORDER[("device", "device_name")][0],
            "Intune_ManagedDevices_Compliance",
        )
        self.assertIn("Intune_Android_Devices", AUTHORITY_ORDER[("device", "device_name")])

    def test_pit_bindings_for_android_fields(self):
        binding = lookup_property_binding(
            "device",
            "Intune_Android_Devices",
            "AndroidSecurityPatchLevel",
        )
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.key, "android_security_patch")
        self.assertEqual(binding.section_id, "os_compliance")


class AndroidRegressionTests(unittest.TestCase):
    def test_ios_dashboard_still_works(self):
        ios_headers = [
            "DeviceName",
            "OperatingSystem",
            "ComplianceState",
            "UDID",
            "IMEI",
            "IsSupervised",
        ]
        self.assertTrue(looks_like_intune_ios_devices_report(ios_headers))
        self.assertFalse(looks_like_intune_android_devices_report(ios_headers))

    def test_managed_devices_dashboard_still_works(self):
        managed_headers = [
            "DeviceName",
            "OperatingSystem",
            "ComplianceState",
            "OwnerType",
            "DaysSinceLastSync",
            "DeviceActivityStatus",
        ]
        self.assertTrue(looks_like_devices_report(managed_headers))
        self.assertFalse(looks_like_intune_android_devices_report(managed_headers))

    def test_user_pit_does_not_duplicate_android_managed_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 8, 4, 12, 0, 0)
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
                root / "Intune_ManagedDevices_Compliance_20260801-010000.csv",
                [
                    {
                        "UserId": "user-1",
                        "UserPrincipalName": "ada@example.com",
                        "AzureADDeviceId": AAD_ONE,
                        "ManagedDeviceId": "md-1",
                        "DeviceName": "Pixel-7",
                        "SerialNumber": "SN-ANDROID-1",
                        "ComplianceState": "compliant",
                        "OperatingSystem": "Android",
                    }
                ],
            )
            write_report(
                root / "Intune_Android_Devices_20260801-010000.csv",
                [android_row()],
            )
            families = scan_report_history(root)
            user_key = CanonicalEntityKey("user", "user-1")
            enrichment = enrich_user_managed_devices(user_key, families, target)
            self.assertEqual(len(enrichment.devices), 1)
            self.assertNotIn(
                "Intune_Android_Devices",
                {prop.family for device in enrichment.devices for prop in device.properties},
            )

    def test_index_sync_only_adds_android_family(self):
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
            db_path = entity_index_path(root)
            first = run_sync(root, cold=True, db_path=db_path)
            self.assertEqual(first.failed, 0)
            write_report(
                root / "Intune_Android_Devices_20260802-010000.csv",
                [android_row()],
            )
            second = run_sync(root, cold=False, db_path=db_path)
            self.assertEqual(second.failed, 0)
            self.assertGreaterEqual(second.parsed, 1)


if __name__ == "__main__":
    unittest.main()
