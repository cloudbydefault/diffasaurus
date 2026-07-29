from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel, Qt


class CsvFilterProxy(QSortFilterProxyModel):
    """Fast global search plus combined Excel-style column filters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tokens: list[str] = []
        self._search_mode = "smart"
        self._smart_columns: list[int] | None = None
        self._column_filters: dict[int, tuple[set[str], bool]] = {}
        self._fixed_rows: set[int] | None = None
        self.setDynamicSortFilter(True)

    def set_search_text(self, text: str):
        self._tokens = [part.casefold() for part in str(text or "").split() if part]
        self.invalidateFilter()

    def set_search_mode(self, mode: str):
        self._search_mode = "all" if mode == "all" else "smart"
        self.invalidateFilter()

    def set_smart_search_columns(self, columns: list[int] | None):
        self._smart_columns = list(columns) if columns else None
        self.invalidateFilter()

    def set_column_allowed_values(
        self,
        column: int,
        allowed: set[str] | None,
        allow_empty: bool = False,
    ):
        if allowed is None:
            self._column_filters.pop(column, None)
        else:
            self._column_filters[column] = (
                {str(value).strip() for value in allowed},
                bool(allow_empty),
            )
        self.invalidateFilter()

    def set_fixed_rows(self, rows: set[int] | None):
        self._fixed_rows = set(rows) if rows is not None else None
        self.invalidateFilter()

    def clear_filters(self):
        self._column_filters.clear()
        self._fixed_rows = None
        self.invalidateFilter()

    def active_filter_count(self) -> int:
        return len(self._column_filters) + int(self._fixed_rows is not None)

    def filterAcceptsRow(self, source_row: int, parent: QModelIndex) -> bool:
        if self._fixed_rows is not None and source_row not in self._fixed_rows:
            return False
        model = self.sourceModel()
        if model is None:
            return False
        direct_row = getattr(model, "row_values", None)
        row = direct_row(source_row) if callable(direct_row) else None

        if self._tokens:
            if self._search_mode == "smart" and self._smart_columns:
                columns = self._smart_columns
            else:
                columns = range(model.columnCount())
            values = []
            for column in columns:
                if not 0 <= column < model.columnCount():
                    continue
                if row is not None:
                    value = row[column] if column < len(row) else ""
                else:
                    value = model.data(model.index(source_row, column, parent))
                values.append(str(value or "").casefold())
            haystack = " | ".join(values)
            if not all(token in haystack for token in self._tokens):
                return False

        for column, (allowed, allow_empty) in self._column_filters.items():
            if not 0 <= column < model.columnCount():
                continue
            if row is not None:
                value = row[column] if column < len(row) else ""
            else:
                value = model.data(model.index(source_row, column, parent))
            normalized = str(value or "").strip()
            if not normalized:
                if not allow_empty:
                    return False
            elif normalized not in allowed:
                return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        left_value = str(self.sourceModel().data(left) or "").strip()
        right_value = str(self.sourceModel().data(right) or "").strip()
        try:
            return float(left_value.replace(",", "")) < float(
                right_value.replace(",", "")
            )
        except ValueError:
            return left_value.casefold() < right_value.casefold()
