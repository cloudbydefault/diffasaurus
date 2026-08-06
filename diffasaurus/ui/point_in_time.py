from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.entity.pit_presentation import (
    build_point_in_time_card,
    PointInTimeReconstructionResult,
)
from diffasaurus.core.entity.resolution import EntityResolver, build_entity_resolver
from diffasaurus.core.entity.types import EntityPresenceStatus, EntityRecord, EntityState
from diffasaurus.core.report_history import ReportSnapshot
from diffasaurus.ui.datetime_selector import TargetDateTimeSelector
from diffasaurus.ui.entity_search import ENTITY_TYPE_LABELS, EntitySelectorPanel
from diffasaurus.ui.point_in_time_card import EntityIdentityCardView
from diffasaurus.ui.point_in_time_source_details import PointInTimeSourceDetailsPanel
from diffasaurus.ui.point_in_time_styles import PIT_COLORS

PRESENCE_LABELS: dict[EntityPresenceStatus, str] = {
    "present": "Present",
    "absent": "Absent",
    "unknown": "Unknown",
    "partial": "Partial",
}

PRESENCE_PARTIAL_COPY = (
    "Absent from available snapshots; primary inventory has no coverage."
)


class PointInTimePage(QWidget):
    reconstruct_requested = pyqtSignal(object, object)
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resolver: EntityResolver | None = None
        self._selected: EntityRecord | None = None
        self._state: EntityState | None = None
        self._source_details_visible = False

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
        self.reconstruct_button.setEnabled(False)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryButton")
        action_box.addWidget(action_spacer)
        action_box.addWidget(self.reconstruct_button)
        action_box.addWidget(self.refresh_button)
        controls.addLayout(action_box)
        layout.addLayout(controls)

        self.summary_card = QFrame()
        self.summary_card.setObjectName("entityCard")
        summary_layout = QVBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(16, 14, 16, 12)
        summary_layout.setSpacing(8)
        self.summary_title = QLabel("Select an entity and target time")
        self.summary_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.summary_subtitle = QLabel("")
        self.summary_subtitle.setStyleSheet(f"color: {PIT_COLORS['muted']}; font-size: 12px;")
        self.summary_target = QLabel("—")
        self.summary_presence = QLabel("—")
        self.summary_presence_detail = QLabel("")
        self.summary_presence_detail.setWordWrap(True)
        self.summary_presence_detail.setStyleSheet(f"color: {PIT_COLORS['amber']}; font-size: 11px;")
        self.summary_presence_detail.hide()
        self.summary_history = QLabel("—")
        self.summary_history.setStyleSheet(f"color: {PIT_COLORS['text']}; font-size: 11px;")
        self.summary_families = QLabel("—")
        self.summary_families.setStyleSheet(f"color: {PIT_COLORS['text']}; font-size: 11px;")

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
        layout.addWidget(self.summary_card)

        self.card_scroll = QScrollArea()
        self.card_scroll.setObjectName("pointInTimeCardScroll")
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.identity_card = EntityIdentityCardView()
        self.card_scroll.setWidget(self.identity_card)
        layout.addWidget(self.card_scroll, 1)

        self.source_details_toggle = QToolButton()
        self.source_details_toggle.setText("Show source details")
        self.source_details_toggle.setCheckable(True)
        self.source_details_toggle.setChecked(False)
        self.source_details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.source_details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.source_details_toggle.clicked.connect(self._toggle_source_details)
        layout.addWidget(self.source_details_toggle)

        self.source_details_panel = PointInTimeSourceDetailsPanel()
        self.source_details_panel.hide()
        layout.addWidget(self.source_details_panel)

        self.entity_selector.entity_selected.connect(self._on_entity_selected)
        self.entity_selector.selection_cleared.connect(self._on_selection_cleared)
        self.reconstruct_button.clicked.connect(self._request_reconstruct)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)

    @staticmethod
    def _meta_caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {PIT_COLORS['muted']}; font-size: 9px; font-weight: 700; letter-spacing: 0.8px;"
        )
        return label

    def show_sync_progress(self, detail: str) -> None:
        self.entity_selector.show_sync_progress(detail)

    def set_repository(self, repository) -> None:
        from diffasaurus.core.entity.index_repository import EntityIndexRepository

        assert isinstance(repository, EntityIndexRepository)
        self._resolver = None
        self.entity_selector.set_repository(repository)
        self._clear_state()

    def set_resolver(self, resolver: EntityResolver) -> None:
        self._resolver = resolver
        self.entity_selector.set_resolver(resolver)
        self._clear_state()

    def show_indexing(self) -> None:
        self.entity_selector.show_indexing()
        self._clear_state()

    def show_index_progress(self, detail: str) -> None:
        self.entity_selector.show_index_progress(detail)

    def clear_index_state(self) -> None:
        self.entity_selector.clear_index_state()
        self._clear_state()

    def show_index_error(self, message: str) -> None:
        self.entity_selector.show_index_error(message)
        self._clear_state()

    def select_entity(self, record: EntityRecord, target: datetime | None = None) -> None:
        if target is not None:
            self.datetime_selector.set_datetime(target)
        self.entity_selector.select_record(record)

    def apply_reconstruction(self, result: PointInTimeReconstructionResult, record: EntityRecord) -> None:
        state = result.state
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
            "present": PIT_COLORS["green"],
            "absent": PIT_COLORS["red"],
            "unknown": PIT_COLORS["muted"],
            "partial": PIT_COLORS["amber"],
        }.get(presence, PIT_COLORS["text"])
        self.summary_presence.setStyleSheet(f"color: {color}; font-size: 11px;")
        if presence == "partial":
            self.summary_presence_detail.setText(PRESENCE_PARTIAL_COPY)
            self.summary_presence_detail.show()
        else:
            self.summary_presence_detail.hide()

        history_range = (record.first_seen, record.last_seen)
        if record.first_seen and record.last_seen:
            self.summary_history.setText(
                f"{record.first_seen.strftime('%d %b %Y · %H:%M')} → "
                f"{record.last_seen.strftime('%d %b %Y · %H:%M')}"
            )
        else:
            self.summary_history.setText("—")

        model = build_point_in_time_card(
            state,
            display_name=record.display_name,
            history_range=history_range,
            enrichment=result.enrichment,
            enrichment_error=result.enrichment_error,
        )
        self.summary_families.setText(model.coverage_summary)
        self.identity_card.set_model(model)
        self.source_details_panel.set_details(state.key.entity_type, model.source_details)

    def apply_state(self, state: EntityState, record: EntityRecord) -> None:
        self.apply_reconstruction(
            PointInTimeReconstructionResult(state=state),
            record,
        )

    def show_loading(self) -> None:
        self.summary_title.setText("Reconstructing…")
        self.identity_card.set_model(None)
        self.source_details_panel.set_details("user", None)

    def _on_entity_selected(self, record: EntityRecord) -> None:
        self._selected = record
        self.reconstruct_button.setEnabled(True)

    def _on_selection_cleared(self) -> None:
        self._selected = None
        self.reconstruct_button.setEnabled(False)

    def _request_reconstruct(self) -> None:
        if not self._selected:
            return
        target = self.datetime_selector.current_datetime()
        self.show_loading()
        self.reconstruct_requested.emit(self._selected, target)

    def _toggle_source_details(self) -> None:
        self._source_details_visible = self.source_details_toggle.isChecked()
        self.source_details_panel.setVisible(self._source_details_visible)
        self.source_details_toggle.setArrowType(
            Qt.ArrowType.DownArrow if self._source_details_visible else Qt.ArrowType.RightArrow
        )
        self.source_details_toggle.setText(
            "Hide source details" if self._source_details_visible else "Show source details"
        )

    def _clear_state(self) -> None:
        self._state = None
        self._selected = None
        self.reconstruct_button.setEnabled(False)
        self.summary_title.setText("Select an entity and target time")
        self.summary_subtitle.setText("")
        self.summary_target.setText("—")
        self.summary_presence.setText("—")
        self.summary_presence.setStyleSheet(f"color: {PIT_COLORS['text']}; font-size: 11px;")
        self.summary_presence_detail.hide()
        self.summary_history.setText("—")
        self.summary_families.setText("—")
        self.identity_card.set_model(None)
        self.source_details_panel.set_details("user", None)
        if self._source_details_visible:
            self.source_details_toggle.setChecked(False)
            self._toggle_source_details()

    @staticmethod
    def build_resolver(families: dict[str, list[ReportSnapshot]]) -> EntityResolver:
        return build_entity_resolver(families)
