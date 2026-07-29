from __future__ import annotations

from PyQt6.QtCore import QThreadPool, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.ui.background import BackgroundCall


def collect_distinct_values(model, column: int) -> list[str]:
    direct = getattr(model, "column_values", None)
    if callable(direct):
        values = direct(column)
    else:
        values = [
            str(model.data(model.index(row, column)) or "").strip()
            for row in range(model.rowCount())
        ]
    return sorted(set(values), key=lambda value: (value == "", value.casefold()))


class MultiColumnFilterDialog(QDialog):
    """Searchable, combined column filter with lazy background value scans."""

    def __init__(self, parent, model, current_filters: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Multi-column filter")
        self.resize(980, 680)
        self.model = model
        self.headers = [
            str(model.headerData(column, Qt.Orientation.Horizontal) or "").strip()
            for column in range(model.columnCount())
        ]
        self.working_filters = {
            int(column): {
                "allowed": set(data.get("allowed", set())),
                "allow_empty": bool(data.get("allow_empty", False)),
            }
            for column, data in (current_filters or {}).items()
        }
        self.result_filters: dict = {}
        self._value_cache: dict[int, list[str]] = {}
        self._tasks: set[BackgroundCall] = set()
        self._generation = 0
        self._active_column: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("Multi-column filter")
        title.setStyleSheet("font-size:22px; font-weight:850;")
        subtitle = QLabel(
            "Build filters across several columns. Search values, keep exactly "
            "what matters, then apply every condition at once."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#8295a8;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addWidget(QLabel("COLUMNS"))
        self.column_search = QLineEdit()
        self.column_search.setPlaceholderText("Search columns…")
        self.column_list = QListWidget()
        left_layout.addWidget(self.column_search)
        left_layout.addWidget(self.column_list, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        self.values_title = QLabel("VALUES")
        self.value_search = QLineEdit()
        self.value_search.setPlaceholderText("Search values…")
        right_layout.addWidget(self.values_title)
        right_layout.addWidget(self.value_search)
        checks = QHBoxLayout()
        self.check_visible = QPushButton("Check visible")
        self.uncheck_visible = QPushButton("Uncheck visible")
        checks.addWidget(self.check_visible)
        checks.addWidget(self.uncheck_visible)
        right_layout.addLayout(checks)
        self.value_list = QListWidget()
        right_layout.addWidget(self.value_list, 1)
        splitter.addWidget(right)
        splitter.setSizes((300, 680))

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        footer = QHBoxLayout()
        self.status = QLabel("Choose a column")
        self.status.setStyleSheet("color:#8295a8;")
        footer.addWidget(self.status)
        footer.addStretch()
        self.clear_button = QPushButton("Clear all")
        cancel = QPushButton("Cancel")
        self.apply_button = QPushButton("Apply filters")
        self.apply_button.setObjectName("primaryButton")
        footer.addWidget(self.clear_button)
        footer.addWidget(cancel)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)

        self.column_search.textChanged.connect(self._refresh_columns)
        self.value_search.textChanged.connect(self._refresh_values)
        self.column_list.currentItemChanged.connect(self._column_changed)
        self.check_visible.clicked.connect(lambda: self._set_visible(True))
        self.uncheck_visible.clicked.connect(lambda: self._set_visible(False))
        self.clear_button.clicked.connect(self._clear)
        cancel.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self._apply)
        self._refresh_columns()
        if self.column_list.count():
            self.column_list.setCurrentRow(0)

    def current_column(self) -> int | None:
        item = self.column_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return int(value) if value is not None else None

    def _refresh_columns(self):
        selected = self.current_column()
        needle = self.column_search.text().strip().casefold()
        self.column_list.blockSignals(True)
        self.column_list.clear()
        for column, header in enumerate(self.headers):
            if needle and needle not in header.casefold():
                continue
            suffix = "  • filtered" if column in self.working_filters else ""
            item = QListWidgetItem(header + suffix)
            item.setData(Qt.ItemDataRole.UserRole, column)
            self.column_list.addItem(item)
        self.column_list.blockSignals(False)
        for row in range(self.column_list.count()):
            if self.column_list.item(row).data(Qt.ItemDataRole.UserRole) == selected:
                self.column_list.setCurrentRow(row)
                break
        else:
            if self.column_list.count():
                self.column_list.setCurrentRow(0)

    def _column_changed(self, _current=None, _previous=None):
        if self._active_column is not None:
            self._save_visible_state(self._active_column)
        self._active_column = self.current_column()
        self.value_search.clear()
        self._load_values()

    def _load_values(self):
        column = self.current_column()
        self.value_list.clear()
        if column is None:
            return
        self.values_title.setText(f"VALUES · {self.headers[column]}")
        if column in self._value_cache:
            self._refresh_values()
            return
        self._generation += 1
        generation = self._generation
        self.progress.show()
        self.apply_button.setEnabled(False)
        self.status.setText(f"Scanning {self.headers[column]} in the background…")
        task = BackgroundCall(collect_distinct_values, self.model, column)
        self._tasks.add(task)
        task.signals.succeeded.connect(
            lambda values: self._values_loaded(generation, column, values)
        )
        task.signals.failed.connect(self._values_failed)
        task.signals.done.connect(lambda: self._tasks.discard(task))
        QThreadPool.globalInstance().start(task)

    def _values_loaded(self, generation: int, column: int, values):
        self._value_cache[column] = list(values)
        if generation != self._generation or column != self.current_column():
            return
        self.progress.hide()
        self.apply_button.setEnabled(True)
        self._refresh_values()

    def _values_failed(self, message: str):
        self.progress.hide()
        self.apply_button.setEnabled(True)
        self.status.setText(message)

    def _selected_values(self, column: int, all_values: set[str]) -> set[str]:
        data = self.working_filters.get(column)
        if not data:
            return set(all_values)
        selected = set(data.get("allowed", set()))
        if data.get("allow_empty"):
            selected.add("")
        return selected

    def _refresh_values(self):
        column = self.current_column()
        if column is None or column not in self._value_cache:
            return
        values = self._value_cache[column]
        selected = self._selected_values(column, set(values))
        needle = self.value_search.text().strip().casefold()
        self.value_list.clear()
        visible = 0
        for value in values:
            label = "(Blank)" if not value else value
            if needle and needle not in label.casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if value in selected
                else Qt.CheckState.Unchecked
            )
            self.value_list.addItem(item)
            visible += 1
        self.status.setText(
            f"{len(values):,} distinct value{'s' if len(values) != 1 else ''} · "
            f"{visible:,} visible"
        )

    def _save_visible_state(self, column: int):
        if column not in self._value_cache:
            return
        all_values = set(self._value_cache[column])
        selected = self._selected_values(column, all_values)
        for row in range(self.value_list.count()):
            item = self.value_list.item(row)
            value = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if item.checkState() == Qt.CheckState.Checked:
                selected.add(value)
            else:
                selected.discard(value)
        if selected == all_values:
            self.working_filters.pop(column, None)
        else:
            self.working_filters[column] = {
                "allowed": selected - {""},
                "allow_empty": "" in selected,
            }

    def _set_visible(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.value_list.count()):
            self.value_list.item(row).setCheckState(state)
        column = self.current_column()
        if column is not None:
            self._save_visible_state(column)
            self._refresh_columns()

    def _clear(self):
        self.working_filters.clear()
        self._refresh_columns()
        self._refresh_values()
        self.status.setText("All column filters cleared")

    def _apply(self):
        column = self.current_column()
        if column is not None:
            self._save_visible_state(column)
        self.result_filters = self.working_filters
        self.accept()
