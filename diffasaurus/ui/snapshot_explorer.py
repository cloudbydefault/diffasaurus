from __future__ import annotations

import json

from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.dashboard_registry import get_dashboard_definition
from diffasaurus.core.configuration_policies.constants import CONFIGURATION_POLICY_FAMILY
from diffasaurus.core.configuration_policies.integration import (
    POLICY_SESSION_CACHE,
    compact_policy_inventory_rows,
    is_configuration_policy_family,
    resolve_bundle_for_anchor,
)
from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.core.user_dashboards import (
    UserDashboard,
    UserDashboardStore,
    named_filters_to_index,
    validate_dashboard_columns,
)
from diffasaurus.models.csv_model import CsvTableModel, read_csv_table
from diffasaurus.models.proxies import CsvFilterProxy
from diffasaurus.ui.background import BackgroundCall
from diffasaurus.ui.configuration_policy_presentation import count_semantic_settings
from diffasaurus.ui.multi_column_filter import MultiColumnFilterDialog
from diffasaurus.ui.snapshot_export import (
    default_export_filename,
    selected_source_rows,
    visible_source_rows,
    write_csv_export,
)
from diffasaurus.ui.user_dashboard_editor import UserDashboardEditorDialog
from diffasaurus.ui.user_dashboard_filters import (
    capture_current_view,
    configure_proxy_for_dashboard,
    dashboard_export_filename,
    dashboard_source_rows,
)
from diffasaurus.ui.user_dashboard_workspace import UserDashboardWorkspace

LARGE_SNAPSHOT_ROW_THRESHOLD = 20_000


def load_policy_snapshot_payload(report_dir: Path, snapshot: ReportSnapshot):
    descriptor = resolve_bundle_for_anchor(report_dir, snapshot)
    if descriptor is None:
        raise RuntimeError("Selected anchor does not resolve to a compatible policy bundle.")
    normalized = POLICY_SESSION_CACHE.get_normalized(descriptor)
    rows = compact_policy_inventory_rows(normalized)
    headers = ["Name", "Platform", "Type", "Source"]
    table_rows = [
        [row["name"], row["platform"], row["policy_type"], row["source"]]
        for row in rows
    ]
    stats = [
        {
            "section": "Overview",
            "title": title,
            "value": value,
            "kind": "neutral",
        }
        for title, value in (
            ("Policies", str(len(normalized.policies))),
            ("Settings", str(count_semantic_settings(normalized))),
            ("Assignments", str(sum(len(p.assignments) for p in normalized.policies))),
            ("Export status", descriptor.export_status),
            ("Normalization", normalized.normalization_status),
        )
    ]
    return headers, table_rows, descriptor.path, rows, stats


def load_snapshot_payload(path: Path):
    headers, rows, delimiter = read_csv_table(path)
    analysis_model = CsvTableModel(headers, rows)
    title, stats = get_dashboard_definition(analysis_model, headers)
    return headers, rows, delimiter, title, stats


def _parse_datetime(value: str):
    raw = str(value or "").strip()
    if not raw or raw.casefold() in {"nan", "none"}:
        return None
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        result = datetime.fromisoformat(normalized)
        return result.replace(tzinfo=result.tzinfo or timezone.utc)
    except ValueError:
        pass
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class SnapshotExplorer(QWidget):
    open_configuration_policies_requested = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = CsvTableModel()
        self.proxy = CsvFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.snapshots: list[ReportSnapshot] = []
        self._family = ""
        self._report_dir: Path | None = None
        self._policy_snapshot_path: str | None = None
        self._policy_rows: list[dict[str, str]] = []
        self.loaded_path: Path | None = None
        self._filters: dict = {}
        self._generation = 0
        self._last_snapshot_was_large = False
        self._tasks: set[BackgroundCall] = set()
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(2)
        self._dashboard_store = UserDashboardStore()
        self._smart_search_columns: list[int] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        snapshot_group = QVBoxLayout()
        snapshot_group.setSpacing(3)
        caption = QLabel("SNAPSHOT")
        caption.setObjectName("fieldLabel")
        self.snapshot_combo = QComboBox()
        self.snapshot_combo.setMinimumWidth(330)
        snapshot_group.addWidget(caption)
        snapshot_group.addWidget(self.snapshot_combo)
        controls.addLayout(snapshot_group, 2)

        view_group = QVBoxLayout()
        view_group.setSpacing(3)
        view_caption = QLabel("VIEW")
        view_caption.setObjectName("fieldLabel")
        views = QHBoxLayout()
        views.setSpacing(6)
        self.table_button = QPushButton("▦  Table")
        self.dashboard_button = QPushButton("◫  Dashboard")
        for button in (self.table_button, self.dashboard_button):
            button.setCheckable(True)
            button.setObjectName("filterButton")
            views.addWidget(button)
        self.table_button.setChecked(True)
        view_group.addWidget(view_caption)
        view_group.addLayout(views)
        controls.addLayout(view_group)
        controls.addStretch()
        layout.addLayout(controls)

        self.table_toolbar = QWidget()
        table_tools = QHBoxLayout(self.table_toolbar)
        table_tools.setContentsMargins(0, 0, 0, 0)
        self.filter_button = QPushButton("◇  Multi-column filter")
        self.clear_button = QPushButton("Clear filters")
        self.clear_button.setEnabled(False)
        self.export_view_button = QPushButton("Export view")
        self.export_selection_button = QPushButton("Export selection")
        self.export_selection_button.setEnabled(False)
        self.save_as_dashboard_button = QPushButton("Save as dashboard")
        self.save_as_dashboard_button.setObjectName("secondaryButton")
        self.search_mode = QComboBox()
        self.search_mode.addItem("Smart search", "smart")
        self.search_mode.addItem("All columns", "all")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search this snapshot…")
        self.search.setMinimumWidth(260)
        table_tools.addWidget(self.filter_button)
        table_tools.addWidget(self.clear_button)
        table_tools.addWidget(self.export_view_button)
        table_tools.addWidget(self.export_selection_button)
        table_tools.addWidget(self.save_as_dashboard_button)
        table_tools.addStretch()
        table_tools.addWidget(self.search_mode)
        table_tools.addWidget(self.search, 1)

        self.dashboard_toolbar = QWidget()
        dashboard_tools = QHBoxLayout(self.dashboard_toolbar)
        dashboard_tools.setContentsMargins(0, 0, 0, 0)
        self.new_dashboard_button = QPushButton("+ New dashboard")
        self.import_dashboards_button = QPushButton("Import")
        self.export_dashboards_button = QPushButton("Export definitions")
        self.reset_dashboards_button = QPushButton("Reset my dashboards")
        for button in (
            self.new_dashboard_button,
            self.import_dashboards_button,
            self.export_dashboards_button,
            self.reset_dashboards_button,
        ):
            button.setObjectName("secondaryButton")
        dashboard_tools.addWidget(self.new_dashboard_button)
        dashboard_tools.addStretch()
        dashboard_tools.addWidget(self.import_dashboards_button)
        dashboard_tools.addWidget(self.export_dashboards_button)
        dashboard_tools.addWidget(self.reset_dashboards_button)

        self.toolbar_stack = QStackedWidget()
        self.toolbar_stack.addWidget(self.table_toolbar)
        self.toolbar_stack.addWidget(self.dashboard_toolbar)
        layout.addWidget(self.toolbar_stack)

        if self._dashboard_store.last_load_warning:
            QMessageBox.warning(
                self,
                "Snapshot explorer",
                self._dashboard_store.last_load_warning,
            )

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFormat("Loading snapshot in the background…")
        self.progress.hide()
        layout.addWidget(self.progress)

        self.views = QStackedWidget()
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(25)
        self.table.horizontalHeader().setDefaultSectionSize(175)
        self.table.horizontalHeader().setMinimumSectionSize(80)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        table_layout.addWidget(self.table)
        self.views.addWidget(table_page)
        self.dashboard = UserDashboardWorkspace()
        self.views.addWidget(self.dashboard)
        layout.addWidget(self.views, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Choose a report family and snapshot")
        self.status.setStyleSheet("color:#8295a8;")
        self.filter_summary = QLabel("No filters")
        self.filter_summary.setObjectName("schemaBadge")
        footer.addWidget(self.status)
        footer.addStretch()
        footer.addWidget(self.filter_summary)
        layout.addLayout(footer)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search_mode.currentIndexChanged.connect(self._apply_search)
        self.snapshot_combo.currentIndexChanged.connect(self.load_selected)
        self.table_button.clicked.connect(lambda: self.show_view(0))
        self.dashboard_button.clicked.connect(lambda: self.show_view(1))
        self.filter_button.clicked.connect(self.open_filter)
        self.clear_button.clicked.connect(self.clear_filters)
        self.export_view_button.clicked.connect(self.export_view)
        self.export_selection_button.clicked.connect(self.export_selection)
        self.save_as_dashboard_button.clicked.connect(self._save_current_view_as_dashboard)
        self.new_dashboard_button.clicked.connect(self._create_user_dashboard)
        self.import_dashboards_button.clicked.connect(self._import_user_dashboards)
        self.export_dashboards_button.clicked.connect(self._export_user_dashboard_definitions)
        self.reset_dashboards_button.clicked.connect(self._reset_user_dashboards)
        self.dashboard.open_requested.connect(self._open_user_dashboard)
        self.dashboard.edit_requested.connect(self._edit_user_dashboard)
        self.dashboard.duplicate_requested.connect(self._duplicate_user_dashboard)
        self.dashboard.delete_requested.connect(self._delete_user_dashboard)
        self.dashboard.export_requested.connect(self._export_user_dashboard_csv)
        self.dashboard.new_requested.connect(self._create_user_dashboard)
        self._bound_selection_model: QItemSelectionModel | None = None
        self.proxy.modelReset.connect(self._on_proxy_model_reset)
        self._bind_selection_model()
        self.dashboard.apply_filter_requested.connect(self.apply_dashboard_filter)
        self.open_policy_button = QPushButton("Open in Configuration policies")
        self.open_policy_button.setObjectName("primaryButton")
        self.open_policy_button.hide()
        self.open_policy_button.clicked.connect(self._emit_open_configuration_policies)
        footer.addWidget(self.open_policy_button)

    def set_report_dir(self, report_dir: Path):
        self._report_dir = report_dir

    def set_family(self, family: str):
        self._family = family
        policy_mode = is_configuration_policy_family(family)
        self.dashboard_button.setVisible(not policy_mode)
        self.dashboard_toolbar.setVisible(not policy_mode)
        self.filter_button.setVisible(not policy_mode)
        self.search_mode.setVisible(not policy_mode)
        self.search.setVisible(not policy_mode)
        self.export_view_button.setVisible(not policy_mode)
        self.export_selection_button.setVisible(not policy_mode)
        self.save_as_dashboard_button.setVisible(not policy_mode)
        self.open_policy_button.setVisible(policy_mode)
        if policy_mode:
            self.table_button.setText("▦  Policy snapshot")
            self.table_button.setChecked(True)
            self.show_view(0)
        else:
            self.table_button.setText("▦  Table")
        self._refresh_user_dashboards()
        self._update_dashboard_actions_enabled()
        self._update_export_buttons()

    def _export_enabled(self) -> bool:
        return (
            not is_configuration_policy_family(self._family)
            and self.model.rowCount() > 0
        )

    def _bind_selection_model(self) -> None:
        selection = self.table.selectionModel()
        if selection is self._bound_selection_model:
            return
        if self._bound_selection_model is not None:
            try:
                self._bound_selection_model.selectionChanged.disconnect(
                    self._update_export_selection_state
                )
            except TypeError:
                pass
        self._bound_selection_model = selection
        if selection is not None:
            selection.selectionChanged.connect(self._update_export_selection_state)

    def _on_proxy_model_reset(self) -> None:
        self._bind_selection_model()
        self._update_export_selection_state()

    def _update_export_selection_state(self) -> None:
        selection = self.table.selectionModel()
        has_selection = bool(selection and selection.selectedRows())
        self.export_selection_button.setEnabled(
            self._export_enabled() and has_selection
        )

    def _update_export_buttons(self) -> None:
        self.export_view_button.setEnabled(self._export_enabled())
        self._update_export_selection_state()

    def export_view(self) -> None:
        if not self._export_enabled():
            return
        rows = visible_source_rows(self.proxy, self.model)
        if not rows:
            return
        default_name = default_export_filename(
            self.loaded_path,
            suffix="filtered",
            fallback="snapshot_filtered.csv",
        )
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export visible rows",
            default_name,
            "CSV files (*.csv)",
        )
        if not path:
            return
        try:
            write_csv_export(
                Path(path),
                self.model.headers,
                rows,
                delimiter=self.model.delimiter,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Snapshot explorer", f"Export failed: {exc}")
            return
        self.status.setText(
            f"Exported {len(rows):,} visible rows to {Path(path).name}"
        )

    def export_selection(self) -> None:
        if not self._export_enabled():
            return
        rows = selected_source_rows(self.table, self.proxy, self.model)
        if not rows:
            return
        default_name = default_export_filename(
            self.loaded_path,
            suffix="selection",
            fallback="snapshot_selection.csv",
        )
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export selected rows",
            default_name,
            "CSV files (*.csv)",
        )
        if not path:
            return
        try:
            write_csv_export(
                Path(path),
                self.model.headers,
                rows,
                delimiter=self.model.delimiter,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Snapshot explorer", f"Export failed: {exc}")
            return
        self.status.setText(
            f"Exported {len(rows):,} selected row{'s' if len(rows) != 1 else ''} to {Path(path).name}"
        )

    def set_snapshots(self, snapshots: list[ReportSnapshot]):
        selected_path = self.snapshot_combo.currentData()
        self.snapshots = list(snapshots)
        self.snapshot_combo.blockSignals(True)
        self.snapshot_combo.clear()
        for snapshot in reversed(self.snapshots):
            self.snapshot_combo.addItem(
                f"{snapshot.label}  ·  {snapshot.path.name}",
                snapshot,
            )
        for index in range(self.snapshot_combo.count()):
            snapshot = self.snapshot_combo.itemData(index)
            if (
                isinstance(snapshot, ReportSnapshot)
                and isinstance(selected_path, ReportSnapshot)
                and snapshot.path == selected_path.path
            ):
                self.snapshot_combo.setCurrentIndex(index)
                break
        self.snapshot_combo.blockSignals(False)
        if not snapshots:
            self.model.set_table([], [])
            self.status.setText("No snapshots available for this family")
            self.loaded_path = None
            self._last_snapshot_was_large = False
        self._refresh_user_dashboards()
        self._update_export_buttons()
        self._update_dashboard_actions_enabled()

    def activate(self):
        snapshot = self.snapshot_combo.currentData()
        if isinstance(snapshot, ReportSnapshot) and snapshot.path != self.loaded_path:
            self.load_selected()

    def select_snapshot(self, snapshot: ReportSnapshot):
        for index in range(self.snapshot_combo.count()):
            candidate = self.snapshot_combo.itemData(index)
            if (
                isinstance(candidate, ReportSnapshot)
                and candidate.path == snapshot.path
            ):
                self.snapshot_combo.setCurrentIndex(index)
                if candidate.path == self.loaded_path:
                    self.show_view(0)
                return

    def show_view(self, index: int):
        self.views.setCurrentIndex(index)
        self.table_button.setChecked(index == 0)
        self.dashboard_button.setChecked(index == 1)
        self.toolbar_stack.setCurrentIndex(index)
        self._update_dashboard_actions_enabled()

    def load_selected(self, _index: int | None = None):
        snapshot = self.snapshot_combo.currentData()
        if not isinstance(snapshot, ReportSnapshot):
            return
        self._generation += 1
        generation = self._generation
        self.progress.show()
        self.snapshot_combo.setEnabled(False)
        self.filter_button.setEnabled(False)
        self.status.setText(f"Loading {snapshot.path.name} without blocking the app…")
        if is_configuration_policy_family(self._family) and self._report_dir is not None:
            task = BackgroundCall(load_policy_snapshot_payload, self._report_dir, snapshot)
            self._tasks.add(task)
            task.signals.succeeded.connect(
                lambda payload: self._policy_snapshot_loaded(generation, snapshot, payload)
            )
            task.signals.failed.connect(
                lambda message: self._snapshot_failed(generation, message)
            )
            task.signals.done.connect(lambda: self._tasks.discard(task))
            self.thread_pool.start(task)
            return
        task = BackgroundCall(load_snapshot_payload, snapshot.path)
        self._tasks.add(task)
        task.signals.succeeded.connect(
            lambda payload: self._snapshot_loaded(generation, snapshot, payload)
        )
        task.signals.failed.connect(
            lambda message: self._snapshot_failed(generation, message)
        )
        task.signals.done.connect(lambda: self._tasks.discard(task))
        self.thread_pool.start(task)

    def _snapshot_loaded(self, generation: int, snapshot: ReportSnapshot, payload):
        if generation != self._generation:
            return
        headers, rows, delimiter, title, stats = payload
        large_snapshot = len(rows) >= LARGE_SNAPSHOT_ROW_THRESHOLD
        header = self.table.horizontalHeader()
        prior_sort_column = header.sortIndicatorSection()
        prior_sort_order = header.sortIndicatorOrder()
        had_prior_snapshot = self.loaded_path is not None
        self.table.setSortingEnabled(False)
        self.proxy.begin_bulk_update()
        try:
            self.model.set_table(headers, rows, delimiter)
            self.loaded_path = snapshot.path
            self._configure_smart_search()
            self._reset_filters_after_load()
        finally:
            if large_snapshot:
                header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            self.proxy.end_bulk_update()
        self.table.setSortingEnabled(True)
        if not large_snapshot:
            if (
                had_prior_snapshot
                and not self._last_snapshot_was_large
                and prior_sort_column >= 0
            ):
                sort_column = prior_sort_column
                sort_order = prior_sort_order
            else:
                sort_column = 0
                sort_order = Qt.SortOrder.AscendingOrder
            self.table.sortByColumn(sort_column, sort_order)
        self._last_snapshot_was_large = large_snapshot
        self.dashboard.build_builtin_dashboard(title or "Snapshot Dashboard", stats or [])
        self._refresh_user_dashboards()
        self.progress.hide()
        self.snapshot_combo.setEnabled(True)
        self.filter_button.setEnabled(bool(headers))
        self._update_status()
        self.status.setText(
            f"{len(rows):,} rows · {len(headers):,} columns · {snapshot.path.name}"
        )
        self._update_export_buttons()
        self._update_dashboard_actions_enabled()

    def _policy_snapshot_loaded(self, generation: int, snapshot: ReportSnapshot, payload):
        if generation != self._generation:
            return
        headers, rows, bundle_path, policy_rows, stats = payload
        self.model.set_table(headers, rows, ",")
        self.loaded_path = snapshot.path
        self._policy_snapshot_path = bundle_path
        self._policy_rows = policy_rows
        self.proxy.setFilterFixedString("")
        self.progress.hide()
        self.snapshot_combo.setEnabled(True)
        self.filter_button.setEnabled(False)
        self.status.setText(
            f"Policy bundle · {len(policy_rows)} policies · {snapshot.path.name}"
        )
        self.dashboard.build_builtin_dashboard("Policy snapshot", stats or [])
        self._update_export_buttons()

    def _emit_open_configuration_policies(self):
        policy_key = None
        selected = self.table.selectionModel().selectedRows()
        if selected:
            row = selected[0].row()
            if 0 <= row < len(self._policy_rows):
                policy_key = self._policy_rows[row].get("policy_key")
        self.open_configuration_policies_requested.emit(
            self._policy_snapshot_path or "",
            policy_key,
        )

    def _snapshot_failed(self, generation: int, message: str):
        if generation != self._generation:
            return
        self.progress.hide()
        self.snapshot_combo.setEnabled(True)
        self.filter_button.setEnabled(True)
        self.status.setText("Snapshot load failed")
        QMessageBox.warning(self, "Snapshot explorer", message)

    def _configure_smart_search(self):
        candidates = {
            "displayname",
            "userprincipalname",
            "upn",
            "mail",
            "givenname",
            "surname",
            "employeeid",
            "devicename",
            "serialnumber",
            "operatingsystem",
            "compliancestate",
            "groupid",
            "accesspackageid",
            "primarysmtpaddress",
        }
        columns = [
            index
            for index, header in enumerate(self.model.headers)
            if header.strip().casefold() in candidates
        ]
        self._smart_search_columns = columns
        self.proxy.set_smart_search_columns(columns)

    def _apply_search(self):
        self.proxy.set_search_mode(str(self.search_mode.currentData() or "smart"))
        self.proxy.set_search_text(self.search.text())
        self._update_status()

    def open_filter(self):
        if not self.model.rowCount():
            return
        dialog = MultiColumnFilterDialog(
            self,
            self.model,
            current_filters=self._filters,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._filters = dialog.result_filters
        self.proxy.clear_filters()
        for column, data in self._filters.items():
            self.proxy.set_column_allowed_values(
                int(column),
                set(data.get("allowed", set())),
                bool(data.get("allow_empty", False)),
            )
        self.show_view(0)
        self._update_status()

    def clear_filters(self):
        self._filters = {}
        self.proxy.clear_filters()
        self.search.clear()
        self.proxy.set_search_text("")
        self.table.clearSelection()
        self._update_status()
        self._update_export_buttons()

    def _reset_filters_after_load(self):
        self._filters = {}
        self.proxy.clear_filters()
        self.search.clear()
        self.proxy.set_search_text("")
        self.table.clearSelection()

    def _column(self, name: str) -> int | None:
        wanted = str(name or "").strip().casefold()
        for index, header in enumerate(self.model.headers):
            if header.strip().casefold() == wanted:
                return index
        return None

    def apply_dashboard_filter(self, payload: dict):
        custom = payload.get("custom_filter") or {}
        if custom:
            self._apply_date_filter(custom)
            return
        specification = payload.get("filter_spec") or {}
        self.clear_filters()
        if not specification:
            self.show_view(0)
            return
        mode = specification.get("__mode__")
        if mode == "and":
            for condition in specification.get("conditions", []):
                column = self._column(condition.get("column", ""))
                if column is not None:
                    self.proxy.set_column_allowed_values(
                        column,
                        {str(value) for value in condition.get("values", [])},
                    )
        elif mode:
            self._apply_special_filter(specification)
        else:
            for name, values in specification.items():
                column = self._column(name)
                if column is None:
                    continue
                allowed = {str(value).strip() for value in values}
                self.proxy.set_column_allowed_values(
                    column,
                    allowed - {""},
                    "" in allowed,
                )
        self.show_view(0)
        self._update_status()

    def _apply_special_filter(self, specification: dict):
        mode = specification.get("__mode__")
        column = self._column(specification.get("column", ""))
        if column is None:
            return
        values = self.model.column_values(column)
        if mode == "distinct_nonblank":
            seen = set()
            rows = set()
            for row, value in enumerate(values):
                if value and value not in seen:
                    seen.add(value)
                    rows.add(row)
            self.proxy.set_fixed_rows(rows)
            return
        wanted = set()
        needle = str(specification.get("value", "")).casefold()
        needles = [
            str(value).casefold() for value in specification.get("values", [])
        ]
        for value in values:
            lowered = value.casefold()
            match = (
                (mode in {"blank"} and not value)
                or (mode in {"nonblank", "not_empty"} and bool(value))
                or (mode == "contains" and needle in lowered)
                or (mode == "contains_any" and any(item in lowered for item in needles))
                or (mode == "startswith" and lowered.startswith(needle))
            )
            if mode in {"eq0", "gt0"}:
                try:
                    number = float(value)
                    match = number == 0 if mode == "eq0" else number > 0
                except ValueError:
                    match = False
            if match:
                wanted.add(value)
        self.proxy.set_column_allowed_values(column, wanted - {""}, "" in wanted)

    def _apply_date_filter(self, specification: dict):
        self.clear_filters()
        column = self._column(specification.get("column", ""))
        if column is None:
            return
        mode = specification.get("mode")
        days = int(specification.get("days", 0) or 0)
        now = datetime.now(timezone.utc)
        rows = set()
        for row, value in enumerate(self.model.column_values(column)):
            parsed = _parse_datetime(value)
            age = (now - parsed).days if parsed else None
            match = (
                (mode == "blank" and parsed is None)
                or (mode == "days_lte" and age is not None and age <= days)
                or (mode == "days_gt" and age is not None and age > days)
                or (
                    mode == "days_gt_or_blank"
                    and (age is None or age > days)
                )
            )
            if match:
                rows.add(row)
        self.proxy.set_fixed_rows(rows)
        self.show_view(0)
        self._update_status()

    def _update_status(self):
        total = self.model.rowCount()
        visible = self.proxy.rowCount()
        search_active = bool(self.search.text().strip())
        count = self.proxy.active_filter_count()
        self.clear_button.setEnabled(bool(count or search_active))
        self.filter_summary.setText(
            f"{count} column filter{'s' if count != 1 else ''}"
            if count
            else "No column filters"
        )
        name = self.loaded_path.name if self.loaded_path else "No snapshot"
        self.status.setText(
            f"{visible:,} of {total:,} rows visible · "
            f"{self.model.columnCount():,} columns · {name}"
        )
        self._update_export_selection_state()

    def _user_dashboards_enabled(self) -> bool:
        return (
            not is_configuration_policy_family(self._family)
            and bool(self._family)
        )

    def _update_dashboard_actions_enabled(self) -> None:
        enabled = self._user_dashboards_enabled()
        model_ready = self.model.rowCount() > 0
        self.new_dashboard_button.setEnabled(enabled and model_ready)
        self.save_as_dashboard_button.setEnabled(enabled and model_ready)
        self.import_dashboards_button.setEnabled(enabled)
        self.export_dashboards_button.setEnabled(enabled)
        self.reset_dashboards_button.setEnabled(enabled)

    def _refresh_user_dashboards(self) -> None:
        if not self._user_dashboards_enabled():
            self.dashboard.set_dashboards([], self.model.headers)
            return
        dashboards = self._dashboard_store.for_family(self._family)
        self.dashboard.set_dashboards(dashboards, self.model.headers)

    def _named_filters_from_runtime(self) -> dict[int, dict]:
        if self._filters:
            return {
                int(column): {
                    "allowed": set(data.get("allowed", set())),
                    "allow_empty": bool(data.get("allow_empty", False)),
                }
                for column, data in self._filters.items()
            }
        return {
            int(column): {
                "allowed": set(data.get("allowed", set())),
                "allow_empty": bool(data.get("allow_empty", False)),
            }
            for column, data in self.proxy.column_filter_map().items()
        }

    def _apply_user_dashboard(self, dashboard: UserDashboard) -> bool:
        missing = validate_dashboard_columns(dashboard, self.model.headers)
        if missing:
            QMessageBox.warning(
                self,
                "Custom dashboard",
                "This dashboard needs attention before it can be opened.\n"
                f"Missing columns: {', '.join(missing)}",
            )
            return False
        self.clear_filters()
        configure_proxy_for_dashboard(
            self.proxy,
            self.model,
            dashboard,
            smart_search_columns=self._smart_search_columns,
        )
        self._filters = named_filters_to_index(dashboard.filters, self.model.headers)
        self.search_mode.blockSignals(True)
        self.search.blockSignals(True)
        search_index = self.search_mode.findData(dashboard.search_mode)
        if search_index >= 0:
            self.search_mode.setCurrentIndex(search_index)
        self.search.setText(dashboard.search_text)
        self.search_mode.blockSignals(False)
        self.search.blockSignals(False)
        if dashboard.sort and dashboard.sort.column:
            column = self._column(dashboard.sort.column)
            if column is not None:
                order = (
                    Qt.SortOrder.AscendingOrder
                    if dashboard.sort.order == "asc"
                    else Qt.SortOrder.DescendingOrder
                )
                self.table.setSortingEnabled(True)
                self.table.sortByColumn(column, order)
        self.table.clearSelection()
        self.show_view(0)
        self._update_status()
        return True

    def _create_user_dashboard(self) -> None:
        if not self._user_dashboards_enabled() or not self.model.rowCount():
            return
        dialog = UserDashboardEditorDialog(
            self,
            model=self.model,
            family=self._family,
            title="New custom dashboard",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.dashboard is None:
            return
        self._dashboard_store.create(dialog.dashboard)
        self._refresh_user_dashboards()
        self.status.setText(f"Saved custom dashboard “{dialog.dashboard.name}”")

    def _save_current_view_as_dashboard(self) -> None:
        if not self._user_dashboards_enabled() or not self.model.rowCount():
            return
        header = self.table.horizontalHeader()
        draft, notice = capture_current_view(
            family=self._family,
            headers=self.model.headers,
            filters=self._named_filters_from_runtime(),
            search_mode=str(self.search_mode.currentData() or "smart"),
            search_text=self.search.text(),
            sort_column=header.sortIndicatorSection(),
            sort_order=header.sortIndicatorOrder(),
            has_fixed_row_filter=self.proxy.has_fixed_row_filter(),
        )
        if draft is None:
            QMessageBox.warning(self, "Save as dashboard", notice)
            return
        name, ok = QInputDialog.getText(
            self,
            "Save as dashboard",
            "Dashboard name:",
        )
        if not ok or not name.strip():
            return
        description, ok = QInputDialog.getMultiLineText(
            self,
            "Save as dashboard",
            "Description:",
        )
        if not ok:
            return
        draft.name = name.strip()
        draft.description = description.strip()
        self._dashboard_store.create(draft)
        self._refresh_user_dashboards()
        if notice:
            QMessageBox.information(self, "Save as dashboard", notice)
        self.status.setText(f'Dashboard "{draft.name}" saved')

    def _edit_user_dashboard(self, dashboard_id: str) -> None:
        dashboard = self._dashboard_store.get(dashboard_id)
        if dashboard is None or not self.model.rowCount():
            return
        dialog = UserDashboardEditorDialog(
            self,
            model=self.model,
            family=self._family,
            dashboard=dashboard,
            title="Edit custom dashboard",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.dashboard is None:
            return
        updated = dialog.dashboard
        updated.id = dashboard.id
        updated.created_at = dashboard.created_at
        self._dashboard_store.update(updated)
        self._refresh_user_dashboards()

    def _open_user_dashboard(self, dashboard_id: str) -> None:
        dashboard = self._dashboard_store.get(dashboard_id)
        if dashboard is None:
            return
        self._apply_user_dashboard(dashboard)

    def _duplicate_user_dashboard(self, dashboard_id: str) -> None:
        try:
            copy = self._dashboard_store.duplicate(dashboard_id)
        except KeyError:
            return
        self._refresh_user_dashboards()
        self.status.setText(f"Duplicated dashboard as “{copy.name}”")

    def _delete_user_dashboard(self, dashboard_id: str) -> None:
        dashboard = self._dashboard_store.get(dashboard_id)
        if dashboard is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete custom dashboard",
            f'Delete "{dashboard.name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._dashboard_store.delete(dashboard_id)
        self._refresh_user_dashboards()

    def _export_user_dashboard_csv(self, dashboard_id: str) -> None:
        dashboard = self._dashboard_store.get(dashboard_id)
        if dashboard is None or not self._export_enabled():
            return
        rows, missing = dashboard_source_rows(
            self.model,
            dashboard,
            smart_search_columns=self._smart_search_columns,
        )
        if missing:
            QMessageBox.warning(
                self,
                "Export dashboard",
                "This dashboard is incompatible with the current snapshot.\n"
                f"Missing columns: {', '.join(missing)}",
            )
            return
        if not rows:
            QMessageBox.information(
                self,
                "Export dashboard",
                "This dashboard matches no rows in the current snapshot.",
            )
            return
        default_name = dashboard_export_filename(self.loaded_path, dashboard.name)
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export dashboard rows",
            default_name,
            "CSV files (*.csv)",
        )
        if not path:
            return
        try:
            write_csv_export(
                Path(path),
                self.model.headers,
                rows,
                delimiter=self.model.delimiter,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Export dashboard", f"Export failed: {exc}")
            return
        self.status.setText(
            f"Exported {len(rows):,} dashboard rows to {Path(path).name}"
        )

    def _export_user_dashboard_definitions(self) -> None:
        if not self._user_dashboards_enabled():
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export custom dashboard definitions",
            "diffasaurus-user-dashboards.json",
            "JSON files (*.json)",
        )
        if not path:
            return
        payload = self._dashboard_store.export_definitions()
        try:
            Path(path).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Export dashboard definitions",
                f"Export failed: {exc}",
            )

    def _import_user_dashboards(self) -> None:
        if not self._user_dashboards_enabled():
            return
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Import custom dashboard definitions",
            "",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(
                self,
                "Import dashboard definitions",
                f"Could not read dashboard definitions: {exc}",
            )
            return
        try:
            imported = self._dashboard_store.import_definitions(payload)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "Import dashboard definitions",
                str(exc),
            )
            return
        self._refresh_user_dashboards()
        QMessageBox.information(
            self,
            "Import dashboard definitions",
            f"Imported {len(imported):,} custom dashboard{'s' if len(imported) != 1 else ''}.",
        )

    def _reset_user_dashboards(self) -> None:
        if not self._user_dashboards_enabled():
            return
        answer = QMessageBox.question(
            self,
            "Reset my dashboards",
            "Delete all custom dashboards?\nBuilt-in dashboards will not be affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._dashboard_store.reset()
        self._refresh_user_dashboards()
