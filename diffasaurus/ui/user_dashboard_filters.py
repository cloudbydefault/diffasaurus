from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import Qt

from diffasaurus.core.user_dashboards import (
    ColumnFilterSpec,
    SortSpec,
    UserDashboard,
    index_filters_to_named,
    named_filters_to_index,
    resolve_column_name,
    validate_dashboard_columns,
)
from diffasaurus.models.csv_model import CsvTableModel
from diffasaurus.models.proxies import CsvFilterProxy
from diffasaurus.ui.snapshot_export import visible_source_rows


def _resolve_column_index(name: str, headers: list[str] | tuple[str, ...]) -> int | None:
    for index, header in enumerate(headers):
        if header.strip().casefold() == str(name or "").strip().casefold():
            return index
    return None


def configure_proxy_for_dashboard(
    proxy: CsvFilterProxy,
    model: CsvTableModel,
    dashboard: UserDashboard,
    *,
    smart_search_columns: list[int] | None = None,
) -> tuple[bool, list[str]]:
    missing = validate_dashboard_columns(dashboard, model.headers)
    if missing:
        return False, missing
    proxy.clear_filters()
    proxy.set_search_mode(dashboard.search_mode)
    proxy.set_search_text(dashboard.search_text)
    if smart_search_columns is not None:
        proxy.set_smart_search_columns(smart_search_columns)
    for item in dashboard.filters:
        column = _resolve_column_index(item.column, model.headers)
        if column is None:
            continue
        proxy.set_column_allowed_values(
            column,
            set(item.allowed),
            item.allow_empty,
        )
    if dashboard.sort and dashboard.sort.column:
        column = _resolve_column_index(dashboard.sort.column, model.headers)
        if column is not None:
            order = (
                Qt.SortOrder.AscendingOrder
                if dashboard.sort.order == "asc"
                else Qt.SortOrder.DescendingOrder
            )
            proxy.sort(column, order)
    return True, []


def dashboard_source_rows(
    model: CsvTableModel,
    dashboard: UserDashboard,
    *,
    smart_search_columns: list[int] | None = None,
) -> tuple[list[list[str]], list[str]]:
    missing = validate_dashboard_columns(dashboard, model.headers)
    if missing:
        return [], missing
    proxy = CsvFilterProxy()
    proxy.setSourceModel(model)
    configure_proxy_for_dashboard(
        proxy,
        model,
        dashboard,
        smart_search_columns=smart_search_columns,
    )
    return visible_source_rows(proxy, model), []


def capture_current_view(
    *,
    family: str,
    headers: list[str] | tuple[str, ...],
    filters: dict[int, dict],
    search_mode: str,
    search_text: str,
    sort_column: int,
    sort_order: Qt.SortOrder,
    has_fixed_row_filter: bool,
) -> tuple[UserDashboard | None, str]:
    if has_fixed_row_filter:
        return None, (
            "The current table uses a temporary built-in dashboard filter that "
            "cannot be saved as a custom dashboard."
        )
    sort_spec = None
    if sort_column >= 0 and sort_column < len(headers):
        sort_spec = SortSpec(
            column=headers[sort_column],
            order="asc" if sort_order == Qt.SortOrder.AscendingOrder else "desc",
        )
    dashboard = UserDashboard(
        id="",
        name="",
        family=family,
        filters=index_filters_to_named(filters, headers),
        search_mode="all" if search_mode == "all" else "smart",
        search_text=search_text.strip(),
        sort=sort_spec,
    )
    if (
        not dashboard.filters
        and not dashboard.search_text
        and sort_spec is None
    ):
        return dashboard, (
            "No filters, search, or sort are active. This dashboard will show the "
            "complete report."
        )
    return dashboard, ""


def dashboard_export_filename(
    loaded_path: Path | None,
    dashboard_name: str,
    *,
    fallback: str = "dashboard_export.csv",
) -> str:
    if loaded_path is None:
        return fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", dashboard_name.strip()) or "dashboard"
    return f"{loaded_path.stem}_{safe}.csv"


def named_filters_for_editor(
    filters: list[ColumnFilterSpec],
    headers: list[str] | tuple[str, ...],
) -> dict[int, dict]:
    return named_filters_to_index(filters, headers)


def filters_from_editor_result(
    result_filters: dict[int, dict],
    headers: list[str] | tuple[str, ...],
) -> list[ColumnFilterSpec]:
    return index_filters_to_named(result_filters, headers)
