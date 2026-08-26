import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.session_state import (
    REASON_PRE_UPDATE,
    SCHEMA_VERSION,
    SessionState,
    clear_session_state,
    consume_pre_update_session,
    load_session_state,
    save_pre_update_session,
    session_state_path,
)


class SessionStateTests(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            save_pre_update_session(
                page=7,
                report_family="users",
                snapshot_path="/tmp/users.csv",
                explorer_view="dashboard",
                base_dir=base,
            )
            state = load_session_state(base)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state.page, 7)
            self.assertEqual(state.report_family, "users")
            self.assertEqual(state.snapshot_path, "/tmp/users.csv")
            self.assertEqual(state.explorer_view, "dashboard")
            self.assertEqual(state.reason, REASON_PRE_UPDATE)
            self.assertEqual(state.schema_version, SCHEMA_VERSION)

    def test_atomic_write_uses_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            save_pre_update_session(page=1, base_dir=base)
            path = session_state_path(base)
            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(load_session_state(Path(directory)))

    def test_corrupt_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = session_state_path(base)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_session_state(base))

    def test_schema_mismatch_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = session_state_path(base)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"schema_version": 99, "reason": REASON_PRE_UPDATE, "page": 1}),
                encoding="utf-8",
            )
            self.assertIsNone(load_session_state(base))

    def test_consume_once_deletes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            save_pre_update_session(page=3, base_dir=base)
            first = consume_pre_update_session(base)
            second = consume_pre_update_session(base)
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertFalse(session_state_path(base).exists())

    def test_clear_session_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            save_pre_update_session(page=2, base_dir=base)
            clear_session_state(base)
            self.assertFalse(session_state_path(base).exists())

    def test_from_dict_rejects_invalid_view(self):
        state = SessionState.from_dict(
            {
                "schema_version": SCHEMA_VERSION,
                "reason": REASON_PRE_UPDATE,
                "page": 1,
                "explorer_view": "invalid",
            }
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.explorer_view, "table")


if __name__ == "__main__":
    unittest.main()
