import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diffasaurus.core.powershell_environment import (
    ISOLATION_PREAMBLE,
    REPORT_COMMAND,
    PowerShellModule,
    copy_installed_modules,
    isolated_module_path,
    list_installed_modules,
    powershell_environment,
    runtime_environment_dir,
    runtime_modules_dir,
)
from diffasaurus.core.powershell_runtime import (
    PowerShellRuntime,
    probe_powershell_runtime,
)


class PowerShellEnvironmentTests(unittest.TestCase):
    def test_each_runtime_has_a_distinct_private_module_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = PowerShellRuntime(root / "pwsh-7.5", "7.5.4", "System", "Arm64")
            second = PowerShellRuntime(root / "pwsh-7.6", "7.6.4", "Portable", "Arm64")
            with patch(
                "diffasaurus.core.powershell_environment.powershell_environments_dir",
                return_value=root / "environments",
            ):
                first_modules = runtime_modules_dir(first)
                second_modules = runtime_modules_dir(second)

            self.assertNotEqual(first_modules, second_modules)
            self.assertTrue(first_modules.is_dir())
            self.assertTrue(second_modules.is_dir())

    def test_report_bootstrap_resets_module_path_before_running_script(self):
        reset_position = REPORT_COMMAND.index("$env:PSModulePath")
        script_position = REPORT_COMMAND.index("DIFFASAURUS_SCRIPT_PATH")
        self.assertLess(reset_position, script_position)

    def test_environment_uses_private_cache_and_module_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = PowerShellRuntime(root / "pwsh", "7.5.4", "System", "Arm64")
            with patch(
                "diffasaurus.core.powershell_environment.powershell_environments_dir",
                return_value=root / "environments",
            ):
                values = powershell_environment(
                    runtime,
                    {"PSModulePath": "/global/modules"},
                )
                environment_root = runtime_environment_dir(runtime)

            self.assertNotIn(
                "/global/modules",
                values["DIFFASAURUS_PS_MODULE_PATH"],
            )
            self.assertTrue(
                values["DIFFASAURUS_MODULE_ROOT"].startswith(str(environment_root))
            )
            self.assertTrue(
                values["PSModuleAnalysisCachePath"].startswith(str(environment_root))
            )

    def test_installed_inventory_excludes_builtin_and_isolated_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "runtime" / "pwsh"
            executable.parent.mkdir()
            executable.touch()
            runtime = PowerShellRuntime(executable, "7.5.4", "System", "Arm64")
            native = root / "native" / "Example" / "1.2.3"
            native.mkdir(parents=True)
            with patch(
                "diffasaurus.core.powershell_environment.powershell_environments_dir",
                return_value=root / "environments",
            ):
                private = runtime_modules_dir(runtime) / "Private" / "1.0.0"
                builtin = executable.parent / "Modules" / "BuiltIn"
                private.mkdir(parents=True)
                builtin.mkdir(parents=True)
                inventory = [
                    PowerShellModule("Example", "1.2.3", native),
                    PowerShellModule("Private", "1.0.0", private),
                    PowerShellModule("BuiltIn", "7.0.0", builtin),
                ]
                with patch(
                    "diffasaurus.core.powershell_environment._module_inventory",
                    return_value=inventory,
                ):
                    installed = list_installed_modules(runtime)

            self.assertEqual(installed, [inventory[0]])

    def test_native_module_copy_creates_an_independent_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "native" / "Example" / "1.2.3"
            source.mkdir(parents=True)
            (source / "Example.psd1").write_text("module", encoding="utf-8")
            runtime = PowerShellRuntime(root / "pwsh", "7.5.4", "System", "Arm64")
            module = PowerShellModule("Example", "1.2.3", source)
            with patch(
                "diffasaurus.core.powershell_environment.powershell_environments_dir",
                return_value=root / "environments",
            ):
                copied = copy_installed_modules(runtime, [module])
                destination = runtime_modules_dir(runtime) / "Example" / "1.2.3"

            self.assertEqual(copied, 1)
            self.assertEqual(
                (destination / "Example.psd1").read_text(encoding="utf-8"),
                "module",
            )
            self.assertNotEqual(source, destination)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_live_session_cannot_see_normal_user_module_path_after_bootstrap(self):
        executable = Path(shutil.which("pwsh"))
        runtime = probe_powershell_runtime(executable, "System")
        self.assertIsNotNone(runtime)
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "diffasaurus.core.powershell_environment.powershell_environments_dir",
                return_value=Path(directory) / "environments",
            ):
                expected = isolated_module_path(runtime)
                result = subprocess.run(
                    (
                        str(runtime.path),
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        ISOLATION_PREAMBLE
                        + "Write-Output ('DIFFASAURUS_PATH=' + $env:PSModulePath)",
                    ),
                    env=powershell_environment(runtime, os.environ),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

        self.assertEqual(result.returncode, 0, result.stderr)
        markers = [
            line.removeprefix("DIFFASAURUS_PATH=")
            for line in result.stdout.splitlines()
            if line.startswith("DIFFASAURUS_PATH=")
        ]
        self.assertEqual(markers, [expected], result.stdout)
        self.assertNotIn(".local/share/powershell/Modules", result.stdout)


if __name__ == "__main__":
    unittest.main()
