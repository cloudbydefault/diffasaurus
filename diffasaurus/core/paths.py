from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return Path(sys.executable).resolve().parents[1] / "Resources"
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def user_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            path = Path.home() / "Library" / "Application Support" / "Diffasaurus"
        elif sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA")
            base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
            path = base / "Diffasaurus"
        else:
            xdg_data = os.environ.get("XDG_DATA_HOME")
            base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
            path = base / "Diffasaurus"
    else:
        path = project_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    path = user_data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scripts_dir() -> Path:
    return project_root() / "psscripts"


def modules_dir() -> Path:
    path = user_data_dir() / "psmodules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def powershell_runtimes_dir() -> Path:
    path = user_data_dir() / "pwsh"
    path.mkdir(parents=True, exist_ok=True)
    return path


def powershell_environments_dir() -> Path:
    path = user_data_dir() / "powershell-environments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_report_scripts() -> list[Path]:
    return sorted(scripts_dir().glob("*.ps1"), key=lambda path: path.name.lower())


def powershell_executable() -> Path | None:
    from diffasaurus.core.powershell_runtime import selected_powershell_runtime

    runtime = selected_powershell_runtime()
    return runtime.path if runtime else None
