from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QLabel,
    QWidget,
)

from diffasaurus.core.entity.pit_presentation import PointInTimeSourceDetails
from diffasaurus.core.entity.registry import adapters_for_type
from diffasaurus.core.entity.types import EntityType, ScopedRelationship
from diffasaurus.ui.entity_history import FamilyPropertySection, _table_item
from diffasaurus.ui.point_in_time_styles import PIT_COLORS, format_gap
from diffasaurus.ui.report_runner import family_display_name


class RelationshipSection(QFrame):
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
            f"color: {PIT_COLORS['teal']}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        layout.addWidget(heading)
        observed = relationship.observed_at.strftime("%d %b %Y · %H:%M")
        caption = QLabel(f"Observed {observed}")
        caption.setStyleSheet(f"color: {PIT_COLORS['muted']}; font-size: 10px;")
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


class PointInTimeSourceDetailsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

        coverage_title = QLabel("Coverage by report family")
        coverage_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self._layout.addWidget(coverage_title)

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
        self._layout.addWidget(self.coverage_table)

        self.sections_host = QVBoxLayout()
        self.sections_host.setSpacing(12)
        self._layout.addLayout(self.sections_host)

    def set_details(self, entity_type: EntityType, details: PointInTimeSourceDetails | None) -> None:
        self._populate_coverage(details)
        self._populate_sections(entity_type, details)

    def _populate_coverage(self, details: PointInTimeSourceDetails | None) -> None:
        self.coverage_table.setRowCount(0)
        if details is None:
            return
        for item in details.coverage:
            row = self.coverage_table.rowCount()
            self.coverage_table.insertRow(row)
            status_label = item.status.replace("_", " ").title()
            snapshot_label = (
                item.snapshot_at.strftime("%d %b %Y · %H:%M") if item.snapshot_at else "—"
            )
            gap_label = format_gap(item.gap) if item.gap and item.gap.total_seconds() > 0 else "—"
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
                        "snapshot_used": PIT_COLORS["green"],
                        "entity_absent": PIT_COLORS["red"],
                        "no_snapshot": PIT_COLORS["muted"],
                    }.get(item.status, PIT_COLORS["text"])
                    cell.setForeground(QColor(color))
                self.coverage_table.setItem(row, column, cell)

    def _clear_sections(self) -> None:
        while self.sections_host.count():
            item = self.sections_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _populate_sections(self, entity_type: EntityType, details: PointInTimeSourceDetails | None) -> None:
        self._clear_sections()
        if details is None:
            return
        for adapter in adapters_for_type(entity_type):
            family = adapter.family
            scalar = list(details.scalar_properties_by_family.get(family, ()))
            relationships = list(details.relationships_by_family.get(family, ()))
            if not scalar and not relationships:
                continue
            if scalar:
                section = FamilyPropertySection(family, scalar)
                self.sections_host.addWidget(section)
            for relationship in relationships:
                self.sections_host.addWidget(RelationshipSection(family, relationship))
