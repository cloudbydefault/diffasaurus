import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from diffasaurus.core.entity.history import reconstruct_entity_state
from diffasaurus.core.entity.index_paths import entity_index_path
from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.types import CanonicalEntityKey
from diffasaurus.core.report_history import scan_report_history
from tests.fixtures.entity_index_generator import write_report


class EntityIndexParityTests(unittest.TestCase):
    def test_point_in_time_matches_csv_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
                root / "Entra_Users_Properties_20260801-010000.csv",
                [
                    {
                        "Id": "user-1",
                        "UPN": "ada@example.com",
                        "DisplayName": "Ada",
                        "Department": "IT",
                    }
                ],
            )
            families = scan_report_history(root)
            target = datetime(2026, 7, 15, 12, 0, 0)
            key = CanonicalEntityKey("user", "user-1")
            csv_state = reconstruct_entity_state(key, families, target)

            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(entity_index_path(root))
            run_sync(root, cold=True)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
            repo = EntityIndexRepository.open(root)
            assert repo is not None
            indexed_state = repo.reconstruct_state(key, target)
            repo.close()

            csv_props = csv_state.properties_by_family.get("Entra_Users_Properties", ())
            indexed_props = indexed_state.properties_by_family.get(
                "Entra_Users_Properties", ()
            )
            csv_department = next(
                (prop.value for prop in csv_props if prop.name == "Department"),
                "",
            )
            indexed_department = next(
                (prop.value for prop in indexed_props if prop.name == "Department"),
                "",
            )
            self.assertEqual(csv_state.presence, indexed_state.presence)
            self.assertEqual(csv_department, indexed_department)


if __name__ == "__main__":
    unittest.main()
