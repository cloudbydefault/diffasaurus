from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from diffasaurus.core.paths import user_data_dir

SCHEMA_VERSION = 1
SESSION_FILENAME = "session_state.json"
REASON_PRE_UPDATE = "pre_update"


@dataclass
class SessionState:
    page: int
    report_family: str = ""
    snapshot_path: str = ""
    explorer_view: str = "table"
    reason: str = REASON_PRE_UPDATE
    schema_version: int = SCHEMA_VERSION
    saved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "saved_at": self.saved_at,
            "reason": self.reason,
            "page": self.page,
            "report_family": self.report_family,
            "snapshot_path": self.snapshot_path,
            "explorer_view": self.explorer_view,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState | None:
        if data.get("schema_version") != SCHEMA_VERSION:
            return None
        if data.get("reason") != REASON_PRE_UPDATE:
            return None
        page = data.get("page")
        if not isinstance(page, int):
            return None
        explorer_view = str(data.get("explorer_view") or "table")
        if explorer_view not in {"table", "dashboard"}:
            explorer_view = "table"
        return cls(
            page=page,
            report_family=str(data.get("report_family") or ""),
            snapshot_path=str(data.get("snapshot_path") or ""),
            explorer_view=explorer_view,
            reason=REASON_PRE_UPDATE,
            schema_version=SCHEMA_VERSION,
            saved_at=str(data.get("saved_at") or ""),
        )


def session_state_path(base_dir: Path | None = None) -> Path:
    root = base_dir or user_data_dir()
    return root / "config" / SESSION_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def save_pre_update_session(
    *,
    page: int,
    report_family: str = "",
    snapshot_path: str = "",
    explorer_view: str = "table",
    base_dir: Path | None = None,
) -> None:
    state = SessionState(
        page=page,
        report_family=report_family.strip(),
        snapshot_path=snapshot_path.strip(),
        explorer_view=explorer_view if explorer_view in {"table", "dashboard"} else "table",
        saved_at=_utc_now(),
    )
    path = session_state_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = state.to_dict()
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def load_session_state(base_dir: Path | None = None) -> SessionState | None:
    path = session_state_path(base_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return SessionState.from_dict(payload)


def consume_pre_update_session(base_dir: Path | None = None) -> SessionState | None:
    state = load_session_state(base_dir)
    if state is None:
        return None
    path = session_state_path(base_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return None
    return state


def clear_session_state(base_dir: Path | None = None) -> None:
    session_state_path(base_dir).unlink(missing_ok=True)
