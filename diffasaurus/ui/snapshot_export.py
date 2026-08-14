from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtWidgets import QTableView

from diffasaurus.models.csv_model import CsvTableModel
from diffasaurus.models.proxies import CsvFilterProxy


def proxy_row_source_values(
    proxy: CsvFilterProxy,
    model: CsvTableModel,
    proxy_row: int,
) -> list[str]:
    source_index = proxy.mapToSource(proxy.index(proxy_row, 0))
    return list(model.row_values(source_index.row()))


def visible_source_rows(
    proxy: CsvFilterProxy,
    model: CsvTableModel,
) -> list[list[str]]:
    return [
        proxy_row_source_values(proxy, model, proxy_row)
        for proxy_row in range(proxy.rowCount())
    ]


def selected_source_rows(
    table: QTableView,
    proxy: CsvFilterProxy,
    model: CsvTableModel,
) -> list[list[str]]:
    selection = table.selectionModel()
    if selection is None:
        return []
    proxy_rows = sorted({index.row() for index in selection.selectedRows()})
    return [proxy_row_source_values(proxy, model, proxy_row) for proxy_row in proxy_rows]


def default_export_filename(
    loaded_path: Path | None,
    *,
    suffix: str,
    fallback: str,
) -> str:
    if loaded_path is None:
        return fallback
    return f"{loaded_path.stem}_{suffix}.csv"


def write_csv_export(
    path: Path,
    headers: list[str] | tuple[str, ...],
    rows: list[list[str]],
    *,
    delimiter: str = ",",
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(list(headers))
        writer.writerows(rows)
