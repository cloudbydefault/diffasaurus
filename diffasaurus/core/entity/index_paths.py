from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from diffasaurus.core.paths import user_data_dir


def normalize_reports_path(reports_dir: Path) -> Path:
    path = reports_dir.expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.path.normcase(str(path)))
    return path


def source_key(reports_dir: Path) -> str:
    normalized = str(normalize_reports_path(reports_dir)).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:16]


def entity_index_dir() -> Path:
    override_root = os.environ.get("DIFFASAURUS_ENTITY_INDEX_ROOT")
    if override_root:
        path = Path(override_root).expanduser()
    else:
        path = user_data_dir() / "config" / "entity_index"
    path.mkdir(parents=True, exist_ok=True)
    return path


def entity_index_path(reports_dir: Path) -> Path:
    override = os.environ.get("DIFFASAURUS_ENTITY_INDEX_DB")
    if override:
        return Path(override).expanduser()
    return entity_index_dir() / f"{source_key(reports_dir)}.sqlite3"


def entity_index_temp_path(reports_dir: Path) -> Path:
    return entity_index_path(reports_dir).with_suffix(".sqlite3.tmp")


def cleanup_index_files(base_path: Path) -> None:
    for path in (base_path, Path(f"{base_path}-wal"), Path(f"{base_path}-shm")):
        if path.is_file():
            path.unlink()


def relative_report_path(reports_dir: Path, file_path: Path) -> str:
    root = normalize_reports_path(reports_dir)
    resolved = file_path.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.name


def publish_temp_index(reports_dir: Path) -> Path:
    destination = entity_index_path(reports_dir)
    temporary = entity_index_temp_path(reports_dir)
    if not temporary.is_file():
        raise FileNotFoundError(f"Temporary index not found: {temporary}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(destination)
    return destination
