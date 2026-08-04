from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QDate, QTime, pyqtSignal
from PyQt6.QtWidgets import (
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


class TargetDateTimeSelector(QWidget):
    datetime_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        caption = QLabel("TARGET DATE & TIME")
        caption.setObjectName("fieldLabel")
        layout.addWidget(caption)

        picker_row = QHBoxLayout()
        picker_row.setSpacing(8)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd MMM yyyy")
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        picker_row.addWidget(self.date_edit)
        picker_row.addWidget(self.time_edit)
        layout.addLayout(picker_row)

        presets = QHBoxLayout()
        presets.setSpacing(6)
        self.now_button = QPushButton("Now")
        self.yesterday_button = QPushButton("Yesterday")
        self.week_button = QPushButton("7 days ago")
        self.month_button = QPushButton("30 days ago")
        for button in (
            self.now_button,
            self.yesterday_button,
            self.week_button,
            self.month_button,
        ):
            button.setObjectName("secondaryButton")
            button.setFlat(True)
            presets.addWidget(button)
        presets.addStretch()
        layout.addLayout(presets)

        self.disclaimer = QLabel(
            "Report timestamps use local collection time parsed from CSV filenames (no timezone offset)."
        )
        self.disclaimer.setWordWrap(True)
        self.disclaimer.setStyleSheet("color: #8295a8; font-size: 11px;")
        layout.addWidget(self.disclaimer)

        self.set_now()
        self.date_edit.dateChanged.connect(self._emit_changed)
        self.time_edit.timeChanged.connect(self._emit_changed)
        self.now_button.clicked.connect(self.set_now)
        self.yesterday_button.clicked.connect(self.set_yesterday)
        self.week_button.clicked.connect(self.set_week_ago)
        self.month_button.clicked.connect(self.set_month_ago)

    def current_datetime(self) -> datetime:
        date = self.date_edit.date()
        time = self.time_edit.time()
        return datetime(
            date.year(),
            date.month(),
            date.day(),
            time.hour(),
            time.minute(),
            time.second(),
        )

    def set_datetime(self, value: datetime) -> None:
        self.date_edit.setDate(QDate(value.year, value.month, value.day))
        self.time_edit.setTime(QTime(value.hour, value.minute, value.second))

    def set_now(self) -> None:
        self.set_datetime(datetime.now().replace(microsecond=0))

    def set_yesterday(self) -> None:
        self.set_datetime(datetime.now().replace(microsecond=0) - timedelta(days=1))

    def set_week_ago(self) -> None:
        self.set_datetime(datetime.now().replace(microsecond=0) - timedelta(days=7))

    def set_month_ago(self) -> None:
        self.set_datetime(datetime.now().replace(microsecond=0) - timedelta(days=30))

    def _emit_changed(self) -> None:
        self.datetime_changed.emit(self.current_datetime())
