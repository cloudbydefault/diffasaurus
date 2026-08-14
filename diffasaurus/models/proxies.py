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
        self._bulk_update_depth = 0
        self._restore_dynamic_sort = True
        self._sort_key_cache: list[tuple[int, float | str]] | None = None
        self._sort_key_column = -1
        self.setDynamicSortFilter(True)

    def setSourceModel(self, model):
        current = self.sourceModel()
        if current is not None:
            current.modelAboutToBeReset.disconnect(self._clear_sort_cache)
            current.modelReset.disconnect(self._clear_sort_cache)
        super().setSourceModel(model)
        self._clear_sort_cache()
        if model is not None:
            model.modelAboutToBeReset.connect(self._clear_sort_cache)
            model.modelReset.connect(self._clear_sort_cache)

    def begin_bulk_update(self) -> None:
        if self._bulk_update_depth == 0:
            self._restore_dynamic_sort = self.dynamicSortFilter()
            self.setDynamicSortFilter(False)
        self._bulk_update_depth += 1

    def end_bulk_update(self) -> None:
        if self._bulk_update_depth <= 0:
            return
        self._bulk_update_depth -= 1
        if self._bulk_update_depth == 0:
            self.setDynamicSortFilter(self._restore_dynamic_sort)
            self.invalidateFilter()

    def invalidateFilter(self):
        if self._bulk_update_depth:
            return
        super().invalidateFilter()

    def _clear_sort_cache(self, *_args) -> None:
        self._sort_key_cache = None
        self._sort_key_column = -1

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

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._build_sort_key_cache(column)
        super().sort(column, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        column = left.column()
        if column != self._sort_key_column or self._sort_key_cache is None:
            self._build_sort_key_cache(column)
        cache = self._sort_key_cache
        assert cache is not None
        return cache[left.row()] < cache[right.row()]

    @staticmethod
    def _sort_key(value: str) -> tuple[int, float | str]:
        cleaned = value.replace(",", "")
        if cleaned:
            body = cleaned[1:] if cleaned.startswith("-") else cleaned
            if body and body.replace(".", "", 1).isdigit() and body.count(".") <= 1:
                try:
                    return (0, float(cleaned))
                except ValueError:
                    pass
        return (1, value.casefold())

    def _build_sort_key_cache(self, column: int) -> None:
        model = self.sourceModel()
        if model is None:
            self._sort_key_cache = []
            self._sort_key_column = column
            return
        row_values = getattr(model, "row_values", None)
        keys: list[tuple[int, float | str]] = []
        value_keys: dict[str, tuple[int, float | str]] = {}
        for row in range(model.rowCount()):
            if callable(row_values):
                values = row_values(row)
                cell = str(values[column] if column < len(values) else "").strip()
            else:
                cell = str(model.data(model.index(row, column)) or "").strip()
            key = value_keys.get(cell)
            if key is None:
                key = self._sort_key(cell)
                value_keys[cell] = key
            keys.append(key)
        self._sort_key_cache = keys
        self._sort_key_column = column
