import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.paths import project_root, user_data_dir


class PackagedPathTests(unittest.TestCase):
    def test_macos_bundle_separates_resources_from_writable_data(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            executable = home / "Diffasaurus.app" / "Contents" / "MacOS" / "Diffasaurus"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "darwin"),
                patch.object(sys, "executable", str(executable)),
                patch("pathlib.Path.home", return_value=home),
            ):
                self.assertEqual(
                    project_root(),
                    (
                        home
                        / "Diffasaurus.app"
                        / "Contents"
                        / "Resources"
                    ).resolve(),
                )
                self.assertEqual(
                    user_data_dir(),
                    home / "Library" / "Application Support" / "Diffasaurus",
                )

    def test_windows_bundle_uses_local_app_data(self):
        with tempfile.TemporaryDirectory() as directory:
            local_app_data = Path(directory) / "LocalAppData"
            executable = Path(directory) / "Diffasaurus" / "Diffasaurus.exe"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "win32"),
                patch.object(sys, "executable", str(executable)),
                patch.dict("os.environ", {"LOCALAPPDATA": str(local_app_data)}),
            ):
                self.assertEqual(
                    user_data_dir(),
                    local_app_data / "Diffasaurus",
                )


if __name__ == "__main__":
    unittest.main()
