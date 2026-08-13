"""Configuration Policies administrator page (Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.configuration_policies import (
    compare_policy_bundles,
    discover_policy_snapshots,
    normalize_bundle,
    select_previous_snapshot,
)
from diffasaurus.core.configuration_policies.comparison_models import SnapshotDescriptor
from diffasaurus.core.configuration_policies.integration import (
    POLICY_SESSION_CACHE,
    classify_trust_banner,
    legacy_configuration_policy_diagnostics,
    resolve_group_display_name,
)
from diffasaurus.core.configuration_policies.models import NormalizedPolicy
from diffasaurus.core.configuration_policies.history import _parse_captured_at_utc
from diffasaurus.ui.background import BackgroundCall
from diffasaurus.ui.configuration_policy_presentation import (
    CHANGE_STATE_LABELS,
    ConfigurationPolicyPageModel,
    build_modern_setting_tree,
    build_page_model,
    coverage_label,
    event_type_label,
    export_source_label,
    filter_inventory_rows,
    filter_mode_label,
    format_snapshot_selector_label,
    format_value,
    humanize_property_path,
    policy_events,
    resolve_filter_presentation,
    assignment_target_label,
)
from diffasaurus.ui.recent_changes import SummaryCard

COLORS = {
    "surface": "#101b26",
    "surface2": "#152331",
    "border": "#26394a",
    "text": "#f2f7fb",
    "muted": "#8295a8",
    "teal": "#8bd450",
    "green": "#4fd1a5",
    "red": "#fb7185",
    "amber": "#f5b942",
    "blue": "#65a9ff",
}

CLASSIC_EXPLICITNESS_NOTE = (
    "Classic profiles expose observed API properties. Diffasaurus cannot determine "
    "from this snapshot alone which returned properties were explicitly configured "
    "in the Intune UI."
)


@dataclass
class ConfigurationPolicyLoadResult:
    generation: int
    report_dir: str
    snapshot_path: str | None
    model: ConfigurationPolicyPageModel


def load_configuration_policy_session(
    report_dir: str,
    snapshot_path: str | None,
    generation: int,
) -> ConfigurationPolicyLoadResult:
    discovery = discover_policy_snapshots(report_dir)
    snapshots = list(discovery.snapshots)
    selected: SnapshotDescriptor | None = None
    if snapshots:
        if snapshot_path:
            selected = next((item for item in snapshots if item.path == snapshot_path), None)
            if selected is None:
                anchor_stem = Path(snapshot_path).stem
                selected = next(
                    (item for item in snapshots if item.snapshot_id == anchor_stem),
                    None,
                )
        if selected is None:
            selected = snapshots[-1]

    previous = select_previous_snapshot(report_dir, selected) if selected else None
    normalized = None
    normalization_error: str | None = None
    if selected is not None:
        try:
            normalized = normalize_bundle(selected.path)
        except Exception as exc:
            normalization_error = str(exc)

    comparison = None
    comparison_error: str | None = None
    if normalized is not None and previous is not None:
        try:
            comparison = compare_policy_bundles(previous.path, selected.path)
        except Exception as exc:
            comparison_error = str(exc)

    model = build_page_model(
        snapshots=snapshots,
        diagnostics_count=len(discovery.diagnostics),
        legacy_export_count=legacy_configuration_policy_diagnostics(discovery.diagnostics),
        selected=selected,
        previous=previous,
        normalized=normalized,
        comparison=comparison,
        normalization_error=normalization_error,
        comparison_error=comparison_error,
    )
    return ConfigurationPolicyLoadResult(
        generation=generation,
        report_dir=report_dir,
        snapshot_path=selected.path if selected else None,
        model=model,
    )


class ConfigurationPolicyPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._report_dir: Path | None = None
        self._generation = 0
        self._active_generation = 0
        self._tasks: set[BackgroundCall] = set()
        self._session_cache: dict[str, ConfigurationPolicyPageModel] = {}
        self._model: ConfigurationPolicyPageModel | None = None
        self._selected_policy_key: str | None = None
        self._loading_snapshot_path: str | None = None
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        top = QHBoxLayout()
        snapshot_group = QVBoxLayout()
        snapshot_group.setSpacing(3)
        snapshot_caption = QLabel("SNAPSHOT")
        snapshot_caption.setObjectName("fieldLabel")
        self.snapshot_combo = QComboBox()
        self.snapshot_combo.setMinimumWidth(280)
        snapshot_group.addWidget(snapshot_caption)
        snapshot_group.addWidget(self.snapshot_combo)
        top.addLayout(snapshot_group, 2)
        self.trust_banner = QLabel("")
        self.trust_banner.setWordWrap(True)
        self.trust_banner.hide()
        top.addWidget(self.trust_banner, 3)
        layout.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFormat("Loading configuration policy snapshot…")
        self.progress.hide()
        layout.addWidget(self.progress)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_policies = SummaryCard("Policies", accent=COLORS["teal"])
        self.card_settings = SummaryCard("Settings", accent=COLORS["blue"])
        self.card_assignments = SummaryCard("Assignments", accent=COLORS["green"])
        self.card_changes = SummaryCard("Changes vs previous", accent=COLORS["amber"])
        for card in (
            self.card_policies,
            self.card_settings,
            self.card_assignments,
            self.card_changes,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search policies…")
        self.search.setMinimumWidth(200)
        self.platform_filter = QComboBox()
        self.source_filter = QComboBox()
        self.change_filter = QComboBox()
        for label, widget in (
            ("Search", self.search),
            ("Platform", self.platform_filter),
            ("Source / type", self.source_filter),
            ("Change", self.change_filter),
        ):
            group = QVBoxLayout()
            group.setSpacing(3)
            caption = QLabel(label.upper())
            caption.setObjectName("fieldLabel")
            group.addWidget(caption)
            group.addWidget(widget)
            filters.addLayout(group, 1 if widget is self.search else 0)
        layout.addLayout(filters)

        self.body_stack = QStackedWidget()
        self.empty_state = QLabel(
            "No Configuration Policy snapshots found in this report source.\n\n"
            "Ensure the Configuration Policy exporter has produced a snapshot bundle."
        )
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setStyleSheet(f"color:{COLORS['muted']}; padding: 48px;")
        self.body_stack.addWidget(self.empty_state)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.comparison_notice = QLabel("")
        self.comparison_notice.setWordWrap(True)
        self.comparison_notice.hide()
        content_layout.addWidget(self.comparison_notice)
        self.diagnostics_notice = QLabel("")
        self.diagnostics_notice.setWordWrap(True)
        self.diagnostics_notice.hide()
        content_layout.addWidget(self.diagnostics_notice)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        inventory_panel = QWidget()
        inventory_layout = QVBoxLayout(inventory_panel)
        inventory_layout.setContentsMargins(0, 0, 0, 0)
        inventory_layout.setSpacing(6)
        inventory_title = QLabel("POLICY INVENTORY")
        inventory_title.setObjectName("fieldLabel")
        inventory_layout.addWidget(inventory_title)
        self.inventory_table = QTableWidget(0, 6)
        self.inventory_table.setHorizontalHeaderLabels(
            ["Name", "Platform", "Type", "Source", "Assignments", "Change"]
        )
        self.inventory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.inventory_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.inventory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.inventory_table.verticalHeader().setVisible(False)
        self.inventory_table.setAlternatingRowColors(True)
        self.inventory_table.setShowGrid(False)
        header = self.inventory_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(80)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._inventory_column_max_widths = (0, 100, 150, 90, 96, 130)
        inventory_layout.addWidget(self.inventory_table)
        splitter.addWidget(inventory_panel)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(8)
        self.detail_header = QLabel("Select a policy")
        self.detail_header.setStyleSheet("font-size: 18px; font-weight: 650;")
        self.detail_subheader = QLabel("")
        self.detail_subheader.setWordWrap(True)
        self.detail_subheader.setStyleSheet(f"color:{COLORS['muted']};")
        detail_layout.addWidget(self.detail_header)
        detail_layout.addWidget(self.detail_subheader)
        self.detail_tabs = QTabWidget()
        self.overview_tab = QWidget()
        self._build_overview_tab()
        self.settings_tab = QWidget()
        self._build_settings_tab()
        self.assignments_tab = QWidget()
        self._build_assignments_tab()
        self.changes_tab = QWidget()
        self._build_changes_tab()
        self.detail_tabs.addTab(self.overview_tab, "Overview")
        self.detail_tabs.addTab(self.settings_tab, "Settings")
        self.detail_tabs.addTab(self.assignments_tab, "Assignments")
        self.detail_tabs.addTab(self.changes_tab, "Changes")
        detail_layout.addWidget(self.detail_tabs, 1)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([360, 540])
        content_layout.addWidget(splitter, 1)
        self.body_stack.addWidget(content)
        layout.addWidget(self.body_stack, 1)

        self.error_banner = QLabel("")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        self.snapshot_combo.currentIndexChanged.connect(self._snapshot_changed)
        self.search.textChanged.connect(self._apply_filters)
        self.platform_filter.currentIndexChanged.connect(self._apply_filters)
        self.source_filter.currentIndexChanged.connect(self._apply_filters)
        self.change_filter.currentIndexChanged.connect(self._apply_filters)
        self.inventory_table.itemSelectionChanged.connect(self._policy_selection_changed)

    def _build_overview_tab(self) -> None:
        layout = QVBoxLayout(self.overview_tab)
        self.overview_grid = QGridLayout()
        self.overview_grid.setHorizontalSpacing(18)
        self.overview_grid.setVerticalSpacing(8)
        layout.addLayout(self.overview_grid)
        self.overview_coverage = QLabel("")
        self.overview_coverage.setWordWrap(True)
        layout.addWidget(self.overview_coverage)
        layout.addStretch()

    def _build_settings_tab(self) -> None:
        layout = QVBoxLayout(self.settings_tab)
        self.classic_notice = QLabel(CLASSIC_EXPLICITNESS_NOTE)
        self.classic_notice.setWordWrap(True)
        self.classic_notice.setStyleSheet(f"color:{COLORS['amber']};")
        self.classic_notice.hide()
        layout.addWidget(self.classic_notice)
        self.admx_notice = QLabel("")
        self.admx_notice.setWordWrap(True)
        self.admx_notice.hide()
        layout.addWidget(self.admx_notice)
        self.modern_tree = QTreeWidget()
        self.modern_tree.setHeaderLabels(["Setting", "Configured value", "Type"])
        self.modern_tree.setAlternatingRowColors(True)
        self.modern_tree.setStyleSheet(
            f"""
            QTreeWidget {{
                background: #0d1924;
                alternate-background-color: {COLORS['surface2']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
            QTreeWidget::item {{
                padding: 3px 7px;
            }}
            QTreeWidget::item:selected {{
                background: #23465a;
                color: white;
            }}
            QTreeWidget::item:selected:active {{
                background: #23465a;
                color: white;
            }}
            """
        )
        self.modern_tree.header().setStretchLastSection(False)
        self.modern_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.modern_tree)
        self.classic_table = QTableWidget(0, 2)
        self.classic_table.setHorizontalHeaderLabels(["Property", "Observed value"])
        self.classic_table.verticalHeader().setVisible(False)
        self.classic_table.setAlternatingRowColors(True)
        self.classic_table.hide()
        layout.addWidget(self.classic_table)
        self.admx_table = QTableWidget(0, 3)
        self.admx_table.setHorizontalHeaderLabels(["Setting", "State", "Presentation"])
        self.admx_table.verticalHeader().setVisible(False)
        self.admx_table.setAlternatingRowColors(True)
        self.admx_table.hide()
        layout.addWidget(self.admx_table)
        self.settings_empty = QLabel("No settings available for this policy.")
        self.settings_empty.setStyleSheet(f"color:{COLORS['muted']};")
        self.settings_empty.hide()
        layout.addWidget(self.settings_empty)

    def _build_assignments_tab(self) -> None:
        layout = QVBoxLayout(self.assignments_tab)
        self.assignments_table = QTableWidget(0, 4)
        self.assignments_table.setHorizontalHeaderLabels(["Target", "Group", "Filter", "Filter mode"])
        self.assignments_table.verticalHeader().setVisible(False)
        self.assignments_table.setAlternatingRowColors(True)
        self.assignments_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.assignments_table)
        self.assignments_empty = QLabel("No assignments in this snapshot.")
        self.assignments_empty.setStyleSheet(f"color:{COLORS['muted']};")
        self.assignments_empty.hide()
        layout.addWidget(self.assignments_empty)

    def _build_changes_tab(self) -> None:
        layout = QVBoxLayout(self.changes_tab)
        self.changes_notice = QLabel("")
        self.changes_notice.setWordWrap(True)
        self.changes_notice.hide()
        layout.addWidget(self.changes_notice)
        self.changes_table = QTableWidget(0, 3)
        self.changes_table.setHorizontalHeaderLabels(["Change", "Before", "After"])
        self.changes_table.verticalHeader().setVisible(False)
        self.changes_table.setAlternatingRowColors(True)
        self.changes_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.changes_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.changes_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.changes_table)
        self.changes_empty = QLabel("")
        self.changes_empty.setStyleSheet(f"color:{COLORS['muted']};")
        layout.addWidget(self.changes_empty)

    @property
    def snapshot_count(self) -> int:
        if self._model is None:
            return 0
        return len(self._model.snapshots)

    def source_badge_text(self, report_dir: Path) -> str:
        source_name = "LOCAL DATABASE" if report_dir.name == "reports" else report_dir.name.upper()
        count = self.snapshot_count
        label = "SNAPSHOT" if count == 1 else "SNAPSHOTS"
        return f"●  {source_name}  ·  {count} POLICY {label}"

    def invalidate(self) -> None:
        self._generation += 1
        POLICY_SESSION_CACHE.invalidate(self._report_dir)
        self._session_cache.clear()
        self._model = None
        self._selected_policy_key = None
        self._loading_snapshot_path = None
        self.snapshot_combo.blockSignals(True)
        self.snapshot_combo.clear()
        self.snapshot_combo.blockSignals(False)
        self.body_stack.setCurrentIndex(0)
        self.empty_state.setText(
            "No Configuration Policy snapshots found in this report source.\n\n"
            "Ensure the Configuration Policy exporter has produced a snapshot bundle."
        )
        self._clear_detail()

    def activate(self, report_dir: Path) -> None:
        if self._report_dir != report_dir:
            self.invalidate()
            self._report_dir = report_dir
            self._start_load(None)
            return
        if self._model is None and not self.progress.isVisible():
            self._start_load(None)

    def refresh(self, report_dir: Path) -> None:
        self.invalidate()
        self._report_dir = report_dir
        if self.isVisible():
            self._start_load(None)

    def open_snapshot(
        self,
        report_dir: Path,
        snapshot_path: str | None = None,
        *,
        policy_key: str | None = None,
    ) -> None:
        if policy_key:
            self._selected_policy_key = policy_key
        if self._report_dir != report_dir:
            self._report_dir = report_dir
            self.invalidate()
        self._report_dir = report_dir
        self._start_load(snapshot_path)

    def open_policy(
        self,
        report_dir: Path,
        policy_key: str,
        snapshot_path: str | None = None,
    ) -> None:
        self.open_snapshot(report_dir, snapshot_path, policy_key=policy_key)

    def _start_load(self, snapshot_path: str | None) -> None:
        if self._report_dir is None:
            return
        self._generation += 1
        generation = self._generation
        self._loading_snapshot_path = snapshot_path
        self.progress.show()
        self.error_banner.hide()

        if snapshot_path and snapshot_path in self._session_cache:
            model = self._session_cache[snapshot_path]
            self._apply_model(model, generation)
            self.progress.hide()
            return

        worker = BackgroundCall(
            load_configuration_policy_session,
            str(self._report_dir),
            snapshot_path,
            generation,
        )
        self._tasks.add(worker)
        worker.signals.succeeded.connect(
            lambda result: self._load_succeeded(result, generation)
        )
        worker.signals.failed.connect(
            lambda message: self._load_failed(message, generation)
        )
        worker.signals.done.connect(lambda: self._tasks.discard(worker))
        self.thread_pool.start(worker)

    def _load_succeeded(self, result: ConfigurationPolicyLoadResult, generation: int) -> None:
        if generation != self._generation:
            return
        if result.snapshot_path:
            self._session_cache[result.snapshot_path] = result.model
        self._apply_model(result.model, generation)
        self.progress.hide()

    def _load_failed(self, message: str, generation: int) -> None:
        if generation != self._generation:
            return
        self.progress.hide()
        self.error_banner.setText(f"Failed to load configuration policies: {message}")
        self.error_banner.setStyleSheet(f"color:{COLORS['red']}; padding:8px;")
        self.error_banner.show()

    def _apply_model(self, model: ConfigurationPolicyPageModel, generation: int) -> None:
        if generation != self._generation:
            return
        self._model = model
        self._populate_snapshot_combo(model)
        self._update_trust_banner(model)
        self._update_summary_cards(model)
        self._update_filter_options(model)
        self._update_notices(model)

        if not model.snapshots:
            self.body_stack.setCurrentIndex(0)
            message = (
                "No Configuration Policy snapshots found in this report source.\n\n"
                "Ensure the Configuration Policy exporter has produced a compatible snapshot bundle."
            )
            if model.legacy_export_count:
                message += (
                    "\n\nLegacy Configuration Policy export detected. "
                    "Run the current Configuration Policy exporter to create a compatible snapshot."
                )
            if model.discovery_diagnostics_count:
                message += (
                    f"\n\n{model.discovery_diagnostics_count} snapshot"
                    f"{'s' if model.discovery_diagnostics_count != 1 else ''} could not be read."
                )
            self.empty_state.setText(message)
            self._clear_detail()
            return

        if model.normalization_error:
            self.body_stack.setCurrentIndex(0)
            self.empty_state.setText(
                "The selected snapshot could not be normalized.\n\n"
                f"{model.normalization_error}"
            )
            self._clear_detail()
            return

        self.body_stack.setCurrentIndex(1)
        self._apply_filters()

    def _populate_snapshot_combo(self, model: ConfigurationPolicyPageModel) -> None:
        selected_path = model.selected_snapshot.path if model.selected_snapshot else None
        self.snapshot_combo.blockSignals(True)
        self.snapshot_combo.clear()
        for descriptor in reversed(model.snapshots):
            self.snapshot_combo.addItem(format_snapshot_selector_label(descriptor), descriptor.path)
        if selected_path:
            for index in range(self.snapshot_combo.count()):
                if self.snapshot_combo.itemData(index) == selected_path:
                    self.snapshot_combo.setCurrentIndex(index)
                    break
        self.snapshot_combo.blockSignals(False)

    def _update_trust_banner(self, model: ConfigurationPolicyPageModel) -> None:
        if not model.selected_snapshot or not model.normalized:
            self.trust_banner.hide()
            return
        presentation = classify_trust_banner(
            model.selected_snapshot.export_status,
            model.normalized,
        )
        text = presentation.headline
        if presentation.detail:
            text = f"{presentation.headline} {presentation.detail}"
        color = {
            "success": COLORS["green"],
            "informational": COLORS["blue"],
            "warning": COLORS["amber"],
            "error": COLORS["red"],
        }[presentation.level]
        self.trust_banner.setText(text)
        self.trust_banner.setStyleSheet(f"color:{color};")
        self.trust_banner.show()

    def _update_summary_cards(self, model: ConfigurationPolicyPageModel) -> None:
        self.card_policies.set_data(str(model.policy_count), "normalized policies")
        self.card_settings.set_data(str(model.setting_count), "semantic settings")
        self.card_assignments.set_data(str(model.assignment_count), "normalized assignments")
        value, detail = model.change_summary
        self.card_changes.set_data(value, detail)

    def _update_filter_options(self, model: ConfigurationPolicyPageModel) -> None:
        platforms = sorted({row.platform for row in model.inventory_rows if row.platform}, key=str.casefold)
        sources = sorted({row.source_label for row in model.inventory_rows}, key=str.casefold)
        change_values = ["All", "Added", "Modified", "Unchanged", "Indeterminate", "No baseline"]

        current_platform = self.platform_filter.currentText() or "All"
        current_source = self.source_filter.currentText() or "All"
        current_change = self.change_filter.currentText() or "All"

        for combo, values, current in (
            (self.platform_filter, ["All", *platforms], current_platform),
            (self.source_filter, ["All", *sources], current_source),
            (self.change_filter, change_values, current_change),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(values)
            if current in values:
                combo.setCurrentText(current)
            combo.blockSignals(False)

    def _update_notices(self, model: ConfigurationPolicyPageModel) -> None:
        if model.comparison_error:
            self.comparison_notice.setText(
                "Current snapshot available; historical comparison unavailable."
            )
            self.comparison_notice.setStyleSheet(f"color:{COLORS['amber']};")
            self.comparison_notice.show()
        elif model.comparison and model.comparison.comparison_status == "partial":
            self.comparison_notice.setText(
                "Comparison against the previous snapshot has partial coverage. "
                "Some change states may be indeterminate."
            )
            self.comparison_notice.setStyleSheet(f"color:{COLORS['amber']};")
            self.comparison_notice.show()
        else:
            self.comparison_notice.hide()

        if model.discovery_diagnostics_count:
            self.diagnostics_notice.setText(
                f"{model.discovery_diagnostics_count} snapshot"
                f"{'s' if model.discovery_diagnostics_count != 1 else ''} could not be read."
            )
            self.diagnostics_notice.setStyleSheet(f"color:{COLORS['amber']};")
            self.diagnostics_notice.show()
        else:
            self.diagnostics_notice.hide()

    def _snapshot_changed(self) -> None:
        if self._model is None:
            return
        path = self.snapshot_combo.currentData()
        if not isinstance(path, str) or path == (
            self._model.selected_snapshot.path if self._model.selected_snapshot else None
        ):
            return
        self._start_load(path)

    def _apply_filters(self) -> None:
        if self._model is None or self._model.normalized is None:
            return
        rows = filter_inventory_rows(
            self._model.inventory_rows,
            search=self.search.text(),
            platform=self.platform_filter.currentText(),
            source=self.source_filter.currentText(),
            change=self.change_filter.currentText(),
        )
        previous_key = self._selected_policy_key
        self.inventory_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.name,
                row.platform,
                row.policy_type,
                row.source_label,
                str(row.assignment_count),
                row.change_label,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row.policy_key)
                if column == 0:
                    item.setToolTip(row.name)
                self.inventory_table.setItem(row_index, column, item)

        self._tune_inventory_columns()

        selected_row = 0
        if previous_key:
            for row_index in range(self.inventory_table.rowCount()):
                item = self.inventory_table.item(row_index, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == previous_key:
                    selected_row = row_index
                    break
        if self.inventory_table.rowCount():
            self.inventory_table.selectRow(selected_row)
        else:
            self._clear_detail()

    def _tune_inventory_columns(self) -> None:
        header = self.inventory_table.horizontalHeader()
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.inventory_table.resizeColumnsToContents()
        for column in range(1, 6):
            max_width = self._inventory_column_max_widths[column]
            if self.inventory_table.columnWidth(column) > max_width:
                self.inventory_table.setColumnWidth(column, max_width)
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def _policy_selection_changed(self) -> None:
        items = self.inventory_table.selectedItems()
        if not items:
            return
        policy_key = items[0].data(Qt.ItemDataRole.UserRole)
        if not isinstance(policy_key, str):
            return
        self._selected_policy_key = policy_key
        self._render_policy_detail(policy_key)

    def _find_policy(self, policy_key: str) -> NormalizedPolicy | None:
        if self._model is None or self._model.normalized is None:
            return None
        return next(
            (policy for policy in self._model.normalized.policies if policy.policy_key == policy_key),
            None,
        )

    def _clear_detail(self) -> None:
        self.detail_header.setText("Select a policy")
        self.detail_subheader.setText("")
        self.overview_coverage.setText("")
        self.changes_table.setRowCount(0)
        self.assignments_table.setRowCount(0)
        self.modern_tree.clear()
        self.classic_table.setRowCount(0)
        self.admx_table.setRowCount(0)

    def _render_policy_detail(self, policy_key: str) -> None:
        policy = self._find_policy(policy_key)
        if policy is None or self._model is None:
            self._clear_detail()
            return

        presentation = policy.presentation
        name = str(presentation.get("name") or policy.semantic_metadata.get("name") or "Unnamed policy")
        description = str(presentation.get("description") or "")
        platform = str(presentation.get("platform") or "")
        policy_type = str(presentation.get("policyType") or "")
        source = export_source_label(policy.export_source)

        diff = self._model.policy_diff_by_key.get(policy_key)
        if diff is not None:
            change_label = CHANGE_STATE_LABELS.get(diff.state, diff.state)
        elif self._model.previous_snapshot:
            change_label = "Unchanged"
        else:
            change_label = "No baseline"

        snapshot_time = ""
        if self._model.selected_snapshot:
            snapshot_time = format_snapshot_selector_label(self._model.selected_snapshot).split(" · ")[0]

        self.detail_header.setText(name)
        meta_parts = [platform, policy_type, source, f"Snapshot {snapshot_time}", change_label]
        self.detail_subheader.setText(" · ".join(part for part in meta_parts if part))
        if description:
            self.detail_subheader.setText(self.detail_subheader.text() + f"\n{description}")

        self._render_overview(policy, change_label)
        self._render_settings(policy)
        self._render_assignments(policy)
        self._render_changes(policy_key, diff)

    def _render_overview(self, policy: NormalizedPolicy, change_label: str) -> None:
        while self.overview_grid.count():
            item = self.overview_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        coverage = policy.coverage
        fields = [
            ("Platform", str(policy.presentation.get("platform") or "—")),
            ("Source", export_source_label(policy.export_source)),
            ("Policy type", str(policy.presentation.get("policyType") or "—")),
            ("Change vs previous", change_label),
            (
                "Created",
                str(policy.observational_metadata.get("createdDateTime") or "—"),
            ),
            (
                "Modified",
                str(policy.observational_metadata.get("lastModifiedDateTime") or "—"),
            ),
            (
                "Role scope tags",
                ", ".join(policy.semantic_metadata.get("roleScopeTagIds") or []) or "—",
            ),
        ]
        for index, (label, value) in enumerate(fields):
            name_label = QLabel(label)
            name_label.setStyleSheet(f"color:{COLORS['muted']};")
            value_label = QLabel(value)
            value_label.setWordWrap(True)
            row = index
            self.overview_grid.addWidget(name_label, row, 0)
            self.overview_grid.addWidget(value_label, row, 1)

        coverage_lines = [
            f"Policy detail: {coverage_label(coverage.policy_detail)}",
            f"Settings: {coverage_label(coverage.settings)}",
            f"Assignments: {coverage_label(coverage.assignments)}",
            f"Definitions: {coverage_label(coverage.definitions)}",
            f"Presentation values: {coverage_label(coverage.presentation_values)}",
        ]
        if not coverage.semantic_hash_eligible:
            coverage_lines.append(
                "Semantic hash ineligible — some policy semantics may be incomplete."
            )
        self.overview_coverage.setText("\n".join(coverage_lines))

    def _render_settings(self, policy: NormalizedPolicy) -> None:
        settings = policy.settings
        kind = settings.get("kind")
        self.modern_tree.clear()
        self.classic_table.setRowCount(0)
        self.admx_table.setRowCount(0)
        self.classic_notice.hide()
        self.admx_notice.hide()
        self.modern_tree.hide()
        self.classic_table.hide()
        self.admx_table.hide()
        self.settings_empty.hide()

        if kind == "modern":
            self.modern_tree.show()
            for node in settings.get("nodes", []):
                if isinstance(node, dict):
                    self._add_modern_tree_row(None, build_modern_setting_tree(node))
            if self.modern_tree.topLevelItemCount() == 0:
                self.settings_empty.show()
        elif kind == "classic":
            self.classic_notice.show()
            self.classic_table.show()
            properties = settings.get("properties", [])
            self.classic_table.setRowCount(len(properties))
            for row_index, prop in enumerate(properties):
                if not isinstance(prop, dict):
                    continue
                path = str(prop.get("propertyPath", ""))
                label = humanize_property_path(path)
                value = format_value(prop.get("rawValue"))
                name_item = QTableWidgetItem(label)
                name_item.setToolTip(path)
                self.classic_table.setItem(row_index, 0, name_item)
                self.classic_table.setItem(row_index, 1, QTableWidgetItem(value))
            if not properties:
                self.settings_empty.show()
        elif kind == "admx":
            self.admx_table.show()
            admx_settings = settings.get("settings", [])
            if (
                not policy.coverage.semantic_hash_eligible
                or policy.coverage.presentation_values in {"partial", "error"}
            ):
                self.admx_notice.setText(
                    "ADMX presentation coverage is incomplete. Configured state may not be fully known."
                )
                self.admx_notice.setStyleSheet(f"color:{COLORS['amber']};")
                self.admx_notice.show()
            self.admx_table.setRowCount(len(admx_settings))
            for row_index, setting in enumerate(admx_settings):
                if not isinstance(setting, dict):
                    continue
                presentation = setting.get("presentation") if isinstance(setting.get("presentation"), dict) else {}
                label = str(presentation.get("displayName") or setting.get("definitionId", ""))
                enabled = setting.get("enabled")
                state = "Enabled" if enabled is True else "Disabled" if enabled is False else "—"
                values = setting.get("presentationValues") or []
                value_text = format_value(values)
                self.admx_table.setItem(row_index, 0, QTableWidgetItem(label))
                self.admx_table.setItem(row_index, 1, QTableWidgetItem(state))
                item = QTableWidgetItem(value_text)
                item.setToolTip(format_value(values))
                self.admx_table.setItem(row_index, 2, item)
            if not admx_settings:
                self.settings_empty.show()
        else:
            self.settings_empty.setText("Unsupported or unknown policy settings shape.")
            self.settings_empty.show()

    def _add_modern_tree_row(self, parent: QTreeWidgetItem | None, row: Any) -> None:
        item = QTreeWidgetItem([row.label, row.value, row.kind])
        if row.tooltip:
            item.setToolTip(1, row.tooltip)
        if row.warning:
            item.setToolTip(0, row.warning)
        if parent is None:
            self.modern_tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in row.children:
            self._add_modern_tree_row(item, child)

    def _render_assignments(self, policy: NormalizedPolicy) -> None:
        assignments = policy.assignments
        self.assignments_table.setRowCount(len(assignments))
        filters_by_id = self._model.filter_by_id if self._model else {}
        captured_at = None
        if self._model and self._model.selected_snapshot:
            captured_at = _parse_captured_at_utc(self._model.selected_snapshot.captured_at_utc)
        for row_index, assignment in enumerate(assignments):
            target = assignment_target_label(assignment.target_kind)
            group = "—"
            if assignment.target_kind in {"include_group", "exclude_group"}:
                group_id = assignment.group_id or "—"
                if (
                    group_id != "—"
                    and captured_at is not None
                    and self._report_dir is not None
                ):
                    group = resolve_group_display_name(
                        self._report_dir,
                        group_id,
                        captured_at,
                    )
                else:
                    group = group_id
            filter_name, _ = resolve_filter_presentation(assignment.filter_id, filters_by_id)
            mode = filter_mode_label(assignment.filter_type)
            group_item = QTableWidgetItem(group)
            if assignment.group_id and group != assignment.group_id:
                group_item.setToolTip(assignment.group_id)
            self.assignments_table.setItem(row_index, 0, QTableWidgetItem(target))
            self.assignments_table.setItem(row_index, 1, group_item)
            self.assignments_table.setItem(row_index, 2, QTableWidgetItem(filter_name))
            self.assignments_table.setItem(row_index, 3, QTableWidgetItem(mode))
        self.assignments_empty.setVisible(not assignments)

    def _render_changes(self, policy_key: str, diff: Any) -> None:
        self.changes_notice.hide()
        self.changes_empty.hide()
        self.changes_table.setRowCount(0)

        if self._model is None:
            return
        if self._model.comparison_error:
            self.changes_notice.setText(self._model.comparison_error)
            self.changes_notice.show()
            return
        if not self._model.previous_snapshot:
            self.changes_empty.setText("No earlier policy snapshot exists for comparison.")
            self.changes_empty.show()
            return

        if diff is None:
            self.changes_empty.setText("No comparison data for this policy.")
            self.changes_empty.show()
            return

        if diff.state == "indeterminate":
            reasons = ", ".join(item.reason for item in diff.suppressions) or "incomplete coverage"
            self.changes_notice.setText(
                "Some changes could not be determined because one snapshot has incomplete coverage. "
                f"({reasons})"
            )
            self.changes_notice.setStyleSheet(f"color:{COLORS['amber']};")
            self.changes_notice.show()

        events = policy_events(policy_key, self._model.comparison)
        if not events and diff.state == "unchanged":
            self.changes_empty.setText("No semantic changes since the previous snapshot.")
            self.changes_empty.show()
            return

        self.changes_table.setRowCount(len(events))
        for row_index, event in enumerate(events):
            label = event_type_label(event.event_type)
            before = format_value(event.before)
            after = format_value(event.after)
            self.changes_table.setItem(row_index, 0, QTableWidgetItem(label))
            self.changes_table.setItem(row_index, 1, QTableWidgetItem(before))
            self.changes_table.setItem(row_index, 2, QTableWidgetItem(after))
