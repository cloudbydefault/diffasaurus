from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.entity.pit_presentation import (
    CardCollection,
    CardCollectionItem,
    CardField,
    CardSection,
    FieldAlternate,
    PointInTimeCardModel,
)
from diffasaurus.ui.point_in_time_styles import (
    COLLECTION_FILTER_THRESHOLD,
    COLLECTION_PREVIEW_COUNT,
    PIT_COLORS,
    format_collection_row_label,
    format_provenance_tooltip,
)


class CardFieldWidget(QWidget):
    def __init__(self, field: CardField, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.value_label = QLabel(field.display_value)
        self.value_label.setWordWrap(True)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.value_label.setToolTip(format_provenance_tooltip(field.provenance.observations))
        layout.addWidget(self.value_label, 1)

        if field.conflict:
            conflict_button = QToolButton()
            conflict_button.setText("⚠")
            conflict_button.setToolTip("Conflicting values from other sources")
            conflict_button.setAutoRaise(True)
            conflict_button.clicked.connect(
                lambda: self._show_conflict_menu(conflict_button, field.conflict.alternates)
            )
            layout.addWidget(conflict_button)

    def _show_conflict_menu(self, button: QToolButton, alternates: tuple[FieldAlternate, ...]) -> None:
        menu = QMenu(self)
        for alternate in alternates:
            obs = alternate.observation
            observed = obs.observed_at.strftime("%d %b %Y · %H:%M") if obs.observed_at else "—"
            action = menu.addAction(f"{alternate.value} ({obs.family}, {observed})")
            action.setEnabled(False)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))


class CardCollectionRow(QWidget):
    def __init__(self, item: CardCollectionItem, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        label = QLabel(format_collection_row_label(item.primary_label, item.secondary_label))
        label.setWordWrap(True)
        tooltip = format_provenance_tooltip(item.provenance.observations)
        if item.detail:
            tooltip = f"ID: {item.detail}\n\n{tooltip}"
        label.setToolTip(tooltip)
        layout.addWidget(label, 1)


class CardCollectionWidget(QFrame):
    def __init__(self, collection: CardCollection, parent=None):
        super().__init__(parent)
        self._collection = collection
        self._expanded = False
        self._showing_all = False
        self._filter_text = ""

        self.setObjectName("pitCollectionWidget")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(6)

        header_row = QHBoxLayout()
        self.header_label = QLabel(self._header_text())
        self.header_label.setStyleSheet("font-weight: 700; font-size: 13px;")
        header_row.addWidget(self.header_label, 1)

        self.toggle_button = QToolButton()
        self.toggle_button.setText("▾")
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.clicked.connect(self._toggle_expanded)
        header_row.addWidget(self.toggle_button)
        layout.addLayout(header_row)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(12, 0, 0, 0)
        body_layout.setSpacing(4)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter…")
        self.filter_input.textChanged.connect(self._on_filter_changed)
        self.filter_input.hide()
        body_layout.addWidget(self.filter_input)

        self.rows_host = QVBoxLayout()
        self.rows_host.setSpacing(2)
        body_layout.addLayout(self.rows_host)

        self.show_all_button = QPushButton()
        self.show_all_button.setObjectName("secondaryButton")
        self.show_all_button.clicked.connect(self._toggle_show_all)
        self.show_all_button.hide()
        body_layout.addWidget(self.show_all_button)

        layout.addWidget(self.body)
        self.body.hide()
        self._rebuild_rows()

    def _header_text(self) -> str:
        if self._collection.coverage == "known_empty":
            return f"{self._collection.title} · 0"
        if self._collection.coverage == "populated":
            return f"{self._collection.title} · {len(self._collection.items)}"
        return self._collection.title

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        self.toggle_button.setText("▴" if self._expanded else "▾")
        self._rebuild_rows()

    def _toggle_show_all(self) -> None:
        self._showing_all = not self._showing_all
        self._rebuild_rows()

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = text.strip().casefold()
        self._rebuild_rows()

    def _filtered_items(self) -> list[CardCollectionItem]:
        items = list(self._collection.items)
        if self._filter_text:
            items = [
                item
                for item in items
                if self._filter_text in item.primary_label.casefold()
                or self._filter_text in item.secondary_label.casefold()
                or self._filter_text in item.detail.casefold()
            ]
        return items

    def _rebuild_rows(self) -> None:
        while self.rows_host.count():
            item = self.rows_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        items = self._filtered_items()
        show_filter = len(self._collection.items) > COLLECTION_FILTER_THRESHOLD
        self.filter_input.setVisible(show_filter and self._expanded)

        if not self._expanded:
            self.show_all_button.hide()
            return

        limit = len(items) if self._showing_all else min(len(items), COLLECTION_PREVIEW_COUNT)
        for item in items[:limit]:
            self.rows_host.addWidget(CardCollectionRow(item))

        if len(items) > COLLECTION_PREVIEW_COUNT:
            self.show_all_button.show()
            if self._showing_all:
                self.show_all_button.setText("Show less")
            else:
                remaining = len(items) - COLLECTION_PREVIEW_COUNT
                self.show_all_button.setText(f"Show all ({remaining} more)")
        else:
            self.show_all_button.hide()


class CardSectionWidget(QFrame):
    def __init__(self, section: CardSection, parent=None):
        super().__init__(parent)
        self.setObjectName("pitSectionWidget")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(8)

        title = QLabel(section.title)
        title.setStyleSheet(
            f"color: {PIT_COLORS['teal']}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        layout.addWidget(title)

        if section.fields:
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(6)
            grid.setColumnStretch(1, 1)
            for row, field in enumerate(section.fields):
                label = QLabel(field.label)
                label.setStyleSheet(f"color: {PIT_COLORS['muted']};")
                label.setMinimumWidth(140)
                label.setMaximumWidth(140)
                label.setWordWrap(True)
                grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(CardFieldWidget(field), row, 1)
            layout.addLayout(grid)

        for collection in section.collections:
            layout.addWidget(CardCollectionWidget(collection))


class EntityIdentityCardView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        self._layout.addStretch()

    def set_model(self, model: PointInTimeCardModel | None) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if model is None:
            placeholder = QLabel("No reconstructed state yet.")
            placeholder.setStyleSheet(f"color: {PIT_COLORS['muted']};")
            self._layout.addWidget(placeholder)
            self._layout.addStretch()
            return

        if not model.sections:
            empty = QLabel("No card fields available for this entity at the target time.")
            empty.setStyleSheet(f"color: {PIT_COLORS['muted']};")
            self._layout.addWidget(empty)

        managed_inserted = False
        for section in model.sections:
            if section.section_id == "roles" and model.managed_devices is not None:
                from diffasaurus.ui.point_in_time_device_card import ManagedDevicesSectionWidget

                self._layout.addWidget(ManagedDevicesSectionWidget(model.managed_devices))
                managed_inserted = True
            self._layout.addWidget(CardSectionWidget(section))
        if model.managed_devices is not None and not managed_inserted:
            from diffasaurus.ui.point_in_time_device_card import ManagedDevicesSectionWidget

            self._layout.addWidget(ManagedDevicesSectionWidget(model.managed_devices))
        self._layout.addStretch()

    def collection_widget(self, collection_id: str) -> CardCollectionWidget | None:
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, CardSectionWidget):
                for child in widget.findChildren(CardCollectionWidget):
                    if child._collection.collection_id == collection_id:
                        return child
        return None
