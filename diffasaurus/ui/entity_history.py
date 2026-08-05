from __future__ import annotations

import threading
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.entity.history import build_entity_period_changes
from diffasaurus.core.entity.registry import ADAPTERS_BY_FAMILY
from diffasaurus.core.entity.resolution import EntityResolver, build_entity_resolver
from diffasaurus.core.entity.types import EntityChangeEvent, EntityIndexStats, EntityRecord, SourcedProperty
from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.ui.entity_search import ENTITY_TYPE_LABELS, EntitySelectorPanel
from diffasaurus.ui.period_selector import PeriodSelector
from diffasaurus.ui.report_runner import family_display_name

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

CHANGES_TABLE_MIN_HEIGHT = 220
SPLITTER_CARD_STRETCH = 45
SPLITTER_CHANGES_STRETCH = 55
DEFAULT_SPLITTER_SIZES = (360, 440)


class ElideTooltipDelegate(QStyledItemDelegate):
    def initStyleOption(self, option: QStyleOptionViewItem, index):
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideRight


def _prominent_property_names(family: str) -> tuple[str, ...]:
    adapter = ADAPTERS_BY_FAMILY.get(family)
    if adapter and adapter.card_columns:
        return adapter.card_columns
    return ()


def _ordered_properties(
    family: str,
    properties: list[SourcedProperty],
) -> tuple[list[SourcedProperty], list[SourcedProperty]]:
    prominent_names = _prominent_property_names(family)
    if not prominent_names:
        compact = properties[:6]
        return compact, properties[len(compact) :]

    by_name = {prop.name: prop for prop in properties}
    prominent: list[SourcedProperty] = []
    for name in prominent_names:
        prop = by_name.get(name)
        if prop is not None:
            prominent.append(prop)
    prominent_set = {prop.name for prop in prominent}
    additional = [prop for prop in properties if prop.name not in prominent_set]
    additional.sort(key=lambda item: item.name.lower())
    return prominent, additional


def _table_item(value: str) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    item.setToolTip(value)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class FamilyPropertySection(QFrame):
    def __init__(
        self,
        family: str,
        properties: list[SourcedProperty],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("familyPropertySection")
        self._family = family
        self._expanded = False
        self._prominent, self._additional = _ordered_properties(family, properties)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(6)

        header_row = QHBoxLayout()
        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle_button.setStyleSheet(
            f"QToolButton {{ color: {COLORS['teal']}; border: none; font-weight: 700; }}"
        )
        self.toggle_button.clicked.connect(self._toggle_section)
        heading = QLabel(family_display_name(family))
        heading.setStyleSheet(
            f"color: {COLORS['teal']}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        count_label = QLabel(f"{len(properties)} properties")
        count_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 10px;")
        header_row.addWidget(self.toggle_button)
        header_row.addWidget(heading)
        header_row.addStretch()
        header_row.addWidget(count_label)
        shell.addLayout(header_row)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        self.table = QTableWidget(0, 2)
        self.table.setObjectName("familyPropertyTable")
        self.table.setHorizontalHeaderLabels(("Property", "Value"))
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.setItemDelegate(ElideTooltipDelegate(self.table))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        content_layout.addWidget(self.table)

        self.show_all_button = QPushButton()
        self.show_all_button.setObjectName("secondaryButton")
        self.show_all_button.setFlat(True)
        self.show_all_button.clicked.connect(self._toggle_show_all)
        content_layout.addWidget(self.show_all_button)
        shell.addWidget(self.content)

        self._populate_rows()
        self._update_show_all_button()

    def _toggle_section(self, checked: bool) -> None:
        self.content.setVisible(checked)
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    def _toggle_show_all(self) -> None:
        self._expanded = not self._expanded
        self._populate_rows()
        self._update_show_all_button()

    def _update_show_all_button(self) -> None:
        hidden_count = len(self._additional)
        if hidden_count == 0:
            self.show_all_button.hide()
            return
        if self._expanded:
            self.show_all_button.setText("Show fewer properties")
        else:
            self.show_all_button.setText(f"Show all properties ({hidden_count} more)")
        self.show_all_button.show()

    def _populate_rows(self) -> None:
        visible = list(self._prominent)
        if self._expanded:
            visible.extend(self._additional)
        self.table.setRowCount(len(visible))
        for row, prop in enumerate(visible):
            name_item = _table_item(prop.name)
            value_item = _table_item(prop.value)
            value_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            observed = prop.observed_at.strftime("%d %b %Y")
            name_item.setToolTip(f"{prop.name}\nObserved {observed} in {family_display_name(self._family)}")
            value_item.setToolTip(
                f"{prop.value}\nObserved {observed} in {family_display_name(self._family)}"
            )
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, value_item)
        self.table.resizeRowsToContents()
        table_height = self.table.horizontalHeader().height() + sum(
            self.table.rowHeight(row) for row in range(self.table.rowCount())
        )
        self.table.setMinimumHeight(min(max(table_height + 6, 48), 320))
        self.table.setMaximumHeight(min(max(table_height + 6, 48), 320))


class EntityHistoryPage(QWidget):
    entity_selected = pyqtSignal(object)
    period_changed = pyqtSignal(object, str)
    refresh_requested = pyqtSignal()
    view_at_date_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resolver: EntityResolver | None = None
        self._selected: EntityRecord | None = None
        self._period_changes = None
        self._family_sections: list[FamilyPropertySection] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.entity_selector = EntitySelectorPanel()
        controls.addWidget(self.entity_selector, 2)

        self.period_selector = PeriodSelector()
        controls.addWidget(self.period_selector)

        refresh_box = QVBoxLayout()
        refresh_box.setSpacing(4)
        refresh_spacer = QLabel(" ")
        refresh_spacer.setObjectName("fieldLabel")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryButton")
        refresh_box.addWidget(refresh_spacer)
        refresh_box.addWidget(self.refresh_button)
        controls.addLayout(refresh_box)
        layout.addLayout(controls)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setObjectName("entityHistorySplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)

        self.card_panel = QFrame()
        self.card_panel.setObjectName("entityCard")
        card_panel_layout = QVBoxLayout(self.card_panel)
        card_panel_layout.setContentsMargins(16, 14, 16, 12)
        card_panel_layout.setSpacing(10)

        card_header = QHBoxLayout()
        self.card_title = QLabel("Search for an entity")
        self.card_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.view_at_date_button = QPushButton("View at date")
        self.view_at_date_button.setObjectName("secondaryButton")
        self.view_at_date_button.setEnabled(False)
        card_header.addWidget(self.card_title, 1)
        card_header.addWidget(self.view_at_date_button)
        self.card_subtitle = QLabel("")
        self.card_subtitle.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        self.card_banner = QLabel("")
        self.card_banner.setWordWrap(True)
        self.card_banner.setStyleSheet(
            f"color: {COLORS['amber']}; background: {COLORS['surface2']}; "
            "padding: 8px 10px; border-radius: 6px; font-size: 12px;"
        )
        self.card_banner.hide()

        meta_grid = QGridLayout()
        meta_grid.setHorizontalSpacing(18)
        meta_grid.setVerticalSpacing(4)
        self.card_first_seen = QLabel("—")
        self.card_last_seen = QLabel("—")
        self.card_presence = QLabel("—")
        self.card_family_count = QLabel("—")
        for label in (
            self.card_first_seen,
            self.card_last_seen,
            self.card_presence,
            self.card_family_count,
        ):
            label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        meta_grid.addWidget(self._meta_caption("FIRST SEEN"), 0, 0)
        meta_grid.addWidget(self.card_first_seen, 1, 0)
        meta_grid.addWidget(self._meta_caption("LAST SEEN"), 0, 1)
        meta_grid.addWidget(self.card_last_seen, 1, 1)
        meta_grid.addWidget(self._meta_caption("LATEST SNAPSHOTS"), 0, 2)
        meta_grid.addWidget(self.card_presence, 1, 2)
        meta_grid.addWidget(self._meta_caption("SOURCE FAMILIES"), 0, 3)
        meta_grid.addWidget(self.card_family_count, 1, 3)

        self.properties_scroll = QScrollArea()
        self.properties_scroll.setObjectName("entityPropertiesScroll")
        self.properties_scroll.setWidgetResizable(True)
        self.properties_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.properties_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.properties_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.properties_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.properties_host = QWidget()
        self.properties_layout = QVBoxLayout(self.properties_host)
        self.properties_layout.setContentsMargins(0, 0, 0, 0)
        self.properties_layout.setSpacing(12)
        self.properties_layout.addStretch()
        self.properties_scroll.setWidget(self.properties_host)

        card_panel_layout.addLayout(card_header)
        card_panel_layout.addWidget(self.card_subtitle)
        card_panel_layout.addWidget(self.card_banner)
        card_panel_layout.addLayout(meta_grid)
        card_panel_layout.addWidget(self.properties_scroll, 1)

        self.changes_panel = QWidget()
        changes_panel_layout = QVBoxLayout(self.changes_panel)
        changes_panel_layout.setContentsMargins(0, 4, 0, 0)
        changes_panel_layout.setSpacing(6)
        self.changes_title = QLabel("Changes during period")
        self.changes_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.changes_caption = QLabel("")
        self.changes_caption.setWordWrap(True)
        self.changes_caption.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        changes_panel_layout.addWidget(self.changes_title)
        changes_panel_layout.addWidget(self.changes_caption)

        self.changes_table = QTableWidget(0, 8)
        self.changes_table.setObjectName("entityChangesTable")
        self.changes_table.setHorizontalHeaderLabels(
            ("Latest", "Change", "Report", "Scope", "Property", "Before", "After", "Baseline")
        )
        self.changes_table.setAlternatingRowColors(True)
        self.changes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.changes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.changes_table.verticalHeader().setVisible(False)
        self.changes_table.setShowGrid(False)
        self.changes_table.setWordWrap(False)
        self.changes_table.setMinimumHeight(CHANGES_TABLE_MIN_HEIGHT)
        self.changes_table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.changes_table.setItemDelegate(ElideTooltipDelegate(self.changes_table))
        header = self.changes_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.resizeSection(0, 120)
        header.resizeSection(1, 84)
        header.resizeSection(2, 180)
        header.resizeSection(3, 140)
        header.resizeSection(4, 120)
        header.resizeSection(5, 160)
        header.resizeSection(6, 160)
        header.resizeSection(7, 120)
        self.changes_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.changes_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        changes_panel_layout.addWidget(self.changes_table, 1)

        self.splitter.addWidget(self.card_panel)
        self.splitter.addWidget(self.changes_panel)
        self.splitter.setStretchFactor(0, SPLITTER_CARD_STRETCH)
        self.splitter.setStretchFactor(1, SPLITTER_CHANGES_STRETCH)
        self.splitter.setSizes(list(DEFAULT_SPLITTER_SIZES))
        layout.addWidget(self.splitter, 1)

        self.period_selector.period_changed.connect(self._emit_period_changed)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.entity_selector.entity_selected.connect(self._select_entity)
        self.entity_selector.selection_cleared.connect(self._clear_entity_selection)
        self.view_at_date_button.clicked.connect(self._emit_view_at_date)

        self.type_combo = self.entity_selector.type_combo
        self.search_input = self.entity_selector.search_input
        self.disambiguation = self.entity_selector.disambiguation
        self.status_label = self.entity_selector.status_label

    @staticmethod
    def _meta_caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 9px; font-weight: 700; letter-spacing: 0.8px;"
        )
        return label

    def current_entity_type(self):
        return self.entity_selector.current_entity_type()

    def current_period(self) -> tuple[timedelta, str]:
        return self.period_selector.current_period()

    def _emit_period_changed(self):
        period, label = self.current_period()
        self.period_changed.emit(period, label)
        if self._selected:
            self._load_period_changes(self._selected)

    def show_sync_progress(self, detail: str) -> None:
        self.entity_selector.show_sync_progress(detail)

    def set_repository(self, repository) -> None:
        from diffasaurus.core.entity.index_repository import EntityIndexRepository

        assert isinstance(repository, EntityIndexRepository)
        self._resolver = None
        self._selected = None
        self.entity_selector.set_repository(repository)
        self._clear_card()
        self._clear_changes()
        self.view_at_date_button.setEnabled(False)

    def set_resolver(self, resolver: EntityResolver) -> None:
        self._resolver = resolver
        self._selected = None
        self.entity_selector.set_resolver(resolver)
        self._clear_card()
        self._clear_changes()
        self.view_at_date_button.setEnabled(False)

    def show_indexing(self) -> None:
        self.entity_selector.show_indexing()
        self._clear_card()
        self._clear_changes()
        self.view_at_date_button.setEnabled(False)

    def show_index_progress(self, detail: str) -> None:
        self.entity_selector.show_index_progress(detail)

    def clear_index_state(self) -> None:
        self.entity_selector.clear_index_state()
        self._clear_card()
        self._clear_changes()
        self.view_at_date_button.setEnabled(False)

    def show_index_error(self, message: str) -> None:
        self.entity_selector.show_index_error(message)
        self._clear_card()
        self._clear_changes()
        self.view_at_date_button.setEnabled(False)

    def _clear_entity_selection(self) -> None:
        self._selected = None
        self._clear_card()
        self._clear_changes()
        self.view_at_date_button.setEnabled(False)

    def _select_entity(self, record: EntityRecord) -> None:
        self._selected = record
        self.entity_selected.emit(record)
        self._render_card(record)
        self._load_period_changes(record)
        self.view_at_date_button.setEnabled(True)

    def _emit_view_at_date(self) -> None:
        if self._selected:
            self.view_at_date_requested.emit(self._selected)

    def _clear_property_sections(self) -> None:
        while self.properties_layout.count():
            item = self.properties_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._family_sections.clear()

    def _render_card(self, record: EntityRecord) -> None:
        self.card_title.setText(record.display_name)
        self.card_subtitle.setText(
            f"{ENTITY_TYPE_LABELS[record.key.entity_type]} · {record.key.primary_id}"
        )
        if not record.present_in_latest:
            self.card_banner.setText(
                "No longer present in the latest contributing snapshots."
            )
            self.card_banner.show()
        else:
            self.card_banner.hide()

        self.card_first_seen.setText(
            record.first_seen.strftime("%d %b %Y %H:%M") if record.first_seen else "—"
        )
        self.card_last_seen.setText(
            record.last_seen.strftime("%d %b %Y %H:%M") if record.last_seen else "—"
        )
        if record.present_in_latest:
            self.card_presence.setText("Present")
            self.card_presence.setStyleSheet(f"color: {COLORS['green']}; font-size: 11px;")
        else:
            self.card_presence.setText("Not present")
            self.card_presence.setStyleSheet(f"color: {COLORS['amber']}; font-size: 11px;")
        self.card_family_count.setText(str(len(record.source_families)))

        self._clear_property_sections()
        for family in sorted(record.properties_by_family):
            section = FamilyPropertySection(family, record.properties_by_family[family])
            self._family_sections.append(section)
            self.properties_layout.addWidget(section)
        self.properties_layout.addStretch()

    def _clear_card(self) -> None:
        self.card_title.setText("Search for an entity")
        self.card_subtitle.setText("")
        self.card_banner.hide()
        self.card_first_seen.setText("—")
        self.card_last_seen.setText("—")
        self.card_presence.setText("—")
        self.card_presence.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        self.card_family_count.setText("—")
        self._clear_property_sections()
        self.properties_layout.addStretch()

    def apply_period_changes(self, changes) -> None:
        self._period_changes = changes
        covered = (
            f"Cutoff {changes.covered_from.strftime('%d %b %Y %H:%M')} → "
            f"reference {changes.covered_to.strftime('%d %b %Y %H:%M')}"
        )
        self.changes_caption.setText(covered)
        self.changes_table.setRowCount(0)
        for event in changes.events:
            self._append_change_row(event)
        for family, reason in changes.family_notes:
            row = self.changes_table.rowCount()
            self.changes_table.insertRow(row)
            note = _table_item(f"{family_display_name(family)}: {reason}")
            note.setForeground(QColor(COLORS["amber"]))
            self.changes_table.setItem(row, 2, note)
            self.changes_table.setSpan(row, 2, 1, 6)

    def _append_change_row(self, event: EntityChangeEvent) -> None:
        row = self.changes_table.rowCount()
        self.changes_table.insertRow(row)
        values = (
            event.latest_at.strftime("%d %b %Y %H:%M"),
            event.change_type.title(),
            family_display_name(event.family),
            event.row_scope,
            event.property,
            event.before,
            event.after,
            event.baseline_at.strftime("%d %b %Y %H:%M"),
        )
        color = {
            "added": COLORS["green"],
            "removed": COLORS["red"],
            "modified": COLORS["amber"],
        }.get(event.change_type, COLORS["text"])
        for column, value in enumerate(values):
            item = _table_item(value)
            if column == 1:
                item.setForeground(QColor(color))
            self.changes_table.setItem(row, column, item)

    def _clear_changes(self) -> None:
        self.changes_caption.setText("")
        self.changes_table.setRowCount(0)
        self._period_changes = None

    def _load_period_changes(self, record: EntityRecord) -> None:
        self._clear_changes()
        self.changes_caption.setText("Loading changes…")

    @staticmethod
    def build_resolver(
        families: dict[str, list[ReportSnapshot]],
        cancelled: threading.Event | None = None,
        stats: EntityIndexStats | None = None,
    ) -> EntityResolver:
        return build_entity_resolver(families, cancelled=cancelled, stats=stats)

    @staticmethod
    def compute_period_changes(
        record: EntityRecord,
        families: dict[str, list[ReportSnapshot]],
        period: timedelta,
        reference: datetime | None = None,
    ):
        return build_entity_period_changes(record.key, families, period, reference)
