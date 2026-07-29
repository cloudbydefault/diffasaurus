from __future__ import annotations

from collections import defaultdict

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


ACCENTS = {
    "good": "#4fd1a5",
    "danger": "#fb7185",
    "warning": "#f5b942",
    "info": "#65a9ff",
    "accent": "#a78bfa",
    "neutral": "#8295a8",
}


class DashboardCard(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, definition: dict, parent=None):
        super().__init__(parent)
        self.definition = definition
        self.setObjectName("dashboardCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(112)
        accent = ACCENTS.get(definition.get("kind", "neutral"), ACCENTS["neutral"])
        self.setStyleSheet(
            "QFrame#dashboardCard {"
            "background:#121f2b;"
            f"border:1px solid {accent};"
            "border-radius:13px;"
            "}"
            "QFrame#dashboardCard:hover {"
            f"background:#172837; border:2px solid {accent};"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(4)
        title = QLabel(str(definition.get("title", "Metric")))
        title.setStyleSheet("font-size:13px; font-weight:650;")
        value = QLabel(str(definition.get("value", "—")))
        value.setStyleSheet("font-size:27px; font-weight:750;")
        subtitle = QLabel(str(definition.get("subtitle", "")))
        subtitle.setStyleSheet("color:#8295a8; font-size:10px;")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(value)
        layout.addWidget(subtitle)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(
                {
                    "filter_spec": self.definition.get("filter_spec", {}),
                    "custom_filter": self.definition.get("custom_filter", {}),
                }
            )
        super().mousePressEvent(event)


class DashboardView(QWidget):
    apply_filter_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = "Dashboard"
        self._stats: list[dict] = []
        self._columns = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.title = QLabel("Dashboard")
        self.title.setObjectName("sectionTitle")
        layout.addWidget(self.title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

    def build_dashboard(self, title: str, stats: list[dict]):
        self._title = title
        self._stats = list(stats)
        self.title.setText(title)
        self._rebuild(self._column_count())

    def _column_count(self) -> int:
        width = max(self.scroll.viewport().width(), self.width())
        if width >= 1_150:
            return 4
        if width >= 820:
            return 3
        if width >= 540:
            return 2
        return 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = self._column_count()
        if self._stats and columns != self._columns:
            self._rebuild(columns)

    def _clear(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _rebuild(self, columns: int):
        self._clear()
        self._columns = columns
        grouped: dict[str, list[dict]] = defaultdict(list)
        for definition in self._stats:
            grouped[str(definition.get("section", "Overview"))].append(definition)
        row = 0
        for section, definitions in grouped.items():
            header = QLabel(section)
            header.setObjectName("dashboardSection")
            self.grid.addWidget(header, row, 0, 1, columns)
            row += 1
            for index, definition in enumerate(definitions):
                card = DashboardCard(definition)
                card.clicked.connect(self.apply_filter_requested.emit)
                self.grid.addWidget(
                    card,
                    row + index // columns,
                    index % columns,
                )
            row += (len(definitions) + columns - 1) // columns
        self.grid.setRowStretch(row, 1)
