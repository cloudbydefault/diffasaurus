from __future__ import annotations

import csv
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from diffasaurus.core.entity.family_aliases import (
    canonical_entity_family,
    entity_family_names_for_adapter,
    snapshots_for_adapter,
)
from diffasaurus.core.entity.history import reconstruct_entity_state
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_schema import open_connection
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.pit_auth_methods import AUTH_METHODS_FAMILY
from diffasaurus.core.entity.pit_presentation import build_point_in_time_card
from diffasaurus.core.entity.snapshots import clear_parse_cache
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import report_family, scan_report_history


def write_report(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _collection(model, collection_id: str):
    for section in model.sections:
        for collection in section.collections:
            if collection.collection_id == collection_id:
                return collection
    return None


class EntityFamilyAliasClassificationTests(unittest.TestCase):
    def test_hybrid_filename_maps_to_canonical_family(self):
        path = "Entra_Users_AuthenticationMethods_Hybrid_20260730-051117.csv"
        self.assertEqual(
            report_family(path),
            "Entra_Users_AuthenticationMethods_Hybrid",
        )
        self.assertEqual(
            canonical_entity_family(report_family(path)),
            "Entra_Users_AuthenticationMethods",
        )

    def test_non_hybrid_filename_maps_to_canonical_family(self):
        path = "Entra_Users_AuthenticationMethods_20260801-010000.csv"
        self.assertEqual(
            canonical_entity_family(report_family(path)),
            "Entra_Users_AuthenticationMethods",
        )

    def test_entity_family_names_include_alias(self):
        names = entity_family_names_for_adapter("Entra_Users_AuthenticationMethods")
        self.assertIn("Entra_Users_AuthenticationMethods", names)
        self.assertIn("Entra_Users_AuthenticationMethods_Hybrid", names)


class HybridAuthenticationReconstructionTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def tearDown(self):
        clear_parse_cache()

    def _write_user_fixtures(self, root: Path) -> None:
        write_report(
            root / "Entra_Users_Properties_20260730-010000.csv",
            [
                {
                    "Id": "user-1",
                    "UPN": "ada@example.com",
                    "DisplayName": "Ada",
                }
            ],
        )

    def _write_hybrid_snapshot(
        self,
        root: Path,
        stamp: str,
        methods: str = "Microsoft Authenticator;FIDO2;TAP",
    ) -> Path:
        path = root / f"Entra_Users_AuthenticationMethods_Hybrid_{stamp}.csv"
        write_report(
            path,
            [
                {
                    "UPN": "ada@example.com",
                    "DisplayName": "Ada",
                    "MethodsRegistered": methods,
                    "AuthenticationMethods": methods,
                    "IsMfaRegistered": "True",
                    "IsMfaCapable": "True",
                    "DefaultMfaMethod": "microsoftAuthenticator",
                }
            ],
        )
        return path

    def test_legacy_reconstruction_selects_hybrid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_user_fixtures(root)
            self._write_hybrid_snapshot(root, "20260730-051117")
            target = datetime(2026, 7, 30, 17, 40, 0)
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                scan_report_history(root),
                target,
            )
            auth_coverage = next(
                item for item in state.coverage if item.family == AUTH_METHODS_FAMILY
            )
            self.assertEqual(auth_coverage.status, "snapshot_used")
            self.assertEqual(auth_coverage.entity_present, True)
            self.assertEqual(auth_coverage.snapshot_at, datetime(2026, 7, 30, 5, 11, 17))
            self.assertEqual(
                auth_coverage.source_report_family,
                "Entra_Users_AuthenticationMethods_Hybrid",
            )
            props = state.scalar_properties_by_family[AUTH_METHODS_FAMILY]
            self.assertIn("MethodsRegistered", {prop.name for prop in props})
            model = build_point_in_time_card(state)
            collection = _collection(model, "authentication_methods")
            self.assertIsNotNone(collection)
            assert collection is not None
            self.assertEqual(collection.coverage, "populated")
            self.assertEqual(len(collection.items), 3)

    def test_legacy_future_hybrid_snapshot_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_user_fixtures(root)
            self._write_hybrid_snapshot(root, "20260730-051117", "email")
            self._write_hybrid_snapshot(root, "20260801-010000", "phone")
            target = datetime(2026, 7, 30, 17, 40, 0)
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                scan_report_history(root),
                target,
            )
            props = {
                prop.name: prop.value
                for prop in state.scalar_properties_by_family.get(AUTH_METHODS_FAMILY, ())
            }
            self.assertIn("MethodsRegistered", props)
            self.assertIn("email", props["MethodsRegistered"])
            self.assertNotIn("phone", props["MethodsRegistered"])

    def test_both_aliases_do_not_duplicate_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_user_fixtures(root)
            write_report(
                root / "Entra_Users_AuthenticationMethods_20260730-040000.csv",
                [
                    {
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "MethodsRegistered": "email",
                        "AuthenticationMethods": "email",
                    }
                ],
            )
            self._write_hybrid_snapshot(root, "20260730-051117", "phone")
            families = scan_report_history(root)
            merged = snapshots_for_adapter(families, AUTH_METHODS_FAMILY)
            self.assertEqual(len(merged), 2)
            target = datetime(2026, 7, 30, 17, 40, 0)
            state = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                families,
                target,
            )
            auth_rows = [
                item for item in state.coverage if item.family == AUTH_METHODS_FAMILY
            ]
            self.assertEqual(len(auth_rows), 1)
            props = {
                prop.name: prop.value
                for prop in state.scalar_properties_by_family.get(AUTH_METHODS_FAMILY, ())
            }
            self.assertIn("phone", props.get("MethodsRegistered", ""))

    def test_indexed_hybrid_file_reconstructs_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_user_fixtures(root)
            self._write_hybrid_snapshot(root, "20260730-051117")
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            try:
                run_sync(root, cold=True, db_path=db_path)
            finally:
                os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)

            connection = open_connection(db_path, readonly=True)
            row = connection.execute(
                """
                SELECT family, status FROM indexed_files
                WHERE relative_path LIKE '%AuthenticationMethods_Hybrid%'
                """
            ).fetchone()
            connection.close()
            self.assertIsNotNone(row)
            self.assertEqual(row["family"], AUTH_METHODS_FAMILY)
            self.assertEqual(row["status"], "indexed")

            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            target = datetime(2026, 7, 30, 17, 40, 0)
            state = repo.reconstruct_state(CanonicalEntityKey("user", "user-1"), target)
            auth_coverage = next(
                item for item in state.coverage if item.family == AUTH_METHODS_FAMILY
            )
            self.assertEqual(auth_coverage.status, "snapshot_used")
            self.assertTrue(auth_coverage.entity_present)
            self.assertEqual(auth_coverage.snapshot_at, datetime(2026, 7, 30, 5, 11, 17))
            self.assertIn("Hybrid", auth_coverage.source_report_family)
            model = build_point_in_time_card(state)
            collection = _collection(model, "authentication_methods")
            self.assertIsNotNone(collection)
            assert collection is not None
            self.assertEqual(len(collection.items), 3)

    def test_index_and_legacy_equivalent_for_hybrid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_user_fixtures(root)
            self._write_hybrid_snapshot(root, "20260730-051117")
            target = datetime(2026, 7, 30, 17, 40, 0)
            legacy = reconstruct_entity_state(
                CanonicalEntityKey("user", "user-1"),
                scan_report_history(root),
                target,
            )

            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            try:
                run_sync(root, cold=True, db_path=db_path)
            finally:
                os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            indexed = repo.reconstruct_state(CanonicalEntityKey("user", "user-1"), target)

            legacy_methods = {
                prop.name: prop.value
                for prop in legacy.scalar_properties_by_family.get(AUTH_METHODS_FAMILY, ())
            }
            indexed_methods = {
                prop.name: prop.value
                for prop in indexed.scalar_properties_by_family.get(AUTH_METHODS_FAMILY, ())
            }
            self.assertEqual(legacy_methods.get("MethodsRegistered"), indexed_methods.get("MethodsRegistered"))
            legacy_cov = next(item for item in legacy.coverage if item.family == AUTH_METHODS_FAMILY)
            indexed_cov = next(item for item in indexed.coverage if item.family == AUTH_METHODS_FAMILY)
            self.assertEqual(legacy_cov.status, indexed_cov.status)
            self.assertEqual(legacy_cov.snapshot_at, indexed_cov.snapshot_at)


if __name__ == "__main__":
    unittest.main()
