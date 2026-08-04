from __future__ import annotations

from datetime import datetime

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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.entity.registry import adapters_for_type
from diffasaurus.core.entity.resolution import EntityResolver, build_entity_resolver
from diffasaurus.core.entity.types import EntityPresenceStatus, EntityRecord, EntityState, ScopedRelationship
from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.ui.datetime_selector import TargetDateTimeSelector
from diffasaurus.ui.entity_history import FamilyPropertySection, _table_item
from diffasaurus.ui.entity_search import ENTITY_TYPE_LABELS, EntitySelectorPanel
from diffasaurus.ui.report_runner import family_display_name

COLORS = {
    "surface2": "#152331",
    "text": "#f2f7fb",
    "muted": "#8295a8",
    "teal": "#8bd450",
    "green": "#4fd1a5",
    "red": "#fb7185",
    "amber": "#f5b942",
}

PRESENCE_LABELS: dict[EntityPresenceStatus, str] = {
    "present": "Present",
    "absent": "Absent",
    "unknown": "Unknown",
    "partial": "Partial",
}

PRESENCE_PARTIAL_COPY = (
    "Absent from available snapshots; primary inventory has no coverage."
)

SPLITTER_LEFT_STRETCH = 42
SPLITTER_RIGHT_STRETCH = 58
DEFAULT_SPLITTER_SIZES = (380, 520)


def _format_gap(gap) -> str:
    if gap is None:
        return "—"
    total_seconds = int(gap.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


class PointInTimePage(QWidget):
    reconstruct_requested = pyqtSignal(object, object)
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resolver: EntityResolver | None = None
        self._selected: EntityRecord | None = None
        self._state: EntityState | None = None
        self._family_sections: list[QWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.entity_selector = EntitySelectorPanel()
        controls.addWidget(self.entity_selector, 2)
        self.datetime_selector = TargetDateTimeSelector()
        controls.addWidget(self.datetime_selector, 1)

        action_box = QVBoxLayout()
        action_box.setSpacing(4)
        action_spacer = QLabel(" ")
        action_spacer.setObjectName("fieldLabel")
        self.reconstruct_button = QPushButton("Reconstruct")
        self.reconstruct_button.setObjectName("primaryButton")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryButton")
        action_box.addWidget(action_spacer)
        action_box.addWidget(self.reconstruct_button)
        action_box.addWidget(self.refresh_button)
        controls.addLayout(action_box)
        layout.addLayout(controls)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("pointInTimeSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self.summary_card = QFrame()
        self.summary_card.setObjectName("entityCard")
        summary_layout = QVBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(16, 14, 16, 12)
        summary_layout.setSpacing(8)
        self.summary_title = QLabel("Select an entity and target time")
        self.summary_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.summary_subtitle = QLabel("")
        self.summary_subtitle.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        self.summary_target = QLabel("—")
        self.summary_presence = QLabel("—")
        self.summary_presence_detail = QLabel("")
        self.summary_presence_detail.setWordWrap(True)
        self.summary_presence_detail.setStyleSheet(f"color: {COLORS['amber']}; font-size: 11px;")
        self.summary_presence_detail.hide()
        self.summary_history = QLabel("—")
        self.summary_history.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        self.summary_families = QLabel("—")
        self.summary_families.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")

        meta = QGridLayout()
        meta.setHorizontalSpacing(18)
        meta.setVerticalSpacing(4)
        meta.addWidget(self._meta_caption("REQUESTED TARGET"), 0, 0)
        meta.addWidget(self.summary_target, 1, 0)
        meta.addWidget(self._meta_caption("PRESENCE AT TARGET"), 0, 1)
        meta.addWidget(self.summary_presence, 1, 1)
        meta.addWidget(self._meta_caption("HISTORY RANGE"), 0, 2)
        meta.addWidget(self.summary_history, 1, 2)
        meta.addWidget(self._meta_caption("COVERAGE"), 0, 3)
        meta.addWidget(self.summary_families, 1, 3)

        summary_layout.addWidget(self.summary_title)
        summary_layout.addWidget(self.summary_subtitle)
        summary_layout.addLayout(meta)
        summary_layout.addWidget(self.summary_presence_detail)
        left_layout.addWidget(self.summary_card)

        coverage_title = QLabel("Coverage by report family")
        coverage_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        left_layout.addWidget(coverage_title)

        self.coverage_table = QTableWidget(0, 5)
        self.coverage_table.setObjectName("pointInTimeCoverageTable")
        self.coverage_table.setHorizontalHeaderLabels(
            ("Report", "Status", "Snapshot used", "Gap", "Entity present")
        )
        self.coverage_table.setAlternatingRowColors(True)
        self.coverage_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.coverage_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.coverage_table.verticalHeader().setVisible(False)
        self.coverage_table.setShowGrid(False)
        self.coverage_table.setMinimumHeight(180)
        header = self.coverage_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        left_layout.addWidget(self.coverage_table, 1)

        self.right_scroll = QScrollArea()
        self.right_scroll.setObjectName("pointInTimeStateScroll")
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.state_host = QWidget()
        self.state_layout = QVBoxLayout(self.state_host)
        self.state_layout.setContentsMargins(0, 0, 0, 0)
        self.state_layout.setSpacing(12)
        self.state_layout.addStretch()
        self.right_scroll.setWidget(self.state_host)

        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(self.right_scroll)
        self.splitter.setStretchFactor(0, SPLITTER_LEFT_STRETCH)
        self.splitter.setStretchFactor(1, SPLITTER_RIGHT_STRETCH)
        self.splitter.setSizes(list(DEFAULT_SPLITTER_SIZES))
        layout.addWidget(self.splitter, 1)

        self.entity_selector.entity_selected.connect(self._on_entity_selected)
        self.reconstruct_button.clicked.connect(self._request_reconstruct)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)

    @staticmethod
    def _meta_caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 9px; font-weight: 700; letter-spacing: 0.8px;"
        )
        return label

    def set_resolver(self, resolver: EntityResolver) -> None:
        self._resolver = resolver
        self.entity_selector.set_resolver(resolver)
        self._clear_state()

    def show_indexing(self) -> None:
        self.entity_selector.show_indexing()
        self._clear_state()

    def show_index_error(self, message: str) -> None:
        self.entity_selector.show_index_error(message)
        self._clear_state()

    def select_entity(self, record: EntityRecord, target: datetime | None = None) -> None:
        if target is not None:
            self.datetime_selector.set_datetime(target)
        self.entity_selector.select_record(record)

    def apply_state(self, state: EntityState, record: EntityRecord) -> None:
        self._state = state
        self._selected = record
        self.summary_title.setText(record.display_name)
        self.summary_subtitle.setText(
            f"{ENTITY_TYPE_LABELS[record.key.entity_type]} · {record.key.primary_id}"
        )
        self.summary_target.setText(state.as_of.strftime("%d %b %Y · %H:%M"))
        presence = state.presence
        self.summary_presence.setText(PRESENCE_LABELS[presence])
        color = {
            "present": COLORS["green"],
            "absent": COLORS["red"],
            "unknown": COLORS["muted"],
            "partial": COLORS["amber"],
        }.get(presence, COLORS["text"])
        self.summary_presence.setStyleSheet(f"color: {color}; font-size: 11px;")
        if presence == "partial":
            self.summary_presence_detail.setText(PRESENCE_PARTIAL_COPY)
            self.summary_presence_detail.show()
        else:
            self.summary_presence_detail.hide()

        if record.first_seen and record.last_seen:
            self.summary_history.setText(
                f"{record.first_seen.strftime('%d %b %Y · %H:%M')} → "
                f"{record.last_seen.strftime('%d %b %Y · %H:%M')}"
            )
        else:
            self.summary_history.setText("—")

        contributing = sum(1 for item in state.coverage if item.status == "snapshot_used")
        without = sum(
            1
            for item in state.coverage
            if item.status in ("no_snapshot", "entity_absent")
        )
        self.summary_families.setText(
            f"{contributing} contributing · {without} without usable coverage"
        )

        self._populate_coverage(state)
        self._populate_state_sections(state)

    def show_loading(self) -> None:
        self.summary_title.setText("Reconstructing…")
        self._clear_state_sections()

    def _on_entity_selected(self, record: EntityRecord) -> None:
        self._selected = record

    def _request_reconstruct(self) -> None:
        if not self._selected:
            return
        target = self.datetime_selector.current_datetime()
        self.show_loading()
        self.reconstruct_requested.emit(self._selected, target)

    def _populate_coverage(self, state: EntityState) -> None:
        self.coverage_table.setRowCount(0)
        for item in state.coverage:
            row = self.coverage_table.rowCount()
            self.coverage_table.insertRow(row)
            status_label = item.status.replace("_", " ").title()
            snapshot_label = (
                item.snapshot_at.strftime("%d %b %Y · %H:%M") if item.snapshot_at else "—"
            )
            gap_label = _format_gap(item.gap) if item.gap and item.gap.total_seconds() > 0 else "—"
            values = (
                family_display_name(item.family),
                status_label,
                snapshot_label,
                gap_label,
                "Yes" if item.entity_present else "No",
            )
            for column, value in enumerate(values):
                cell = _table_item(value)
                if column == 1:
                    color = {
                        "snapshot_used": COLORS["green"],
                        "entity_absent": COLORS["red"],
                        "no_snapshot": COLORS["muted"],
                    }.get(item.status, COLORS["text"])
                    cell.setForeground(QColor(color))
                self.coverage_table.setItem(row, column, cell)

    def _clear_state_sections(self) -> None:
        while self.state_layout.count():
            item = self.state_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._family_sections.clear()

    def _populate_state_sections(self, state: EntityState) -> None:
        self._clear_state_sections()
        for adapter in adapters_for_type(state.key.entity_type):
            family = adapter.family
            scalar = list(state.scalar_properties_by_family.get(family, ()))
            relationships = list(state.relationships_by_family.get(family, ()))
            if not scalar and not relationships:
                continue
            if scalar:
                section = FamilyPropertySection(family, scalar)
                self._family_sections.append(section)
                self.state_layout.addWidget(section)
            for relationship in relationships:
                rel_section = _RelationshipSection(family, relationship)
                self._family_sections.append(rel_section)
                self.state_layout.addWidget(rel_section)
        self.state_layout.addStretch()

    def _clear_state(self) -> None:
        self._state = None
        self._selected = None
        self.summary_title.setText("Select an entity and target time")
        self.summary_subtitle.setText("")
        self.summary_target.setText("—")
        self.summary_presence.setText("—")
        self.summary_presence.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        self.summary_presence_detail.hide()
        self.summary_history.setText("—")
        self.summary_families.setText("—")
        self.coverage_table.setRowCount(0)
        self._clear_state_sections()
        self.state_layout.addStretch()

    @staticmethod
    def build_resolver(families: dict[str, list[ReportSnapshot]]) -> EntityResolver:
        return build_entity_resolver(families)


class _RelationshipSection(QFrame):
    def __init__(self, family: str, relationship: ScopedRelationship, parent=None):
        super().__init__(parent)
        self.setObjectName("relationshipSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        heading = QLabel(
            f"{family_display_name(family)} · {relationship.row_scope or 'Relationship'}"
        )
        heading.setStyleSheet(
            f"color: {COLORS['teal']}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        layout.addWidget(heading)
        observed = relationship.observed_at.strftime("%d %b %Y · %H:%M")
        caption = QLabel(f"Observed {observed}")
        caption.setStyleSheet(f"color: {COLORS['muted']}; font-size: 10px;")
        layout.addWidget(caption)

        table = QTableWidget(len(relationship.properties), 2)
        table.setHorizontalHeaderLabels(("Property", "Value"))
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        for row, prop in enumerate(relationship.properties):
            table.setItem(row, 0, _table_item(prop.name))
            table.setItem(row, 1, _table_item(prop.value))
        table.setMinimumHeight(min(max(table.rowHeight(0) * len(relationship.properties) + 40, 60), 240))
        layout.addWidget(table)
