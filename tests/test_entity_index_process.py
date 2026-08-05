import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QApplication

from diffasaurus.core.entity.feature import persistent_entity_index_enabled
from diffasaurus.core.entity.index_paths import (
    entity_index_path,
    normalize_reports_path,
    source_key,
)
from diffasaurus.core.entity.index_sync import run_sync
from diffasaurus.core.entity.index_worker_launch import worker_script_argument
from diffasaurus.core.entity.index_lock import acquire_entity_index_lock
from diffasaurus.core.entity.index_paths import entity_index_path, source_key
from diffasaurus.ui.entity_index_controller import EntityIndexController
from diffasaurus.ui.main_window import DiffasaurusWindow
from diffasaurus.ui.progress_coordinator import ProgressCoordinator
from tests.fixtures.entity_index_generator import write_report


class EntityIndexProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_progress_coordinator_does_not_clobber_tasks(self):
        coordinator = ProgressCoordinator()
        seen: list[str] = []

        def global_handler(current, total, label):
            seen.append(f"global:{label}")

        def entity_handler(detail):
            seen.append(f"entity:{detail}")

        coordinator.set_global_handler(global_handler)
        coordinator.set_entity_handler(entity_handler)
        coordinator.start_task("history_scan", 1, foreground=True)
        coordinator.start_task("entity_sync", 1, foreground=False)
        coordinator.report_progress("history_scan", 1, 1, 10, "history")
        coordinator.report_progress("entity_sync", 1, 2, 10, "entity")
        coordinator.finish_task("history_scan", 1)
        coordinator.report_progress("entity_sync", 1, 3, 10, "entity")
        self.assertTrue(any(item.startswith("entity:") for item in seen))

    def test_persistent_index_flag_default_enabled(self):
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX", None)
        self.assertTrue(persistent_entity_index_enabled())
        os.environ["DIFFASAURUS_ENTITY_INDEX"] = "0"
        self.assertFalse(persistent_entity_index_enabled())
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX", None)

    def test_tests_use_isolated_entity_index_root(self):
        root = os.environ.get("DIFFASAURUS_ENTITY_INDEX_ROOT")
        self.assertIsNotNone(root)
        self.assertTrue(Path(root).is_dir())

    def test_path_spellings_share_source_key(self):
        left = Path("/tmp/example/reports")
        right = Path("/tmp/example/./reports")
        self.assertEqual(source_key(left), source_key(right))

    def test_different_sources_have_different_keys(self):
        self.assertNotEqual(
            source_key(Path("/tmp/tenant-a")),
            source_key(Path("/tmp/tenant-b")),
        )

    def test_worker_launch_includes_run_py(self):
        script = worker_script_argument()
        self.assertIsNotNone(script)
        assert script is not None
        self.assertTrue(Path(script).is_file())

    def test_absent_db_triggers_cold_sync_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            db_path = entity_index_path(root)
            self.assertFalse(db_path.is_file())
            controller = EntityIndexController()
            with mock.patch.object(QProcess, "start") as start:
                controller.start_sync(root, cold=False)
                self.assertEqual(start.call_count, 1)
                _program, arguments = start.call_args[0]
                self.assertIn("--cold", arguments)
                self.assertIn(str(normalize_reports_path(root)), arguments)
                self.assertIn(source_key(root), arguments)
                self.assertIn(str(db_path), arguments)

    def test_successful_cold_build_publishes_expected_db(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            try:
                result = run_sync(root, cold=True, db_path=db_path)
            finally:
                os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
            self.assertIn(result.status, ("complete", "completed_with_errors"))
            self.assertTrue(db_path.is_file())

    def test_window_requests_cold_sync_when_database_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            os.environ["DIFFASAURUS_ENTITY_INDEX"] = "1"
            with mock.patch.object(DiffasaurusWindow, "refresh_history"):
                with mock.patch(
                    "diffasaurus.ui.main_window.get_active_reports_dir",
                    return_value=root,
                ):
                    with mock.patch.object(EntityIndexController, "start_sync") as start_sync:
                        window = DiffasaurusWindow()
            window._screen_fitted = True
            self.assertIsNotNone(window._entity_index_controller)
            self.assertTrue(start_sync.called)
            self.assertTrue(any(call.kwargs.get("cold") for call in start_sync.call_args_list))
            window.close()
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX", None)

    def test_failed_to_start_emits_error(self):
        controller = EntityIndexController()
        seen: list[str] = []
        controller.failed.connect(seen.append)
        with mock.patch.object(QProcess, "start"):
            with mock.patch.object(QProcess, "state", return_value=QProcess.ProcessState.NotRunning):
                with mock.patch.object(QProcess, "waitForStarted", return_value=False):
                    with mock.patch.object(QProcess, "errorString", return_value="launch failed"):
                        with tempfile.TemporaryDirectory() as directory:
                            controller.start_sync(Path(directory))
        self.assertTrue(any("launch failed" in message for message in seen))

    def test_nonzero_exit_without_complete_emits_error(self):
        controller = EntityIndexController()
        seen: list[str] = []
        controller.failed.connect(seen.append)
        controller._generation = 1
        controller._complete_received = False
        controller._on_process_finished(1, 1, QProcess.ExitStatus.NormalExit)
        self.assertTrue(seen)
        self.assertIn("exited with code 1", seen[0])

    def test_exit_without_complete_on_success_code_emits_error(self):
        controller = EntityIndexController()
        seen: list[str] = []
        controller.failed.connect(seen.append)
        controller._generation = 1
        controller._complete_received = False
        controller._on_process_finished(1, 0, QProcess.ExitStatus.NormalExit)
        self.assertIn("without a completion event", seen[0])

    def test_controller_blocks_when_lock_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = entity_index_path(root)
            key = source_key(root)
            with acquire_entity_index_lock(db_path, key):
                controller = EntityIndexController()
                seen: list[str] = []
                controller.failed.connect(seen.append)
                controller.start_sync(root)
                self.assertTrue(seen)
                self.assertIn("already running", seen[0].lower())

    def test_window_opens_repository_when_index_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root / "Entra_Users_Properties_20260701-010000.csv",
                [{"Id": "user-1", "UPN": "ada@example.com", "DisplayName": "Ada"}],
            )
            db_path = entity_index_path(root)
            os.environ["DIFFASAURUS_ENTITY_INDEX_DB"] = str(db_path)
            run_sync(root, cold=True, db_path=db_path)
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX_DB", None)
            os.environ["DIFFASAURUS_ENTITY_INDEX"] = "1"
            with mock.patch.object(DiffasaurusWindow, "refresh_history"):
                window = DiffasaurusWindow()
            window._screen_fitted = True
            window.report_dir = root
            if window._entity_index_controller is not None:
                repository = window._entity_index_controller.open_existing(root)
                self.assertIsNotNone(repository)
            window.close()
            os.environ.pop("DIFFASAURUS_ENTITY_INDEX", None)

    def test_legacy_window_has_no_entity_index_controller(self):
        os.environ["DIFFASAURUS_ENTITY_INDEX"] = "0"
        with mock.patch.object(DiffasaurusWindow, "refresh_history"):
            window = DiffasaurusWindow()
        window._screen_fitted = True
        self.assertFalse(window._persistent_entity_index)
        self.assertIsNone(window._entity_index_controller)
        window.close()
        os.environ.pop("DIFFASAURUS_ENTITY_INDEX", None)


if __name__ == "__main__":
    unittest.main()
