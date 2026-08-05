import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_projection import (
    ALIAS_PROJECTION_VERSION,
    SEARCH_PROJECTION_VERSION,
    alias_projection_version,
    ensure_search_projections,
    projections_need_repair,
    repair_search_projections,
    search_projection_version,
)
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_schema import metadata_value, open_connection
from diffasaurus.core.entity.index_sync import _finalize_entity, run_sync
from diffasaurus.core.entity.resolution import SearchResult
from diffasaurus.core.entity.types import CanonicalEntityKey, EntityRecord
from tests.fixtures.entity_index_generator import write_report


def _build_index(root: Path, *, db_path: Path | None = None) -> Path:
    os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path or entity_index_path(root))
    run_sync(root, cold=True, db_path=db_path)
    os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
    return db_path or entity_index_path(root)


def _write_two_user_fixture(root: Path) -> None:
    write_report(
        root / "Entra_Users_Properties_20260701-010000.csv",
        [
            {
                "Id": "guid-duthil",
                "UPN": "jonathan.duthil@example.com",
                "DisplayName": "Jonathan Duthil",
            },
            {
                "Id": "guid-adnet",
                "UPN": "adm_adnet@example.com",
                "DisplayName": "ADM ADNET",
            },
        ],
    )


def _apply_legacy_alias_projection_corruption(
    connection: sqlite3.Connection,
    source_id: int,
) -> None:
    connection.execute("DELETE FROM entity_aliases")
    if metadata_value(connection, "fts5_available") == "1":
        connection.execute("DELETE FROM entity_search_fts")

    entities = connection.execute(
        "SELECT id, entity_type, primary_id FROM entities WHERE source_id=?",
        (source_id,),
    ).fetchall()
    for entity in entities:
        entity_id = int(entity["id"])
        alias_map: dict[tuple[str, str, str], tuple[str, str, str]] = {}
        observations = connection.execute(
            """
            SELECT ao.kind, ao.normalized_value, ao.observed_at, ao.source_family
            FROM alias_observations ao
            JOIN entity_occurrences eo ON eo.file_id = ao.file_id
            WHERE eo.entity_id = ?
            """,
            (entity_id,),
        ).fetchall()
        for observation in observations:
            key = (
                observation["kind"],
                observation["normalized_value"],
                observation["source_family"],
            )
            seen_at = observation["observed_at"]
            if key not in alias_map:
                alias_map[key] = (seen_at, seen_at, observation["normalized_value"])
            else:
                first, last, display = alias_map[key]
                alias_map[key] = (min(first, seen_at), max(last, seen_at), display)

        occurrence_rows = connection.execute(
            """
            SELECT aliases_json, observed_at
            FROM entity_occurrences WHERE entity_id=?
            """,
            (entity_id,),
        ).fetchall()
        for occurrence in occurrence_rows:
            for alias in json.loads(occurrence["aliases_json"] or "[]"):
                normalized = str(alias.get("value", "")).lower()
                if not normalized:
                    continue
                key = (
                    alias.get("kind", ""),
                    normalized,
                    alias.get("source_family", ""),
                )
                seen_at = alias.get("observed_at") or occurrence["observed_at"]
                if key not in alias_map:
                    alias_map[key] = (seen_at, seen_at, str(alias.get("value", "")))
                else:
                    first, last, display = alias_map[key]
                    alias_map[key] = (min(first, seen_at), max(last, seen_at), display)

        latest_occurrence = connection.execute(
            """
            SELECT display_name FROM entity_occurrences
            WHERE entity_id=? ORDER BY observed_at DESC LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        display_name = latest_occurrence["display_name"] if latest_occurrence else ""

        for (kind, normalized, family), (first_seen, last_seen, display_value) in alias_map.items():
            connection.execute(
                """
                INSERT INTO entity_aliases(
                    entity_id, kind, normalized_value, display_value,
                    first_seen, last_seen, source_family
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (entity_id, kind, normalized, display_value, first_seen, last_seen, family),
            )
        if metadata_value(connection, "fts5_available") == "1":
            alias_values = " ".join(sorted({normalized for (_, normalized, _) in alias_map}))
            connection.execute(
                """
                INSERT INTO entity_search_fts(
                    entity_id, entity_type, primary_id, display_name, alias_values
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    entity["entity_type"],
                    entity["primary_id"],
                    display_name,
                    alias_values,
                ),
            )

    connection.execute(
        "DELETE FROM metadata WHERE key IN ("
        "'alias_projection_version', 'search_projection_version', 'projection_repaired_at'"
        ")"
    )
    connection.commit()


def _aliases_for_entity(connection: sqlite3.Connection, entity_id: int) -> list[str]:
    return [
        row["normalized_value"]
        for row in connection.execute(
            """
            SELECT normalized_value
            FROM entity_aliases
            WHERE entity_id=?
            ORDER BY normalized_value
            """,
            (entity_id,),
        )
    ]


def _entity_id(connection: sqlite3.Connection, source_id: int, primary_id: str) -> int:
    row = connection.execute(
        """
        SELECT id FROM entities
        WHERE source_id=? AND entity_type='user' AND primary_id=?
        """,
        (source_id, primary_id),
    ).fetchone()
    assert row is not None
    return int(row["id"])


class EntityIndexProjectionTests(unittest.TestCase):
    def test_warm_unchanged_sync_repairs_legacy_projections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = entity_index_path(root)
            _write_two_user_fixture(root)
            _build_index(root, db_path=db_path)

            connection = open_connection(db_path, readonly=False)
            source_id = int(
                connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
            )
            _apply_legacy_alias_projection_corruption(connection, source_id)
            connection.close()

            self.assertTrue(projections_need_repair(open_connection(db_path)))

            result = run_sync(root, cold=False, db_path=db_path)
            self.assertEqual(result.parsed, 0)
            self.assertGreater(result.reused, 0)

            connection = open_connection(db_path, readonly=True)
            duthil_aliases = _aliases_for_entity(
                connection, _entity_id(connection, source_id, "guid-duthil")
            )
            adnet_aliases = _aliases_for_entity(
                connection, _entity_id(connection, source_id, "guid-adnet")
            )
            self.assertIn("jonathan.duthil@example.com", duthil_aliases)
            self.assertNotIn("adm_adnet@example.com", duthil_aliases)
            self.assertIn("adm_adnet@example.com", adnet_aliases)
            self.assertNotIn("jonathan.duthil@example.com", adnet_aliases)
            self.assertEqual(alias_projection_version(connection), ALIAS_PROJECTION_VERSION)
            self.assertEqual(search_projection_version(connection), SEARCH_PROJECTION_VERSION)
            connection.close()

            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            search = repo.search("duthil", "user")
            self.assertEqual(len(search.matches), 1)
            self.assertEqual(search.matches[0].key.primary_id, "guid-duthil")
            exact = repo.search("jonathan.duthil@example.com", "user")
            self.assertEqual(exact.matches[0].key, CanonicalEntityKey("user", "guid-duthil"))
            repo.close()

    def test_repair_twice_is_no_op(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = entity_index_path(root)
            _write_two_user_fixture(root)
            _build_index(root, db_path=db_path)

            connection = open_connection(db_path, readonly=False)
            source_id = int(
                connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
            )
            _apply_legacy_alias_projection_corruption(connection, source_id)
            connection.close()

            first = ensure_search_projections(root, db_path=db_path)
            second = ensure_search_projections(root, db_path=db_path)
            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_repair_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = entity_index_path(root)
            _write_two_user_fixture(root)
            _build_index(root, db_path=db_path)

            connection = open_connection(db_path, readonly=False)
            source_id = int(
                connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
            )
            _apply_legacy_alias_projection_corruption(connection, source_id)
            aliases_before = connection.execute("SELECT COUNT(*) AS c FROM entity_aliases").fetchone()["c"]
            entity_ids = [
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM entities WHERE source_id=? ORDER BY id",
                    (source_id,),
                )
            ]
            failing_id = entity_ids[-1]

            original_finalize = _finalize_entity

            def _failing_finalize(conn, sid, entity_id):
                if entity_id == failing_id:
                    raise RuntimeError("injected projection repair failure")
                return original_finalize(conn, sid, entity_id)

            with patch(
                "diffasaurus.core.entity.index_sync._finalize_entity",
                side_effect=_failing_finalize,
            ):
                with self.assertRaises(RuntimeError):
                    repair_search_projections(connection, source_id)

            aliases_after = connection.execute("SELECT COUNT(*) AS c FROM entity_aliases").fetchone()["c"]
            self.assertEqual(aliases_before, aliases_after)
            self.assertTrue(projections_need_repair(connection))
            connection.close()

    def test_report_sources_remain_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root_a = Path(directory) / "a"
            root_b = Path(directory) / "b"
            root_a.mkdir()
            root_b.mkdir()
            db_a = entity_index_path(root_a)
            db_b = entity_index_path(root_b)

            _write_two_user_fixture(root_a)
            write_report(
                root_b / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-b", "UPN": "user.b@example.com", "DisplayName": "User B"}],
            )
            _build_index(root_a, db_path=db_a)
            _build_index(root_b, db_path=db_b)

            connection_a = open_connection(db_a, readonly=False)
            source_a = int(
                connection_a.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
            )
            _apply_legacy_alias_projection_corruption(connection_a, source_a)
            connection_a.close()

            ensure_search_projections(root_a, db_path=db_a)

            connection_b = open_connection(db_b, readonly=True)
            source_b = int(
                connection_b.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
            )
            self.assertFalse(projections_need_repair(connection_b))
            aliases_b = _aliases_for_entity(
                connection_b, _entity_id(connection_b, source_b, "user-b")
            )
            self.assertEqual(aliases_b, ["user.b@example.com"])
            connection_b.close()

    def test_repository_cache_invalidated_after_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = entity_index_path(root)
            _write_two_user_fixture(root)
            _build_index(root, db_path=db_path)

            connection = open_connection(db_path, readonly=False)
            source_id = int(
                connection.execute("SELECT id FROM report_sources LIMIT 1").fetchone()["id"]
            )
            _apply_legacy_alias_projection_corruption(connection, source_id)
            connection.close()

            repo = EntityIndexRepository.open(root, db_path=db_path)
            assert repo is not None
            generation_before = repo.generation
            wrong_matches = tuple(
                EntityRecord(
                    key=CanonicalEntityKey("user", primary_id),
                    display_name=primary_id,
                )
                for primary_id in ("guid-duthil", "guid-adnet")
            )
            repo._search_cache.set(
                f"{generation_before}:user:duthil:50",
                SearchResult(wrong_matches, True),
            )
            self.assertEqual(len(repo.search("duthil", "user").matches), 2)

            stats = ensure_search_projections(root, db_path=db_path)
            self.assertIsNotNone(stats)
            repo.invalidate_caches()
            generation_after = repo.generation
            self.assertNotEqual(generation_before, generation_after)

            repaired = repo.search("duthil", "user")
            self.assertEqual(len(repaired.matches), 1)
            repo.close()


if __name__ == "__main__":
    unittest.main()
