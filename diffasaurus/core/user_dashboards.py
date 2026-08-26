from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from diffasaurus.core.paths import user_data_dir

SCHEMA_VERSION = 1
STORE_FILENAME = "user_dashboards.json"


@dataclass
class ColumnFilterSpec:
    column: str
    allowed: list[str] = field(default_factory=list)
    allow_empty: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "allowed": sorted(self.allowed),
            "allow_empty": self.allow_empty,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnFilterSpec:
        return cls(
            column=str(data.get("column", "")),
            allowed=[str(value) for value in data.get("allowed", [])],
            allow_empty=bool(data.get("allow_empty", False)),
        )


@dataclass
class SortSpec:
    column: str | None = None
    order: str = "asc"

    def to_dict(self) -> dict[str, Any]:
        return {"column": self.column, "order": self.order}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SortSpec | None:
        if not data:
            return None
        column = data.get("column")
        order = str(data.get("order", "asc") or "asc").casefold()
        if order not in {"asc", "desc"}:
            order = "asc"
        return cls(column=str(column) if column else None, order=order)


@dataclass
class UserDashboard:
    id: str
    name: str
    family: str
    description: str = ""
    filters: list[ColumnFilterSpec] = field(default_factory=list)
    search_mode: str = "smart"
    search_text: str = ""
    sort: SortSpec | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "family": self.family,
            "filters": [item.to_dict() for item in self.filters],
            "search_mode": self.search_mode,
            "search_text": self.search_text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.sort is not None:
            payload["sort"] = self.sort.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserDashboard:
        sort = SortSpec.from_dict(data.get("sort"))
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            family=str(data.get("family", "")),
            filters=[
                ColumnFilterSpec.from_dict(item)
                for item in data.get("filters", [])
                if isinstance(item, dict)
            ],
            search_mode="all" if data.get("search_mode") == "all" else "smart",
            search_text=str(data.get("search_text", "")),
            sort=sort,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def user_dashboards_path(base_dir: Path | None = None) -> Path:
    root = base_dir or user_data_dir()
    path = root / "config" / STORE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_headers(headers: list[str] | tuple[str, ...]) -> dict[str, str]:
    return {header.strip().casefold(): header for header in headers}


def resolve_column_name(name: str, headers: list[str] | tuple[str, ...]) -> str | None:
    wanted = str(name or "").strip().casefold()
    for header in headers:
        if header.strip().casefold() == wanted:
            return header
    return None


def validate_dashboard_columns(
    dashboard: UserDashboard,
    headers: list[str] | tuple[str, ...],
) -> list[str]:
    missing: list[str] = []
    seen: set[str] = set()
    for item in dashboard.filters:
        if not item.column:
            continue
        if resolve_column_name(item.column, headers) is None:
            key = item.column.strip()
            if key and key not in seen:
                missing.append(key)
                seen.add(key)
    if dashboard.sort and dashboard.sort.column:
        if resolve_column_name(dashboard.sort.column, headers) is None:
            key = dashboard.sort.column.strip()
            if key and key not in seen:
                missing.append(key)
    return missing


def filter_summary_text(dashboard: UserDashboard) -> str:
    parts: list[str] = []
    if dashboard.filters:
        parts.append(
            f"{len(dashboard.filters)} column filter{'s' if len(dashboard.filters) != 1 else ''}"
        )
    if dashboard.search_text.strip():
        parts.append(f'search: "{dashboard.search_text.strip()}"')
    if dashboard.sort and dashboard.sort.column:
        arrow = "↑" if dashboard.sort.order == "asc" else "↓"
        parts.append(f"sort: {dashboard.sort.column} {arrow}")
    return " · ".join(parts) if parts else "No filters (full report)"


class UserDashboardStore:
    def __init__(self, path: Path | None = None):
        self.path = path or user_dashboards_path()
        self._dashboards: list[UserDashboard] = []
        self.last_load_warning = ""
        self.load()

    def load(self) -> None:
        self.last_load_warning = ""
        self._dashboards = []
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            corrupt = self.path.with_suffix(
                self.path.suffix + f".corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            )
            try:
                self.path.replace(corrupt)
            except OSError:
                pass
            self.last_load_warning = (
                "Custom dashboard definitions could not be read and were reset. "
                f"A backup was preserved as {corrupt.name}."
            )
            return
        if not isinstance(payload, dict):
            self.last_load_warning = "Custom dashboard definitions were invalid and were ignored."
            return
        if payload.get("schema_version") != SCHEMA_VERSION:
            self.last_load_warning = "Custom dashboard definitions used an unsupported schema version."
            return
        items = payload.get("dashboards", [])
        if not isinstance(items, list):
            self.last_load_warning = "Custom dashboard definitions were invalid and were ignored."
            return
        dashboards: list[UserDashboard] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            dashboard = UserDashboard.from_dict(item)
            if dashboard.id and dashboard.name and dashboard.family:
                dashboards.append(dashboard)
        self._dashboards = dashboards

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "dashboards": [dashboard.to_dict() for dashboard in self._dashboards],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(self.path)

    def all(self) -> list[UserDashboard]:
        return list(self._dashboards)

    def for_family(self, family: str) -> list[UserDashboard]:
        return [dashboard for dashboard in self._dashboards if dashboard.family == family]

    def get(self, dashboard_id: str) -> UserDashboard | None:
        for dashboard in self._dashboards:
            if dashboard.id == dashboard_id:
                return dashboard
        return None

    def create(self, dashboard: UserDashboard) -> UserDashboard:
        now = _utc_now()
        if not dashboard.id:
            dashboard.id = str(uuid.uuid4())
        if not dashboard.created_at:
            dashboard.created_at = now
        dashboard.updated_at = now
        self._dashboards.append(dashboard)
        self.save()
        return dashboard

    def update(self, dashboard: UserDashboard) -> UserDashboard:
        for index, existing in enumerate(self._dashboards):
            if existing.id == dashboard.id:
                dashboard.updated_at = _utc_now()
                if not dashboard.created_at:
                    dashboard.created_at = existing.created_at
                self._dashboards[index] = dashboard
                self.save()
                return dashboard
        raise KeyError(dashboard.id)

    def delete(self, dashboard_id: str) -> None:
        self._dashboards = [
            dashboard for dashboard in self._dashboards if dashboard.id != dashboard_id
        ]
        self.save()

    def duplicate(self, dashboard_id: str) -> UserDashboard:
        original = self.get(dashboard_id)
        if original is None:
            raise KeyError(dashboard_id)
        now = _utc_now()
        copy = UserDashboard(
            id=str(uuid.uuid4()),
            name=f"{original.name} copy",
            description=original.description,
            family=original.family,
            filters=[
                ColumnFilterSpec(
                    column=item.column,
                    allowed=list(item.allowed),
                    allow_empty=item.allow_empty,
                )
                for item in original.filters
            ],
            search_mode=original.search_mode,
            search_text=original.search_text,
            sort=(
                SortSpec(column=original.sort.column, order=original.sort.order)
                if original.sort
                else None
            ),
            created_at=now,
            updated_at=now,
        )
        self._dashboards.append(copy)
        self.save()
        return copy

    def reset(self) -> None:
        self._dashboards = []
        self.save()

    def export_definitions(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "dashboards": [dashboard.to_dict() for dashboard in self._dashboards],
        }

    def import_definitions(
        self,
        payload: dict[str, Any],
        *,
        regenerate_ids: bool = True,
    ) -> list[UserDashboard]:
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported dashboard definition schema.")
        items = payload.get("dashboards")
        if not isinstance(items, list):
            raise ValueError("Dashboard definitions must contain a dashboards list.")
        imported: list[UserDashboard] = []
        existing_ids = {dashboard.id for dashboard in self._dashboards}
        for item in items:
            if not isinstance(item, dict):
                continue
            dashboard = UserDashboard.from_dict(item)
            if not dashboard.name or not dashboard.family:
                continue
            if regenerate_ids or dashboard.id in existing_ids or not dashboard.id:
                dashboard.id = str(uuid.uuid4())
            now = _utc_now()
            dashboard.created_at = dashboard.created_at or now
            dashboard.updated_at = now
            self._dashboards.append(dashboard)
            existing_ids.add(dashboard.id)
            imported.append(dashboard)
        if imported:
            self.save()
        return imported


def index_filters_to_named(
    filters: dict[int, dict],
    headers: list[str] | tuple[str, ...],
) -> list[ColumnFilterSpec]:
    named: list[ColumnFilterSpec] = []
    for column_index, data in sorted(filters.items(), key=lambda item: int(item[0])):
        if not 0 <= int(column_index) < len(headers):
            continue
        named.append(
            ColumnFilterSpec(
                column=headers[int(column_index)],
                allowed=sorted(str(value) for value in data.get("allowed", set())),
                allow_empty=bool(data.get("allow_empty", False)),
            )
        )
    return named


def named_filters_to_index(
    filters: list[ColumnFilterSpec],
    headers: list[str] | tuple[str, ...],
) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for item in filters:
        for index, header in enumerate(headers):
            if header.strip().casefold() == item.column.strip().casefold():
                result[index] = {
                    "allowed": set(item.allowed),
                    "allow_empty": item.allow_empty,
                }
                break
    return result
