from __future__ import annotations

from datetime import timedelta

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from diffasaurus.core.report_history import RECENT_CHANGE_PERIODS


class PeriodSelector(QWidget):
    period_changed = pyqtSignal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel("PERIOD")
        label.setObjectName("fieldLabel")
        self.combo = QComboBox()
        for period_label, _value in RECENT_CHANGE_PERIODS:
            self.combo.addItem(f"Last {period_label}", period_label)
        layout.addWidget(label)
        layout.addWidget(self.combo)
        self.combo.currentIndexChanged.connect(self._emit_period_changed)

    def current_period(self) -> tuple[timedelta, str]:
        label = self.combo.currentData()
        for period_label, period in RECENT_CHANGE_PERIODS:
            if period_label == label:
                return period, period_label
        return RECENT_CHANGE_PERIODS[0][1], RECENT_CHANGE_PERIODS[0][0]

    def _emit_period_changed(self):
        period, label = self.current_period()
        self.period_changed.emit(period, label)
