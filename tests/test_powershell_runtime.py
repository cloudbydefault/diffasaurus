import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.powershell_runtime import (
    PowerShellRuntime,
    discover_powershell_runtimes,
    import_portable_runtime,
    probe_powershell_runtime,
    remove_portable_runtime,
    selected_powershell_runtime,
)


def make_fake_pwsh(folder: Path, version: str = "7.5.4", architecture: str = "Arm64") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    executable = folder / ("pwsh.exe" if os.name == "nt" else "pwsh")
    if os.name == "nt":
        raise unittest.SkipTest("The fake executable helper is POSIX-only.")
    executable.write_text(
        f"#!/bin/sh\nprintf '%s\\n%s\\n' '{version}' '{architecture}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


class PowerShellRuntimeTests(unittest.TestCase):
    def test_probe_reads_version_and_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = make_fake_pwsh(Path(directory))
            runtime = probe_powershell_runtime(executable, "System")

        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.version, "7.5.4")
        self.assertEqual(runtime.architecture, "Arm64")
        self.assertTrue(runtime.supported)

    def test_discovery_does_not_depend_on_gui_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = make_fake_pwsh(root / "system")
            managed = root / "managed"
            project = root / "application"
            with (
                patch(
                    "diffasaurus.core.powershell_runtime._system_candidates",
                    return_value=[executable],
                ),
                patch(
                    "diffasaurus.core.powershell_runtime.powershell_runtimes_dir",
                    return_value=managed,
                ),
                patch(
                    "diffasaurus.core.powershell_runtime.project_root",
                    return_value=project,
                ),
                patch(
                    "diffasaurus.core.powershell_runtime.get_powershell_runtime_path",
                    return_value=None,
                ),
            ):
                managed.mkdir()
                runtimes = discover_powershell_runtimes()

        self.assertEqual(len(runtimes), 1)
        self.assertEqual(runtimes[0].path, executable)
        self.assertEqual(runtimes[0].source, "System")

    def test_persisted_runtime_is_preferred(self):
        first = PowerShellRuntime(Path("/tmp/pwsh-7.5"), "7.5.4", "System")
        selected = PowerShellRuntime(Path("/tmp/pwsh-7.4"), "7.4.12", "Portable", managed=True)
        with patch(
            "diffasaurus.core.powershell_runtime.get_powershell_runtime_path",
            return_value=selected.path,
        ):
            self.assertEqual(
                selected_powershell_runtime([first, selected]),
                selected,
            )

    def test_import_copies_complete_portable_folder_and_selects_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "downloaded"
            managed = root / "managed"
            executable = make_fake_pwsh(source)
            dependency = source / "libexample.dylib"
            dependency.write_text("runtime dependency", encoding="utf-8")
            selected_paths = []
            with (
                patch(
                    "diffasaurus.core.powershell_runtime.powershell_runtimes_dir",
                    return_value=managed,
                ),
                patch(
                    "diffasaurus.core.powershell_runtime.set_powershell_runtime_path",
                    side_effect=selected_paths.append,
                ),
            ):
                managed.mkdir()
                runtime = import_portable_runtime(source)

            self.assertNotEqual(runtime.path, executable)
            self.assertTrue(runtime.path.is_file())
            self.assertTrue((runtime.path.parent / dependency.name).is_file())
            self.assertEqual(selected_paths[-1], runtime.path)
            self.assertTrue(runtime.managed)

    def test_remove_is_limited_to_managed_portables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / "managed"
            executable = make_fake_pwsh(managed / "PowerShell-7.5.4-Arm64")
            runtime = PowerShellRuntime(
                executable,
                "7.5.4",
                "Portable",
                "Arm64",
                managed=True,
            )
            with (
                patch(
                    "diffasaurus.core.powershell_runtime.powershell_runtimes_dir",
                    return_value=managed,
                ),
                patch(
                    "diffasaurus.core.powershell_runtime.get_powershell_runtime_path",
                    return_value=None,
                ),
            ):
                remove_portable_runtime(runtime)
            self.assertFalse(executable.parent.exists())

            unmanaged = PowerShellRuntime(root / "outside" / "pwsh", "7.5.4", "System")
            with patch(
                "diffasaurus.core.powershell_runtime.powershell_runtimes_dir",
                return_value=managed,
            ):
                with self.assertRaises(ValueError):
                    remove_portable_runtime(unmanaged)


if __name__ == "__main__":
    unittest.main()
