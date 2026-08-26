from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.user_dashboards import (
    ColumnFilterSpec,
    SortSpec,
    UserDashboard,
    validate_dashboard_columns,
)
from diffasaurus.models.csv_model import CsvTableModel
from diffasaurus.ui.multi_column_filter import MultiColumnFilterDialog
from diffasaurus.ui.user_dashboard_filters import (
    filters_from_editor_result,
    named_filters_for_editor,
)


class UserDashboardEditorDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        model: CsvTableModel,
        family: str,
        dashboard: UserDashboard | None = None,
        title: str = "Custom dashboard",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 420)
        self._model = model
        self._family = family
        self._filters: list[ColumnFilterSpec] = list(dashboard.filters) if dashboard else []
        self.dashboard: UserDashboard | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(dashboard.name if dashboard else "")
        self.description_edit = QTextEdit(dashboard.description if dashboard else "")
        self.description_edit.setFixedHeight(70)
        form.addRow("Name *", self.name_edit)
        form.addRow("Description", self.description_edit)
        layout.addLayout(form)

        filter_row = QHBoxLayout()
        self.filter_summary = QLabel(self._filter_summary())
        self.filter_summary.setWordWrap(True)
        self.filter_summary.setStyleSheet("color:#8295a8;")
        configure_button = QPushButton("Configure filters…")
        configure_button.clicked.connect(self._configure_filters)
        filter_row.addWidget(self.filter_summary, 1)
        filter_row.addWidget(configure_button)
        layout.addLayout(filter_row)

        search_form = QFormLayout()
        self.search_mode = QComboBox()
        self.search_mode.addItem("Smart search", "smart")
        self.search_mode.addItem("All columns", "all")
        if dashboard and dashboard.search_mode == "all":
            self.search_mode.setCurrentIndex(1)
        self.search_text = QLineEdit(dashboard.search_text if dashboard else "")
        search_form.addRow("Search mode", self.search_mode)
        search_form.addRow("Search text", self.search_text)
        layout.addLayout(search_form)

        sort_row = QWidget()
        sort_layout = QHBoxLayout(sort_row)
        sort_layout.setContentsMargins(0, 0, 0, 0)
        self.sort_column = QComboBox()
        self.sort_column.addItem("None", "")
        for header in model.headers:
            self.sort_column.addItem(header, header)
        if dashboard and dashboard.sort and dashboard.sort.column:
            index = self.sort_column.findData(dashboard.sort.column)
            if index >= 0:
                self.sort_column.setCurrentIndex(index)
        self.sort_order = QComboBox()
        self.sort_order.addItem("Ascending", "asc")
        self.sort_order.addItem("Descending", "desc")
        if dashboard and dashboard.sort and dashboard.sort.order == "desc":
            self.sort_order.setCurrentIndex(1)
        sort_layout.addWidget(QLabel("Sort column"))
        sort_layout.addWidget(self.sort_column, 1)
        sort_layout.addWidget(QLabel("Order"))
        sort_layout.addWidget(self.sort_order)
        layout.addWidget(sort_row)

        self.compatibility_label = QLabel("")
        self.compatibility_label.setWordWrap(True)
        self.compatibility_label.setStyleSheet("color:#f5b942;")
        layout.addWidget(self.compatibility_label)
        self._refresh_compatibility()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _filter_summary(self) -> str:
        if not self._filters:
            return "No column filters configured."
        parts = []
        for item in self._filters[:3]:
            parts.append(f"{item.column} ({len(item.allowed)} values)")
        suffix = f" · +{len(self._filters) - 3} more" if len(self._filters) > 3 else ""
        return "Filters: " + ", ".join(parts) + suffix

    def _refresh_compatibility(self) -> None:
        draft = self._build_dashboard("")
        missing = validate_dashboard_columns(draft, self._model.headers)
        if missing:
            self.compatibility_label.setText(
                "Needs attention · Missing: " + ", ".join(missing)
            )
        else:
            self.compatibility_label.setText("Compatible with the current snapshot schema.")

    def _configure_filters(self) -> None:
        dialog = MultiColumnFilterDialog(
            self,
            self._model,
            current_filters=named_filters_for_editor(self._filters, self._model.headers),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._filters = filters_from_editor_result(
            dialog.result_filters,
            self._model.headers,
        )
        self.filter_summary.setText(self._filter_summary())
        self._refresh_compatibility()

    def _build_dashboard(self, name: str) -> UserDashboard:
        sort_column = str(self.sort_column.currentData() or "")
        sort = SortSpec(column=sort_column, order=str(self.sort_order.currentData() or "asc"))
        if not sort.column:
            sort = None
        return UserDashboard(
            id="",
            name=name,
            description=self.description_edit.toPlainText().strip(),
            family=self._family,
            filters=list(self._filters),
            search_mode=str(self.search_mode.currentData() or "smart"),
            search_text=self.search_text.text().strip(),
            sort=sort,
        )

    def _accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Custom dashboard", "Name is required.")
            return
        dashboard = self._build_dashboard(name)
        self.dashboard = dashboard
        self.accept()
