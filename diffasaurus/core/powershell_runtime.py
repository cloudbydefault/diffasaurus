from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from diffasaurus.core.paths import (
    powershell_runtimes_dir,
    project_root,
)
from diffasaurus.core.settings import (
    get_powershell_runtime_path,
    set_powershell_runtime_path,
)


VERSION_SCRIPT = (
    "$PSVersionTable.PSVersion.ToString();"
    "[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()"
)


@dataclass(frozen=True)
class PowerShellRuntime:
    path: Path
    version: str
    source: str
    architecture: str = ""
    managed: bool = False

    @property
    def supported(self) -> bool:
        match = re.match(r"(\d+)", self.version)
        return bool(match and int(match.group(1)) >= 7)

    @property
    def label(self) -> str:
        architecture = f" · {self.architecture}" if self.architecture else ""
        return f"PowerShell {self.version} · {self.source}{architecture}"

    @property
    def identity(self) -> str:
        try:
            return str(self.path.resolve())
        except OSError:
            return str(self.path.absolute())


def _runtime_name() -> str:
    return "pwsh.exe" if sys.platform == "win32" else "pwsh"


def _candidate_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def _add_candidate(
    candidates: dict[str, tuple[Path, str, bool]],
    path: Path | str | None,
    source: str,
    managed: bool = False,
) -> None:
    if not path:
        return
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return
    if sys.platform != "win32" and not os.access(candidate, os.X_OK):
        return
    candidates.setdefault(_candidate_key(candidate), (candidate, source, managed))


def _login_shell_pwsh() -> Path | None:
    if sys.platform == "win32":
        return None
    shell = os.environ.get("SHELL") or ("/bin/zsh" if sys.platform == "darwin" else "/bin/sh")
    if not Path(shell).is_file():
        return None
    try:
        result = subprocess.run(
            (shell, "-lc", "command -v pwsh"),
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip().splitlines()
    return Path(value[-1]) if result.returncode == 0 and value else None


def _system_candidates() -> list[Path]:
    paths: list[Path] = []
    discovered = shutil.which("pwsh")
    if discovered:
        paths.append(Path(discovered))
    login_shell = _login_shell_pwsh()
    if login_shell:
        paths.append(login_shell)

    if sys.platform == "darwin":
        paths.extend(
            Path(value)
            for value in (
                "/opt/homebrew/bin/pwsh",
                "/usr/local/bin/pwsh",
                "/opt/microsoft/powershell/7/pwsh",
                "/usr/local/microsoft/powershell/7/pwsh",
            )
        )
        for cellar in (
            Path("/opt/homebrew/Cellar/powershell"),
            Path("/usr/local/Cellar/powershell"),
        ):
            if cellar.is_dir():
                paths.extend(cellar.glob("*/libexec/pwsh"))
    elif sys.platform == "win32":
        for variable in ("ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if not base:
                continue
            root = Path(base)
            paths.extend((root / "PowerShell").glob("*/pwsh.exe"))
            paths.extend((root / "Microsoft" / "PowerShell").glob("*/pwsh.exe"))
    else:
        paths.extend(
            Path(value)
            for value in (
                "/usr/bin/pwsh",
                "/usr/local/bin/pwsh",
                "/opt/microsoft/powershell/7/pwsh",
                "/snap/bin/pwsh",
            )
        )
    return paths


def probe_powershell_runtime(
    path: Path,
    source: str,
    managed: bool = False,
) -> PowerShellRuntime | None:
    try:
        result = subprocess.run(
            (
                str(path),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                VERSION_SCRIPT,
            ),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines or not re.match(r"^\d+(?:\.\d+){1,3}", lines[0]):
        return None
    return PowerShellRuntime(
        path=path,
        version=lines[0],
        source=source,
        architecture=lines[1] if len(lines) > 1 else "",
        managed=managed,
    )


def _version_key(runtime: PowerShellRuntime) -> tuple[int, ...]:
    values = tuple(int(value) for value in re.findall(r"\d+", runtime.version)[:4])
    return values + (0,) * (4 - len(values))


def discover_powershell_runtimes() -> list[PowerShellRuntime]:
    candidates: dict[str, tuple[Path, str, bool]] = {}
    selected = get_powershell_runtime_path()

    name = _runtime_name()
    managed_root = powershell_runtimes_dir()
    for path in managed_root.rglob(name):
        _add_candidate(candidates, path, "Portable", True)

    legacy_root = project_root() / "pwsh"
    if legacy_root != managed_root and legacy_root.is_dir():
        for path in legacy_root.rglob(name):
            _add_candidate(candidates, path, "Bundled portable")

    for path in _system_candidates():
        _add_candidate(candidates, path, "System")

    # Keep the persisted path even when it lives outside a standard install
    # location. Known system and managed paths retain their more useful source.
    _add_candidate(candidates, selected, "Custom path", _is_managed_path(selected))

    runtimes = [
        runtime
        for path, source, managed in candidates.values()
        if (runtime := probe_powershell_runtime(path, source, managed)) is not None
    ]
    runtimes.sort(
        key=lambda runtime: (
            runtime.identity != _candidate_key(selected) if selected else True,
            runtime.source != "System",
            tuple(-value for value in _version_key(runtime)),
            runtime.identity.lower(),
        )
    )
    return runtimes


def selected_powershell_runtime(
    runtimes: list[PowerShellRuntime] | None = None,
) -> PowerShellRuntime | None:
    runtimes = runtimes if runtimes is not None else discover_powershell_runtimes()
    if not runtimes:
        return None
    selected = get_powershell_runtime_path()
    if selected:
        key = _candidate_key(selected)
        for runtime in runtimes:
            if runtime.identity == key:
                return runtime
    supported = [runtime for runtime in runtimes if runtime.supported]
    return supported[0] if supported else runtimes[0]


def select_powershell_runtime(runtime: PowerShellRuntime | None) -> None:
    set_powershell_runtime_path(runtime.path if runtime else None)


def _is_managed_path(path: Path | None) -> bool:
    if not path:
        return False
    try:
        path.resolve().relative_to(powershell_runtimes_dir().resolve())
        return True
    except (OSError, ValueError):
        return False


def find_portable_executable(folder: Path) -> Path | None:
    direct = folder / _runtime_name()
    if direct.is_file():
        return direct
    matches = sorted(folder.rglob(_runtime_name()), key=lambda path: len(path.parts))
    return matches[0] if matches else None


def import_portable_runtime(folder: Path) -> PowerShellRuntime:
    source = folder.expanduser().resolve()
    executable = find_portable_executable(source)
    if executable is None:
        raise ValueError(f"No {_runtime_name()} executable was found in that folder.")
    probed = probe_powershell_runtime(executable, "Portable", True)
    if probed is None:
        raise ValueError("The selected portable PowerShell runtime could not be started.")

    managed_root = powershell_runtimes_dir().resolve()
    try:
        source.relative_to(managed_root)
        select_powershell_runtime(probed)
        return probed
    except ValueError:
        pass

    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", probed.version)
    safe_architecture = re.sub(r"[^A-Za-z0-9._-]+", "-", probed.architecture or "unknown")
    destination = managed_root / f"PowerShell-{safe_version}-{safe_architecture}"
    suffix = 2
    while destination.exists():
        destination = managed_root / f"PowerShell-{safe_version}-{safe_architecture}-{suffix}"
        suffix += 1
    shutil.copytree(source, destination)
    relative_executable = executable.relative_to(source)
    imported = probe_powershell_runtime(
        destination / relative_executable,
        "Portable",
        True,
    )
    if imported is None:
        shutil.rmtree(destination)
        raise RuntimeError("The imported PowerShell runtime did not pass validation.")
    select_powershell_runtime(imported)
    return imported


def remove_portable_runtime(runtime: PowerShellRuntime) -> None:
    managed_root = powershell_runtimes_dir().resolve()
    try:
        relative = runtime.path.resolve().relative_to(managed_root)
    except (OSError, ValueError) as exc:
        raise ValueError("Only managed portable runtimes can be removed.") from exc
    if not relative.parts:
        raise ValueError("The PowerShell runtime folder cannot be removed.")
    runtime_root = managed_root / relative.parts[0]
    shutil.rmtree(runtime_root)
    selected = get_powershell_runtime_path()
    if selected and _candidate_key(selected) == runtime.identity:
        set_powershell_runtime_path(None)
