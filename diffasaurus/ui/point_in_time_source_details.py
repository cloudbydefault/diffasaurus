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

from diffasaurus.core.entity.pit_auth_methods import AUTH_METHODS_FAMILY
from diffasaurus.core.entity.pit_presentation import PointInTimeSourceDetails
from diffasaurus.core.entity.registry import adapters_for_type
from diffasaurus.core.entity.types import EntityType, ScopedRelationship, SourcedProperty
from diffasaurus.ui.entity_history import FamilyPropertySection, _table_item
from diffasaurus.ui.point_in_time_styles import PIT_COLORS, format_gap
from diffasaurus.ui.report_runner import family_display_name


class ManagedDevicesDetailsSection(QFrame):
    def __init__(self, audit, parent=None):
        super().__init__(parent)
        self.setObjectName("managedDevicesDetailsSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("Managed devices · enrichment")
        heading.setStyleSheet(
            f"color: {PIT_COLORS['teal']}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        layout.addWidget(heading)

        summary_rows = [
            ("Coverage", audit.coverage_status.replace("_", " ")),
            (
                "Managed snapshot",
                audit.snapshot_at.strftime("%d %b %Y · %H:%M") if audit.snapshot_at else "—",
            ),
            ("Managed source file", audit.source_relative_path or "—"),
            ("Resolved devices", str(audit.resolved_device_count)),
            ("Unresolved observations", str(audit.unresolved_observation_count)),
            (
                "Autopilot snapshot",
                audit.autopilot_snapshot_at.strftime("%d %b %Y · %H:%M")
                if audit.autopilot_snapshot_at
                else "—",
            ),
            ("Autopilot source file", audit.autopilot_source_relative_path or "—"),
            (
                "Autopilot coverage",
                audit.autopilot_coverage_status.replace("_", " ") if audit.autopilot_coverage_status else "—",
            ),
        ]
        if audit.enrichment_error:
            summary_rows.append(("Enrichment error", audit.enrichment_error))

        summary = QTableWidget(len(summary_rows), 2)
        summary.setHorizontalHeaderLabels(("Field", "Value"))
        summary.setAlternatingRowColors(True)
        summary.verticalHeader().setVisible(False)
        summary.setShowGrid(False)
        summary.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        summary.horizontalHeader().setStretchLastSection(True)
        for row, (label, value) in enumerate(summary_rows):
            summary.setItem(row, 0, _table_item(label))
            summary.setItem(row, 1, _table_item(value))
        summary.setMinimumHeight(min(max(summary.rowHeight(0) * len(summary_rows) + 40, 80), 220))
        layout.addWidget(summary)

        for device in audit.devices:
            device_heading = QLabel(f"Device · {device.stable_key}")
            device_heading.setStyleSheet(
                f"color: {PIT_COLORS['muted']}; font-size: 10px; font-weight: 700;"
            )
            layout.addWidget(device_heading)

            device_rows = [
                ("Link kind", device.link_kind or "—"),
                ("Resolution", device.resolution_status),
                ("Normalized link", device.normalized_link_value or "—"),
                ("Diagnostic", device.diagnostic or "—"),
            ]
            if device.candidate_user_ids:
                device_rows.append(("Candidate users", ", ".join(device.candidate_user_ids)))
            if device.managed_provenance_observations:
                obs = device.managed_provenance_observations[0]
                device_rows.append(
                    (
                        "Managed snapshot",
                        obs.snapshot_at.strftime("%d %b %Y · %H:%M") if obs.snapshot_at else "—",
                    )
                )

            device_table = QTableWidget(len(device_rows), 2)
            device_table.setHorizontalHeaderLabels(("Property", "Value"))
            device_table.setAlternatingRowColors(True)
            device_table.verticalHeader().setVisible(False)
            device_table.setShowGrid(False)
            device_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            device_table.horizontalHeader().setStretchLastSection(True)
            for row, (label, value) in enumerate(device_rows):
                device_table.setItem(row, 0, _table_item(label))
                device_table.setItem(row, 1, _table_item(value))
            device_table.setMinimumHeight(
                min(max(device_table.rowHeight(0) * len(device_rows) + 40, 60), 180)
            )
            layout.addWidget(device_table)

            if device.properties:
                props_table = QTableWidget(len(device.properties), 2)
                props_table.setHorizontalHeaderLabels(("Managed property", "Value"))
                props_table.setAlternatingRowColors(True)
                props_table.verticalHeader().setVisible(False)
                props_table.setShowGrid(False)
                props_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                props_table.horizontalHeader().setStretchLastSection(True)
                for row, prop in enumerate(device.properties):
                    props_table.setItem(row, 0, _table_item(prop.name))
                    props_table.setItem(row, 1, _table_item(prop.value))
                props_table.setMinimumHeight(
                    min(max(props_table.rowHeight(0) * len(device.properties) + 40, 60), 240)
                )
                layout.addWidget(props_table)

            if device.autopilot is not None:
                ap = device.autopilot
                ap_heading = QLabel("Autopilot audit")
                ap_heading.setStyleSheet(
                    f"color: {PIT_COLORS['muted']}; font-size: 10px; font-weight: 700;"
                )
                layout.addWidget(ap_heading)
                ap_rows = [
                    ("Status", ap.status),
                    (
                        "Snapshot",
                        ap.snapshot_at.strftime("%d %b %Y · %H:%M") if ap.snapshot_at else "—",
                    ),
                    ("Source file", ap.source_relative_path or "—"),
                    ("Diagnostic", ap.diagnostic or "—"),
                    ("Matching keys", ", ".join(ap.matching_keys) if ap.matching_keys else "—"),
                ]
                for key, value in ap.normalized_values.items():
                    ap_rows.append((f"Normalized {key}", value))
                for key, count in ap.candidate_counts.items():
                    if count:
                        ap_rows.append((f"{key} candidates", str(count)))
                ap_table = QTableWidget(len(ap_rows), 2)
                ap_table.setHorizontalHeaderLabels(("Property", "Value"))
                ap_table.setAlternatingRowColors(True)
                ap_table.verticalHeader().setVisible(False)
                ap_table.setShowGrid(False)
                ap_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                ap_table.horizontalHeader().setStretchLastSection(True)
                for row, (label, value) in enumerate(ap_rows):
                    ap_table.setItem(row, 0, _table_item(label))
                    ap_table.setItem(row, 1, _table_item(value))
                ap_table.setMinimumHeight(
                    min(max(ap_table.rowHeight(0) * len(ap_rows) + 40, 60), 200)
                )
                layout.addWidget(ap_table)
                if ap.properties:
                    ap_props = QTableWidget(len(ap.properties), 2)
                    ap_props.setHorizontalHeaderLabels(("Autopilot property", "Value"))
                    ap_props.setAlternatingRowColors(True)
                    ap_props.verticalHeader().setVisible(False)
                    ap_props.setShowGrid(False)
                    ap_props.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                    ap_props.horizontalHeader().setStretchLastSection(True)
                    for row, prop in enumerate(ap.properties):
                        ap_props.setItem(row, 0, _table_item(prop.name))
                        ap_props.setItem(row, 1, _table_item(prop.value))
                    ap_props.setMinimumHeight(
                        min(max(ap_props.rowHeight(0) * len(ap.properties) + 40, 60), 240)
                    )
                    layout.addWidget(ap_props)


class AuthMethodsDetailsSection(QFrame):
    def __init__(self, details: PointInTimeSourceDetails, parent=None):
        super().__init__(parent)
        self.setObjectName("authMethodsDetailsSection")
        parsed = details.auth_methods
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        heading = QLabel("Authentication methods · parsed")
        heading.setStyleSheet(
            f"color: {PIT_COLORS['teal']}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        layout.addWidget(heading)

        if parsed is None:
            empty = QLabel("No authentication methods enrichment available.")
            empty.setStyleSheet(f"color: {PIT_COLORS['muted']}; font-size: 11px;")
            layout.addWidget(empty)
            return

        summary_rows = [
            ("Coverage", parsed.coverage.replace("_", " ")),
            ("Merged methods", ", ".join(parsed.methods) if parsed.methods else "—"),
            ("Has conflict", "Yes" if parsed.has_conflict else "No"),
        ]
        source_info = details.family_source_info.get(AUTH_METHODS_FAMILY)
        if source_info is not None:
            relative_path, raw_family = source_info
            if relative_path:
                summary_rows.append(("Source file", relative_path))
            if raw_family:
                summary_rows.append(("Detected report family", raw_family))
        summary = QTableWidget(len(summary_rows), 2)
        summary.setHorizontalHeaderLabels(("Field", "Value"))
        summary.setAlternatingRowColors(True)
        summary.verticalHeader().setVisible(False)
        summary.setShowGrid(False)
        summary.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        summary.horizontalHeader().setStretchLastSection(True)
        for row, (label, value) in enumerate(summary_rows):
            summary.setItem(row, 0, _table_item(label))
            summary.setItem(row, 1, _table_item(value))
        summary.setMinimumHeight(min(max(summary.rowHeight(0) * len(summary_rows) + 40, 60), 120))
        layout.addWidget(summary)

        for source in parsed.sources:
            source_heading = QLabel(f"Source · {source.property_name}")
            source_heading.setStyleSheet(
                f"color: {PIT_COLORS['muted']}; font-size: 10px; font-weight: 700;"
            )
            layout.addWidget(source_heading)
            source_rows = [
                ("Raw value", source.raw_value if source.raw_value else "—"),
                ("Parsed methods", ", ".join(source.parsed_methods) if source.parsed_methods else "—"),
            ]
            if source.provenance.observations:
                obs = source.provenance.observations[0]
                observed = (
                    obs.observed_at.strftime("%d %b %Y · %H:%M") if obs.observed_at else "—"
                )
                source_rows.append(("Observed", observed))
                source_rows.append(("Family", obs.family))
            table = QTableWidget(len(source_rows), 2)
            table.setHorizontalHeaderLabels(("Property", "Value"))
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            table.setShowGrid(False)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.horizontalHeader().setStretchLastSection(True)
            for row, (label, value) in enumerate(source_rows):
                table.setItem(row, 0, _table_item(label))
                table.setItem(row, 1, _table_item(value))
            table.setMinimumHeight(min(max(table.rowHeight(0) * len(source_rows) + 40, 60), 160))
            layout.addWidget(table)

        if parsed.conflict is not None:
            conflict_heading = QLabel("Source disagreement")
            conflict_heading.setStyleSheet(
                f"color: {PIT_COLORS['amber']}; font-size: 10px; font-weight: 700;"
            )
            layout.addWidget(conflict_heading)
            conflict_rows = [
                (
                    "Authoritative property",
                    parsed.conflict.authoritative_property,
                ),
                (
                    "Authoritative methods",
                    ", ".join(parsed.conflict.authoritative_methods),
                ),
            ]
            for alternate in parsed.conflict.alternates:
                conflict_rows.append(
                    (
                        f"Alternate · {alternate.property_name}",
                        ", ".join(alternate.parsed_methods),
                    )
                )
            conflict_table = QTableWidget(len(conflict_rows), 2)
            conflict_table.setHorizontalHeaderLabels(("Property", "Value"))
            conflict_table.setAlternatingRowColors(True)
            conflict_table.verticalHeader().setVisible(False)
            conflict_table.setShowGrid(False)
            conflict_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            conflict_table.horizontalHeader().setStretchLastSection(True)
            for row, (label, value) in enumerate(conflict_rows):
                conflict_table.setItem(row, 0, _table_item(label))
                conflict_table.setItem(row, 1, _table_item(value))
            conflict_table.setMinimumHeight(
                min(max(conflict_table.rowHeight(0) * len(conflict_rows) + 40, 60), 200)
            )
            layout.addWidget(conflict_table)


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
        if details.auth_methods is not None:
            self.sections_host.addWidget(AuthMethodsDetailsSection(details))
        if details.managed_devices_audit is not None:
            self.sections_host.addWidget(ManagedDevicesDetailsSection(details.managed_devices_audit))
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
