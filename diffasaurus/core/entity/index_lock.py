from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class EntityIndexLockError(Exception):
    """Raised when an entity-index writer lock cannot be acquired."""


@dataclass(frozen=True)
class EntityIndexLockInfo:
    pid: int
    source_key: str
    db_path: str
    started_at: str
    cold: bool = False

    def is_holder_alive(self) -> bool:
        return _process_is_alive(self.pid)


def lock_path_for_db(db_path: Path) -> Path:
    return db_path.with_suffix(db_path.suffix + ".lock")


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def read_lock_info(lock_path: Path) -> EntityIndexLockInfo | None:
    if not lock_path.is_file():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        return EntityIndexLockInfo(
            pid=int(payload["pid"]),
            source_key=str(payload["source_key"]),
            db_path=str(payload["db_path"]),
            started_at=str(payload.get("started_at", "")),
            cold=bool(payload.get("cold", False)),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def check_lock_holder(db_path: Path) -> EntityIndexLockInfo | None:
    info = read_lock_info(lock_path_for_db(db_path))
    if info is None:
        return None
    if info.is_holder_alive():
        return info
    return None


def lock_unavailable_message(db_path: Path, info: EntityIndexLockInfo) -> str:
    return (
        "Entity index synchronization is already running for this report source "
        f"(pid {info.pid}, started {info.started_at or 'unknown'})."
    )


class EntityIndexLock:
    def __init__(self, lock_path: Path, file_handle) -> None:
        self._lock_path = lock_path
        self._file_handle = file_handle
        self._released = False

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._file_handle.close()
        except OSError:
            pass
        try:
            if self._lock_path.is_file():
                self._lock_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> EntityIndexLock:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def acquire_entity_index_lock(
    db_path: Path,
    source_key: str,
    *,
    cold: bool = False,
) -> EntityIndexLock:
    lock_path = lock_path_for_db(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = EntityIndexLockInfo(
        pid=os.getpid(),
        source_key=source_key,
        db_path=str(db_path),
        started_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(
            timespec="seconds"
        ),
        cold=cold,
    )
    encoded = json.dumps(asdict(payload), sort_keys=True)

    for attempt in range(2):
        try:
            handle = open(lock_path, "x", encoding="utf-8")
        except FileExistsError:
            existing = read_lock_info(lock_path)
            if existing is not None and existing.is_holder_alive():
                raise EntityIndexLockError(lock_unavailable_message(db_path, existing))
            try:
                lock_path.unlink()
            except OSError as exc:
                raise EntityIndexLockError(
                    f"Unable to recover stale entity index lock at {lock_path}: {exc}"
                ) from exc
            if attempt == 0:
                time.sleep(0.05)
                continue
            raise EntityIndexLockError(
                f"Unable to acquire entity index lock at {lock_path}"
            )
        else:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            return EntityIndexLock(lock_path, handle)

    raise EntityIndexLockError(f"Unable to acquire entity index lock at {lock_path}")
