import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from diffasaurus.core.entity.index_paths import entity_index_path, source_key
from diffasaurus.core.entity.index_schema import (
    SCHEMA_VERSION,
    initialize_schema,
    metadata_value,
    open_connection,
    probe_fts5,
)
from diffasaurus.core.entity.index_sync import compute_adapter_version


class EntityIndexSchemaTests(unittest.TestCase):
    def test_source_keys_differ_for_different_directories(self):
        self.assertNotEqual(
            source_key(Path("/tmp/tenant-a")),
            source_key(Path("/tmp/tenant-b")),
        )

    def test_schema_initialization_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "index.sqlite3"
            connection = open_connection(
                db_path, readonly=False, adapter_version=compute_adapter_version()
            )
            self.assertEqual(metadata_value(connection, "schema_version"), str(SCHEMA_VERSION))
            self.assertEqual(
                metadata_value(connection, "adapter_version"),
                compute_adapter_version(),
            )
            fts_flag = metadata_value(connection, "fts5_available")
            self.assertIn(fts_flag, {"0", "1"})
            connection.close()

    def test_fts5_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "probe.sqlite3"
            connection = sqlite3.connect(db_path)
            enabled = probe_fts5(connection)
            self.assertIsInstance(enabled, bool)
            connection.close()

    def test_entity_index_path_override(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "custom.sqlite3"
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(override)
            try:
                self.assertEqual(entity_index_path(Path("/any/path")), override)
            finally:
                os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)


if __name__ == "__main__":
    unittest.main()
