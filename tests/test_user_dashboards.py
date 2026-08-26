import json
import tempfile
import unittest
from pathlib import Path

from diffasaurus.core.user_dashboards import (
    SCHEMA_VERSION,
    ColumnFilterSpec,
    SortSpec,
    UserDashboard,
    UserDashboardStore,
    index_filters_to_named,
    named_filters_to_index,
    validate_dashboard_columns,
)


class UserDashboardStoreTests(unittest.TestCase):
    def _store_path(self, directory: str) -> Path:
        return Path(directory) / "config" / "user_dashboards.json"

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserDashboardStore(self._store_path(directory))
            self.assertEqual(store.all(), [])
            self.assertEqual(store.last_load_warning, "")

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._store_path(directory)
            store = UserDashboardStore(path)
            dashboard = UserDashboard(
                id="dash-1",
                name="Enabled users",
                family="Entra_Users_Properties",
                description="Only enabled accounts",
                filters=[
                    ColumnFilterSpec(
                        column="AccountEnabled",
                        allowed=["True"],
                        allow_empty=False,
                    )
                ],
                search_mode="smart",
                search_text="adele",
                sort=SortSpec(column="DisplayName", order="asc"),
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
            store.create(dashboard)
            reloaded = UserDashboardStore(path)
            items = reloaded.all()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].name, "Enabled users")
            self.assertEqual(items[0].filters[0].column, "AccountEnabled")
            self.assertEqual(items[0].sort.column, "DisplayName")

    def test_atomic_save_uses_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._store_path(directory)
            store = UserDashboardStore(path)
            store.create(
                UserDashboard(
                    id="dash-1",
                    name="Sample",
                    family="TestFamily",
                )
            )
            temp_files = list(path.parent.glob("user_dashboards.json.tmp"))
            self.assertEqual(temp_files, [])
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], SCHEMA_VERSION)

    def test_create_update_delete_duplicate_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserDashboardStore(self._store_path(directory))
            created = store.create(
                UserDashboard(id="", name="Alpha", family="FamilyA")
            )
            self.assertTrue(created.id)
            created.description = "Updated"
            store.update(created)
            self.assertEqual(store.get(created.id).description, "Updated")

            copy = store.duplicate(created.id)
            self.assertNotEqual(copy.id, created.id)
            self.assertEqual(copy.name, "Alpha copy")
            self.assertEqual(len(store.all()), 2)

            store.delete(created.id)
            self.assertIsNone(store.get(created.id))
            self.assertEqual(len(store.all()), 1)

            store.reset()
            self.assertEqual(store.all(), [])

    def test_corrupt_json_does_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._store_path(directory)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not valid", encoding="utf-8")
            store = UserDashboardStore(path)
            self.assertEqual(store.all(), [])
            self.assertIn("could not be read", store.last_load_warning.lower())
            backups = list(path.parent.glob("user_dashboards.json.corrupt-*"))
            self.assertEqual(len(backups), 1)

    def test_unsupported_schema_version_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._store_path(directory)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"schema_version": 99, "dashboards": []}),
                encoding="utf-8",
            )
            store = UserDashboardStore(path)
            self.assertEqual(store.all(), [])
            self.assertIn("unsupported schema", store.last_load_warning.lower())

    def test_export_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserDashboardStore(self._store_path(directory))
            original = store.create(
                UserDashboard(
                    id="dash-1",
                    name="Beta",
                    family="FamilyB",
                    filters=[ColumnFilterSpec(column="State", allowed=["Enabled"])],
                )
            )
            payload = store.export_definitions()
            imported_store = UserDashboardStore(self._store_path(directory + "-import"))
            imported = imported_store.import_definitions(payload)
            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0].name, "Beta")
            self.assertNotEqual(imported[0].id, original.id)

    def test_import_malformed_payload_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserDashboardStore(self._store_path(directory))
            with self.assertRaises(ValueError):
                store.import_definitions({"schema_version": SCHEMA_VERSION})
            self.assertEqual(store.all(), [])

    def test_import_id_collision_regenerates_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserDashboardStore(self._store_path(directory))
            dashboard = store.create(
                UserDashboard(id="same-id", name="One", family="FamilyA")
            )
            payload = store.export_definitions()
            imported = store.import_definitions(payload)
            self.assertEqual(len(store.all()), 2)
            self.assertNotEqual(imported[0].id, dashboard.id)

    def test_for_family_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserDashboardStore(self._store_path(directory))
            store.create(UserDashboard(id="1", name="A", family="FamilyA"))
            store.create(UserDashboard(id="2", name="B", family="FamilyB"))
            self.assertEqual(len(store.for_family("FamilyA")), 1)
            self.assertEqual(store.for_family("FamilyA")[0].name, "A")

    def test_unicode_names_and_descriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._store_path(directory)
            store = UserDashboardStore(path)
            store.create(
                UserDashboard(
                    id="dash-1",
                    name="Rapport général",
                    family="TestFamily",
                    description="Données pour example.com — équipe Contoso",
                )
            )
            reloaded = UserDashboardStore(path)
            self.assertEqual(reloaded.all()[0].name, "Rapport général")


class UserDashboardConversionTests(unittest.TestCase):
    def test_index_filters_to_named_and_back(self):
        headers = ["DisplayName", "AccountEnabled"]
        runtime = {
            1: {"allowed": {"False"}, "allow_empty": False},
        }
        named = index_filters_to_named(runtime, headers)
        self.assertEqual(named[0].column, "AccountEnabled")
        roundtrip = named_filters_to_index(named, headers)
        self.assertEqual(roundtrip[1]["allowed"], {"False"})

    def test_validate_dashboard_columns_reports_missing(self):
        dashboard = UserDashboard(
            id="1",
            name="Missing",
            family="FamilyA",
            filters=[ColumnFilterSpec(column="Department", allowed=["Sales"])],
            sort=SortSpec(column="DeviceName", order="asc"),
        )
        missing = validate_dashboard_columns(
            dashboard,
            ["DisplayName", "AccountEnabled"],
        )
        self.assertEqual(set(missing), {"Department", "DeviceName"})


if __name__ == "__main__":
    unittest.main()
