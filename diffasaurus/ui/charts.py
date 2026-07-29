from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class LineChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self._labels: list[str] = []
        self._accent = QColor("#8bd450")
        self.setMinimumHeight(260)

    def set_series(self, values: list[float], labels: list[str], accent: str = "#8bd450"):
        self._values = values
        self._labels = labels
        self._accent = QColor(accent)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(14, 14, -14, -14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#101b26"))
        painter.drawRoundedRect(bounds, 18, 18)

        plot = bounds.adjusted(54, 28, -24, -44)
        if not self._values:
            painter.setPen(QColor("#7e91a5"))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "No snapshots for this metric")
            return

        minimum = min(self._values)
        maximum = max(self._values)
        spread = maximum - minimum
        if not spread:
            spread = max(abs(maximum) * 0.15, 1.0)
            minimum -= spread / 2
            maximum += spread / 2

        grid_pen = QPen(QColor("#223344"), 1)
        painter.setFont(QFont(painter.font().family(), 9))
        for index in range(5):
            y = plot.top() + plot.height() * index / 4
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            value = maximum - (maximum - minimum) * index / 4
            painter.setPen(QColor("#71869a"))
            painter.drawText(
                QRectF(bounds.left(), y - 10, 45, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:,.0f}",
            )

        count = len(self._values)
        points = []
        for index, value in enumerate(self._values):
            x = plot.left() if count == 1 else plot.left() + plot.width() * index / (count - 1)
            y = plot.bottom() - (value - minimum) / (maximum - minimum) * plot.height()
            points.append(QPointF(x, y))

        if len(points) > 1:
            fill = QPainterPath(points[0])
            for point in points[1:]:
                fill.lineTo(point)
            fill.lineTo(points[-1].x(), plot.bottom())
            fill.lineTo(points[0].x(), plot.bottom())
            fill.closeSubpath()
            gradient_color = QColor(self._accent)
            gradient_color.setAlpha(42)
            painter.fillPath(fill, gradient_color)

            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            painter.setPen(QPen(self._accent, 3))
            painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent)
        marker_step = max(1, math.ceil(len(points) / 140))
        marker_indexes = set(range(0, len(points), marker_step))
        marker_indexes.add(len(points) - 1)
        for index in sorted(marker_indexes):
            painter.drawEllipse(points[index], 4.5, 4.5)

        label_indexes = sorted({0, count // 2, count - 1})
        painter.setPen(QColor("#8195a8"))
        for index in label_indexes:
            label = self._labels[index] if index < len(self._labels) else ""
            painter.drawText(
                QRectF(points[index].x() - 55, plot.bottom() + 12, 110, 22),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )


class ChangeBars(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: list[tuple[str, int, int, int]] = []
        self.setMinimumHeight(230)

    def set_series(self, series: list[tuple[str, int, int, int]]):
        self._series = series
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(14, 14, -14, -14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#101b26"))
        painter.drawRoundedRect(bounds, 18, 18)

        if not self._series:
            painter.setPen(QColor("#7e91a5"))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "Compare at least two snapshots")
            return

        plot = bounds.adjusted(34, 28, -20, -42)
        maximum = max((a + r + c for _, a, r, c in self._series), default=1) or 1
        slot = plot.width() / max(len(self._series), 1)
        width = min(slot * 0.55, 46)
        colors = (QColor("#4fd1a5"), QColor("#fb7185"), QColor("#f5b942"))

        for index, (label, added, removed, changed) in enumerate(self._series):
            x = plot.left() + slot * index + (slot - width) / 2
            y = plot.bottom()
            for value, color in zip((added, removed, changed), colors):
                height = plot.height() * value / maximum
                y -= height
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(x, y, width, height + 1), 4, 4)
            painter.setPen(QColor("#8195a8"))
            painter.drawText(
                QRectF(x - 22, plot.bottom() + 10, width + 44, 22),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
            painter.setPen(Qt.PenStyle.NoPen)

        legend = (("Added", colors[0]), ("Removed", colors[1]), ("Changed", colors[2]))
        x = bounds.left() + 18
        for text, color in legend:
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x + 4, bounds.top() + 12), 4, 4)
            painter.setPen(QColor("#aab9c7"))
            painter.drawText(QRectF(x + 12, bounds.top() + 2, 70, 20), text)
            painter.setPen(Qt.PenStyle.NoPen)
            x += 86
