from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from diffasaurus.core.paths import powershell_environments_dir
from diffasaurus.core.powershell_runtime import PowerShellRuntime


ISOLATION_PREAMBLE = (
    "$env:PSModulePath = $env:DIFFASAURUS_PS_MODULE_PATH;"
    "$env:PSModuleAnalysisCachePath = $env:DIFFASAURUS_PS_MODULE_CACHE;"
)

REPORT_COMMAND = (
    ISOLATION_PREAMBLE
    + "& $env:DIFFASAURUS_SCRIPT_PATH;"
    "if (-not $?) { exit 1 }"
)


@dataclass(frozen=True)
class PowerShellModule:
    name: str
    version: str
    path: Path


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "unknown"


def runtime_environment_key(runtime: PowerShellRuntime) -> str:
    identity = f"{runtime.identity}\0{runtime.version}\0{runtime.architecture}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return (
        f"PowerShell-{_safe_name(runtime.version)}-"
        f"{_safe_name(runtime.architecture or 'unknown')}-{digest}"
    )


def runtime_environment_dir(runtime: PowerShellRuntime) -> Path:
    path = powershell_environments_dir() / runtime_environment_key(runtime)
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_modules_dir(runtime: PowerShellRuntime) -> Path:
    path = runtime_environment_dir(runtime) / "Modules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_builtin_modules_dir(runtime: PowerShellRuntime) -> Path:
    try:
        home = runtime.path.resolve().parent
    except OSError:
        home = runtime.path.absolute().parent
    return home / "Modules"


def isolated_module_paths(runtime: PowerShellRuntime) -> tuple[Path, ...]:
    private = runtime_modules_dir(runtime)
    built_in = runtime_builtin_modules_dir(runtime)
    return (private, built_in) if built_in.is_dir() else (private,)


def isolated_module_path(runtime: PowerShellRuntime) -> str:
    return os.pathsep.join(str(path) for path in isolated_module_paths(runtime))


def powershell_environment(
    runtime: PowerShellRuntime,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment_root = runtime_environment_dir(runtime)
    environment.update(
        {
            "DIFFASAURUS_PS_MODULE_PATH": isolated_module_path(runtime),
            "DIFFASAURUS_MODULE_ROOT": str(runtime_modules_dir(runtime)),
            "DIFFASAURUS_BUILTIN_MODULE_ROOT": str(
                runtime_builtin_modules_dir(runtime)
            ),
            "DIFFASAURUS_PS_MODULE_CACHE": str(
                environment_root / "ModuleAnalysisCache"
            ),
            "PSModuleAnalysisCachePath": str(
                environment_root / "ModuleAnalysisCache"
            ),
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        }
    )
    return environment


def private_module_count(runtime: PowerShellRuntime) -> int:
    root = runtime_modules_dir(runtime)
    return sum(
        1 for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")
    )


def _module_inventory(
    runtime: PowerShellRuntime,
    script: str,
) -> list[PowerShellModule]:
    try:
        result = subprocess.run(
            (
                str(runtime.path),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ),
            env=powershell_environment(runtime),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(values, dict):
        values = [values]
    modules: list[PowerShellModule] = []
    for value in values if isinstance(values, list) else []:
        path = Path(str(value.get("ModuleBase", "")))
        if not path:
            continue
        modules.append(
            PowerShellModule(
                name=str(value.get("Name", "")),
                version=str(value.get("Version", "")),
                path=path,
            )
        )
    return modules


def _inventory_script(prefix: str = "") -> str:
    return (
        prefix
        + "Get-Module -ListAvailable | "
        "Sort-Object Name,Version -Unique | "
        "Select-Object Name,@{n='Version';e={$_.Version.ToString()}},ModuleBase | "
        "ConvertTo-Json -Compress"
    )


def list_private_modules(runtime: PowerShellRuntime) -> list[PowerShellModule]:
    root = runtime_modules_dir(runtime).resolve()
    modules = _module_inventory(runtime, _inventory_script(ISOLATION_PREAMBLE))
    private: list[PowerShellModule] = []
    for module in modules:
        try:
            module.path.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        private.append(module)
    return private


def list_installed_modules(runtime: PowerShellRuntime) -> list[PowerShellModule]:
    """Return user/machine modules visible natively to this executable.

    PowerShell's built-in modules and Diffasaurus's isolated modules are excluded,
    so this inventory describes reusable modules installed outside Diffasaurus.
    """
    private_root = runtime_modules_dir(runtime).resolve()
    builtin_root = runtime_builtin_modules_dir(runtime).resolve()
    installed: list[PowerShellModule] = []
    seen: set[tuple[str, str, str]] = set()
    for module in _module_inventory(runtime, _inventory_script()):
        try:
            resolved = module.path.resolve()
        except OSError:
            continue
        if resolved == private_root or private_root in resolved.parents:
            continue
        if resolved == builtin_root or builtin_root in resolved.parents:
            continue
        key = (module.name.casefold(), module.version, str(resolved))
        if key in seen:
            continue
        seen.add(key)
        installed.append(module)
    return installed


def copy_installed_modules(
    runtime: PowerShellRuntime,
    modules: list[PowerShellModule],
) -> int:
    """Copy selected native modules into this runtime's isolated environment."""
    private_root = runtime_modules_dir(runtime).resolve()
    copied = 0
    for module in modules:
        source = module.path.resolve()
        if not source.is_dir():
            continue
        try:
            source.relative_to(private_root)
        except ValueError:
            pass
        else:
            continue
        name = _safe_name(module.name)
        version = _safe_name(module.version or "unversioned")
        parent = private_root / name
        destination = parent / version
        if destination.exists():
            continue
        parent.mkdir(parents=True, exist_ok=True)
        temporary = parent / f".{version}.copying"
        if temporary.exists():
            shutil.rmtree(temporary)
        try:
            shutil.copytree(source, temporary)
            temporary.rename(destination)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        copied += 1
    return copied


def remove_private_module(
    runtime: PowerShellRuntime,
    module: PowerShellModule,
) -> None:
    root = runtime_modules_dir(runtime).resolve()
    try:
        relative = module.path.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("Only modules in this runtime environment can be removed.") from exc
    if len(relative.parts) < 1:
        raise ValueError("The runtime module root cannot be removed.")
    shutil.rmtree(module.path)
    parent = module.path.parent
    if parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
