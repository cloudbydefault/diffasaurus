from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt


def read_csv_table(path: Path) -> tuple[list[str], list[list[str]], str]:
    """Read a CSV using the same tolerant rules as the history engine."""
    if not path.is_file():
        raise FileNotFoundError(str(path))
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                sample = handle.read(8192).replace("\x00", "")
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
                except csv.Error:
                    delimiter = max((";", ",", "\t"), key=sample.count)
                    dialect = csv.excel
                    dialect.delimiter = delimiter
                rows = list(csv.reader(handle, dialect))
            if not rows:
                return [], [], getattr(dialect, "delimiter", ",")
            width = len(rows[0])
            normalized = [
                [str(value or "") for value in (row + [""] * width)[:width]]
                for row in rows[1:]
            ]
            return (
                [str(value or "").strip() for value in rows[0]],
                normalized,
                getattr(dialect, "delimiter", ","),
            )
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Unable to read {path.name}: {last_error}")


class CsvTableModel(QAbstractTableModel):
    def __init__(self, headers=None, rows=None):
        super().__init__()
        self._headers = list(headers or [])
        self._rows = list(rows or [])
        self._delimiter = ","

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    @property
    def headers(self) -> tuple[str, ...]:
        return tuple(self._headers)

    def row_values(self, row: int) -> list[str]:
        return self._rows[row] if 0 <= row < len(self._rows) else []

    def column_values(self, column: int) -> list[str]:
        if not 0 <= column < len(self._headers):
            return []
        return [
            str(row[column] if column < len(row) else "").strip()
            for row in self._rows
        ]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            row = self.row_values(index.row())
            return row[index.column()] if index.column() < len(row) else ""
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._headers[section] if 0 <= section < len(self._headers) else ""
        return str(section + 1)

    def set_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        delimiter: str = ",",
    ):
        self.beginResetModel()
        self._headers = list(headers)
        self._rows = list(rows)
        self._delimiter = delimiter
        self.endResetModel()

    def load_csv(self, csv_path: Path):
        headers, rows, delimiter = read_csv_table(Path(csv_path))
        self.set_table(headers, rows, delimiter)
