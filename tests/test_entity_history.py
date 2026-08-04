import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from diffasaurus.core.entity.history import build_entity_period_changes, reconstruct_entity_state
from diffasaurus.core.entity.resolution import build_entity_resolver
from diffasaurus.core.entity.snapshots import clear_parse_cache
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import RECENT_CHANGE_PERIODS, scan_report_history


def write_report(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class EntityHistoryTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def _families(self, root: Path):
        return scan_report_history(root)

    def test_period_modified_event_for_baseline_and_latest_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
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
                root / "Entra_Users_Properties_20260804-042100.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "Engineering",
                    }
                ],
            )
            families = self._families(root)
            changes = build_entity_period_changes(
                CanonicalEntityKey("user", "user-1"),
                families,
                timedelta(hours=48),
                reference=reference,
            )
            modified = [event for event in changes.events if event.property == "Department"]
            self.assertEqual(len(modified), 1)
            self.assertEqual(modified[0].change_type, "modified")
            self.assertEqual(modified[0].before, "R&D")
            self.assertEqual(modified[0].after, "Engineering")

    def test_upn_rename_emits_property_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "old@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "new@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            families = self._families(root)
            changes = build_entity_period_changes(
                CanonicalEntityKey("user", "user-1"),
                families,
                timedelta(hours=48),
                reference=reference,
            )
            upn_changes = [event for event in changes.events if event.property == "UPN"]
            self.assertEqual(len(upn_changes), 1)
            self.assertEqual(upn_changes[0].before, "old@example.com")
            self.assertEqual(upn_changes[0].after, "new@example.com")

    def test_per_family_added_and_removed_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Activity_20260731-042100.csv",
                [
                    {
                        "UserId": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Activity_20260804-042100.csv",
                [
                    {
                        "UserId": "user-2",
                        "UPN": "other@example.com",
                        "DisplayName": "Other",
                    }
                ],
            )
            families = self._families(root)
            changes = build_entity_period_changes(
                CanonicalEntityKey("user", "user-1"),
                families,
                timedelta(hours=48),
                reference=reference,
            )
            activity_events = [
                event for event in changes.events if event.family == "Entra_Users_Activity"
            ]
            self.assertEqual(len(activity_events), 1)
            self.assertEqual(activity_events[0].change_type, "removed")

    def test_present_in_latest_false_for_deleted_entity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-2",
                        "UPN": "other@example.com",
                        "DisplayName": "Other",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            record = resolver.search("user-1", "user").matches[0]
            self.assertFalse(record.present_in_latest)

    def test_card_properties_keep_family_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada Properties",
                        "Department": "R&D",
                    }
                ],
            )
            write_report(
                root / "Entra_Users_Activity_20260801-010000.csv",
                [
                    {
                        "UserId": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada Activity",
                        "Department": "IT",
                    }
                ],
            )
            resolver = build_entity_resolver(self._families(root))
            record = resolver.search("user-1", "user").matches[0]
            props_family = record.properties_by_family["Entra_Users_Properties"]
            activity_family = record.properties_by_family["Entra_Users_Activity"]
            prop_names = {prop.name: prop.value for prop in props_family}
            activity_names = {prop.name: prop.value for prop in activity_family}
            self.assertEqual(prop_names["DisplayName"], "Ada Properties")
            self.assertEqual(activity_names["DisplayName"], "Ada Activity")
            self.assertEqual(prop_names["Department"], "R&D")
            self.assertEqual(activity_names["Department"], "IT")

    def test_period_model_exposes_covered_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Entra_Users_Properties_20260731-042100.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            write_report(
                root / "Entra_Users_Properties_20260804-042100.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            changes = build_entity_period_changes(
                CanonicalEntityKey("user", "user-1"),
                self._families(root),
                timedelta(hours=48),
                reference=reference,
            )
            self.assertEqual(changes.covered_to, reference)
            self.assertEqual(changes.covered_from, reference - timedelta(hours=48))

    def test_reconstruct_entity_state_groups_properties_by_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = datetime(2026, 7, 15, 1, 0, 0)
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
                root / "Entra_Users_Activity_20260801-010000.csv",
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
            self.assertIn("Entra_Users_Properties", state.properties_by_family)
            self.assertNotIn("Entra_Users_Activity", state.properties_by_family)
            props = {
                prop.name: prop.value
                for prop in state.properties_by_family["Entra_Users_Properties"]
            }
            self.assertEqual(props["Department"], "R&D")

    def test_mailbox_forwarding_change_in_period(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = datetime(2026, 8, 4, 13, 5, 0)
            write_report(
                root / "Exchange_SharedMailboxes_20260731-042100.csv",
                [
                    {
                        "DisplayName": "Finance",
                        "PrimarySmtpAddress": "finance@example.com",
                        "Alias": "finance",
                        "ExternalDirectoryObjectId": "mbx-1",
                        "HasForwarding": "False",
                        "ForwardingSmtpAddress": "",
                    }
                ],
            )
            write_report(
                root / "Exchange_SharedMailboxes_20260804-042100.csv",
                [
                    {
                        "DisplayName": "Finance",
                        "PrimarySmtpAddress": "finance@example.com",
                        "Alias": "finance",
                        "ExternalDirectoryObjectId": "mbx-1",
                        "HasForwarding": "True",
                        "ForwardingSmtpAddress": "boss@example.com",
                    }
                ],
            )
            changes = build_entity_period_changes(
                CanonicalEntityKey("shared_mailbox", "mbx-1"),
                self._families(root),
                timedelta(hours=48),
                reference=reference,
            )
            forwarding = [
                event
                for event in changes.events
                if event.property == "ForwardingSmtpAddress"
            ]
            self.assertEqual(len(forwarding), 1)
            self.assertEqual(forwarding[0].after, "boss@example.com")


class EntityHistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_period_selector_matches_recent_changes_options(self):
        from diffasaurus.ui.period_selector import PeriodSelector
        from diffasaurus.ui.recent_changes import RecentChangesPage

        recent = RecentChangesPage()
        entity = PeriodSelector()
        recent_labels = [recent.period_selector.combo.itemText(index) for index in range(recent.period_selector.combo.count())]
        entity_labels = [entity.combo.itemText(index) for index in range(entity.combo.count())]
        self.assertEqual(recent_labels, entity_labels)
        self.assertEqual(len(recent_labels), len(RECENT_CHANGE_PERIODS))

    def test_entity_history_page_labels_changes_during_period(self):
        from diffasaurus.ui.entity_history import EntityHistoryPage

        page = EntityHistoryPage()
        self.assertEqual(page.changes_title.text(), "Changes during period")

    def test_deleted_entity_banner_when_not_present_in_latest(self):
        from diffasaurus.core.entity.types import EntityRecord
        from diffasaurus.ui.entity_history import EntityHistoryPage

        page = EntityHistoryPage()
        record = EntityRecord(
            key=CanonicalEntityKey("user", "user-1"),
            display_name="Ada",
            present_in_latest=False,
            first_seen=datetime(2026, 7, 1),
            last_seen=datetime(2026, 7, 15),
            source_families={"Entra_Users_Properties"},
            properties_by_family={},
        )
        page._render_card(record)
        self.assertIn("No longer present", page.card_banner.text())
        self.assertEqual(page.card_presence.text(), "Not present")
        self.assertEqual(page.card_family_count.text(), "1")

    def test_entity_history_page_has_splitter_and_changes_table_minimum(self):
        from diffasaurus.ui.entity_history import CHANGES_TABLE_MIN_HEIGHT, EntityHistoryPage

        page = EntityHistoryPage()
        self.assertEqual(page.splitter.count(), 2)
        self.assertGreaterEqual(page.changes_table.minimumHeight(), CHANGES_TABLE_MIN_HEIGHT)

    def test_family_property_section_uses_table_and_show_all(self):
        from diffasaurus.core.entity.types import SourcedProperty
        from diffasaurus.ui.entity_history import FamilyPropertySection

        properties = [
            SourcedProperty("Entra_Users_Properties", name, f"value-{index}", datetime(2026, 8, 1))
            for index, name in enumerate(
                ("Id", "UPN", "DisplayName", "Mail", "Department", "JobTitle", "AccountEnabled", "UserType")
            )
        ] + [
            SourcedProperty(
                "Entra_Users_Properties",
                "ExtensionAttribute1",
                "extra",
                datetime(2026, 8, 1),
            )
        ]
        section = FamilyPropertySection("Entra_Users_Properties", properties)
        self.assertEqual(section.table.columnCount(), 2)
        self.assertLess(section.table.rowCount(), len(properties))
        self.assertFalse(section.show_all_button.isHidden())
        section._toggle_show_all()
        self.assertEqual(section.table.rowCount(), len(properties))
