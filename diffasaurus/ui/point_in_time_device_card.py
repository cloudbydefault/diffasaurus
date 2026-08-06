from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.entity.pit_presentation import (
    AutopilotPresentationModel,
    CardField,
    DeviceFieldGroup,
    ManagedDeviceCardModel,
    ManagedDevicesSectionModel,
)
from diffasaurus.ui.point_in_time_card import CardFieldWidget
from diffasaurus.ui.point_in_time_styles import COLLECTION_FILTER_THRESHOLD, PIT_COLORS


class _AutopilotSubsection(QFrame):
    def __init__(self, autopilot: AutopilotPresentationModel, parent=None):
        super().__init__(parent)
        self.setObjectName("pitAutopilotSubsection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        title = QLabel(autopilot.display_label)
        title.setStyleSheet(
            f"color: {PIT_COLORS['muted']}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;"
        )
        layout.addWidget(title)

        if autopilot.show_warning and autopilot.warning_message:
            warning = QLabel(autopilot.warning_message)
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {PIT_COLORS['amber']}; font-size: 11px;")
            layout.addWidget(warning)

        if autopilot.fields:
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(4)
            grid.setColumnStretch(1, 1)
            for row, field in enumerate(autopilot.fields):
                label = QLabel(field.label)
                label.setStyleSheet(f"color: {PIT_COLORS['muted']};")
                label.setMinimumWidth(120)
                label.setMaximumWidth(120)
                label.setWordWrap(True)
                grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(CardFieldWidget(field), row, 1)
            layout.addLayout(grid)


class _DeviceDetailsPanel(QWidget):
    def __init__(self, device: ManagedDeviceCardModel, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 0, 8)
        layout.setSpacing(8)

        for group in device.management_groups:
            heading = QLabel(group.title)
            heading.setStyleSheet(
                f"color: {PIT_COLORS['muted']}; font-size: 10px; font-weight: 700;"
            )
            layout.addWidget(heading)
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(4)
            grid.setColumnStretch(1, 1)
            for row, field in enumerate(group.fields):
                label = QLabel(field.label)
                label.setStyleSheet(f"color: {PIT_COLORS['muted']};")
                label.setMinimumWidth(120)
                label.setMaximumWidth(120)
                label.setWordWrap(True)
                grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(CardFieldWidget(field), row, 1)
            layout.addLayout(grid)

        if device.autopilot is not None:
            layout.addWidget(_AutopilotSubsection(device.autopilot))


class ManagedDeviceCardWidget(QFrame):
    def __init__(self, device: ManagedDeviceCardModel, parent=None):
        super().__init__(parent)
        self._device = device
        self._expanded = False
        self._details: _DeviceDetailsPanel | None = None

        self.setObjectName("pitManagedDeviceCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(8)

        self.toggle_button = QToolButton()
        self.toggle_button.setText("▾")
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setAccessibleName(f"Expand device {device.primary_label}")
        self.toggle_button.clicked.connect(self._toggle)
        header.addWidget(self.toggle_button)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        primary = QLabel(device.primary_label)
        primary.setStyleSheet("font-weight: 600; font-size: 13px;")
        text_box.addWidget(primary)
        if device.secondary_label:
            secondary = QLabel(device.secondary_label)
            secondary.setStyleSheet(f"color: {PIT_COLORS['text']}; font-size: 11px;")
            secondary.setWordWrap(True)
            text_box.addWidget(secondary)
        if device.tertiary_label:
            tertiary = QLabel(device.tertiary_label)
            tertiary.setStyleSheet(f"color: {PIT_COLORS['muted']}; font-size: 11px;")
            tertiary.setWordWrap(True)
            text_box.addWidget(tertiary)
        header.addLayout(text_box, 1)
        layout.addLayout(header)

        self.details_host = QWidget()
        self.details_layout = QVBoxLayout(self.details_host)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_host.hide()
        layout.addWidget(self.details_host)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.toggle_button.setText("▴" if self._expanded else "▾")
        self.toggle_button.setAccessibleName(
            f"{'Collapse' if self._expanded else 'Expand'} device {self._device.primary_label}"
        )
        if self._expanded and self._details is None:
            self._details = _DeviceDetailsPanel(self._device, self.details_host)
            self.details_layout.addWidget(self._details)
        self.details_host.setVisible(self._expanded)


class ManagedDevicesSectionWidget(QFrame):
    def __init__(self, section: ManagedDevicesSectionModel, parent=None):
        super().__init__(parent)
        self._section = section
        self._filter_text = ""

        self.setObjectName("pitManagedDevicesSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(8)

        title = QLabel(self._header_text())
        title.setStyleSheet(
            f"color: {PIT_COLORS['teal']}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        layout.addWidget(title)

        if section.message:
            message = QLabel(section.message)
            message.setWordWrap(True)
            message.setStyleSheet(f"color: {PIT_COLORS['muted']}; font-size: 11px;")
            layout.addWidget(message)

        if section.warning_message:
            warning = QLabel(section.warning_message)
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {PIT_COLORS['amber']}; font-size: 11px;")
            layout.addWidget(warning)

        if section.enrichment_error:
            error = QLabel(section.enrichment_error)
            error.setWordWrap(True)
            error.setStyleSheet(f"color: {PIT_COLORS['red']}; font-size: 11px;")
            layout.addWidget(error)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter devices…")
        self.filter_input.textChanged.connect(self._on_filter_changed)
        if len(section.devices) > COLLECTION_FILTER_THRESHOLD:
            layout.addWidget(self.filter_input)

        self.devices_host = QVBoxLayout()
        self.devices_host.setSpacing(4)
        layout.addLayout(self.devices_host)

        self._rebuild_devices()

    def _header_text(self) -> str:
        if self._section.coverage == "enrichment_error":
            return "Managed devices"
        if self._section.coverage == "known_zero":
            return "Managed devices · 0"
        if self._section.coverage == "populated":
            return f"Managed devices · {self._section.device_count}"
        return "Managed devices"

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = text.strip().casefold()
        self._rebuild_devices()

    def _filtered_devices(self) -> tuple[ManagedDeviceCardModel, ...]:
        if not self._filter_text:
            return self._section.devices
        return tuple(
            device
            for device in self._section.devices
            if self._filter_text in device.filter_blob
        )

    def _rebuild_devices(self) -> None:
        while self.devices_host.count():
            item = self.devices_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for device in self._filtered_devices():
            self.devices_host.addWidget(ManagedDeviceCardWidget(device))
