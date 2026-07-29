from __future__ import annotations

import json
from pathlib import Path

from diffasaurus.core.paths import reports_dir, user_data_dir


DEFAULT_SETTINGS = {
    "report_source": "local",
    "external_reports_path": "",
}


def settings_path() -> Path:
    path = user_data_dir() / "config" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        save_settings(DEFAULT_SETTINGS.copy())
        return DEFAULT_SETTINGS.copy()
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        values = {}
    result = DEFAULT_SETTINGS.copy()
    result.update(values)
    return result


def save_settings(settings: dict):
    settings_path().write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_active_reports_dir() -> Path:
    settings = load_settings()
    if settings.get("report_source") == "external":
        external = Path(settings.get("external_reports_path", "")).expanduser()
        if external.is_dir():
            return external
    return reports_dir()
