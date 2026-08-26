import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.settings import (
    DEFAULT_SETTINGS,
    load_settings,
    managed_updates_enabled,
    save_settings,
    settings_path,
    update_channel,
    updates_enabled,
)


class UpdateSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self._tmpdir.name)
        (self.base / "config").mkdir(parents=True, exist_ok=True)
        self._settings_patch = patch(
            "diffasaurus.core.settings.settings_path",
            return_value=self.base / "config" / "settings.json",
        )
        self._settings_patch.start()

    def tearDown(self):
        self._settings_patch.stop()
        self._tmpdir.cleanup()

    def test_defaults_include_update_fields(self):
        settings = load_settings()
        for key, value in (
            ("updates_enabled", True),
            ("managed_updates", False),
            ("update_channel", "preview"),
        ):
            self.assertEqual(settings[key], value)

    def test_old_settings_without_update_fields_load_normally(self):
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"report_source": "local", "external_reports_path": ""}),
            encoding="utf-8",
        )
        settings = load_settings()
        self.assertEqual(settings["report_source"], "local")
        self.assertTrue(settings["updates_enabled"])
        self.assertFalse(settings["managed_updates"])
        self.assertEqual(settings["update_channel"], "preview")

    def test_updates_enabled_false(self):
        save_settings({**DEFAULT_SETTINGS, "updates_enabled": False})
        self.assertFalse(updates_enabled())

    def test_managed_updates_true(self):
        save_settings({**DEFAULT_SETTINGS, "managed_updates": True})
        self.assertTrue(managed_updates_enabled())

    def test_preview_channel_validation(self):
        save_settings({**DEFAULT_SETTINGS, "update_channel": "stable"})
        self.assertEqual(update_channel(), "preview")


if __name__ == "__main__":
    unittest.main()
