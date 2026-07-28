from __future__ import annotations

import shutil
import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def reports_dir() -> Path:
    path = project_root() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scripts_dir() -> Path:
    return project_root() / "psscripts"


def modules_dir() -> Path:
    path = project_root() / "psmodules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_report_scripts() -> list[Path]:
    return sorted(scripts_dir().glob("*.ps1"), key=lambda path: path.name.lower())


def powershell_executable() -> Path | None:
    bundled = project_root() / "pwsh"
    names = ("pwsh.exe",) if sys.platform == "win32" else ("pwsh",)
    for name in names:
        direct = bundled / name
        if direct.is_file():
            return direct
        matches = sorted(bundled.rglob(name), key=lambda path: len(path.parts), reverse=True)
        if matches:
            return matches[0]
    system_pwsh = shutil.which("pwsh")
    return Path(system_pwsh) if system_pwsh else None
