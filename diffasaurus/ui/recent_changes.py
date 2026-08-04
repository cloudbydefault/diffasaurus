from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.report_history import (
    REASON_NO_BASELINE,
    ComparisonSummary,
    FamilyChangeStatus,
    RecentChangesReport,
    ReportSnapshot,
)
from diffasaurus.ui.period_selector import PeriodSelector
from diffasaurus.ui.report_runner import family_display_name

COLORS = {
    "surface": "#101b26",
    "surface2": "#152331",
    "border": "#26394a",
    "text": "#f2f7fb",
    "muted": "#8295a8",
    "teal": "#8bd450",
    "green": "#4fd1a5",
    "red": "#fb7185",
    "amber": "#f5b942",
    "blue": "#65a9ff",
}
DETAIL_TABLE_LIMIT = 2_000


class SummaryCard(QFrame):
    def __init__(self, eyebrow: str, value: str = "—", detail: str = "", accent: str = "#8bd450"):
        super().__init__()
        self.setObjectName("metricCard")
        self.setMinimumHeight(118)
        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        rail = QFrame()
        rail.setFixedWidth(4)
        rail.setStyleSheet(f"background:{accent}; border-radius:2px;")
        shell.addWidget(rail)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(4)
        eyebrow_label = QLabel(eyebrow.upper())
        eyebrow_label.setStyleSheet(
            f"color: {accent}; font-size: 10px; font-weight: 700; letter-spacing:1px;"
        )
        self.value = QLabel(value)
        self.value.setStyleSheet("font-size: 29px; font-weight: 700;")
        self.detail = QLabel(detail)
        self.detail.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        layout.addWidget(eyebrow_label)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        shell.addWidget(content, 1)

    def set_data(self, value: str, detail: str):
        self.value.setText(value)
        self.detail.setText(detail)


class FamilyChangeSection(QFrame):
    details_requested = pyqtSignal(str)
    open_in_compare_requested = pyqtSignal(str, object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self._family = ""
        self._status: FamilyChangeStatus | None = None
        self._details: ComparisonSummary | None = None
        self._filter = "All"
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.status_badge = QLabel()
        self.status_badge.setStyleSheet("font-size: 11px; font-weight: 700;")
        header.addWidget(self.title_label)
        header.addWidget(self.status_badge)
        header.addStretch()
        self.counts_label = QLabel()
        self.counts_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        header.addWidget(self.counts_label)
        self.compare_button = QPushButton("Open in Compare")
        self.compare_button.setObjectName("secondaryButton")
        self.compare_button.clicked.connect(self._emit_open_in_compare)
        header.addWidget(self.compare_button)
        self.toggle_button = QPushButton("Show details")
        self.toggle_button.setObjectName("filterButton")
        self.toggle_button.clicked.connect(self._toggle_details)
        header.addWidget(self.toggle_button)
        root.addLayout(header)

        self.subtitle_label = QLabel()
        self.subtitle_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        root.addWidget(self.subtitle_label)

        self.coverage_label = QLabel()
        self.coverage_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        self.coverage_label.setWordWrap(True)
        root.addWidget(self.coverage_label)

        self.reason_label = QLabel()
        self.reason_label.setStyleSheet(f"color: {COLORS['amber']}; font-size: 11px;")
        self.reason_label.setWordWrap(True)
        self.reason_label.hide()
        root.addWidget(self.reason_label)

        self.details_panel = QWidget()
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)

        filter_row = QHBoxLayout()
        self.filter_buttons: list[QPushButton] = []
        for label in ("All", "Added", "Removed", "Changed"):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("filterButton")
            button.clicked.connect(lambda _checked=False, value=label: self._set_filter(value))
            self.filter_buttons.append(button)
            filter_row.addWidget(button)
        self.filter_buttons[0].setChecked(True)
        filter_row.addStretch()
        self.detail_search = QLineEdit()
        self.detail_search.setPlaceholderText("Search keys or changed values…")
        self.detail_search.setMinimumWidth(260)
        self.detail_search.textChanged.connect(self._apply_detail_filters)
        filter_row.addWidget(self.detail_search)
        details_layout.addLayout(filter_row)

        self.detail_notice = QLabel("")
        self.detail_notice.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        self.detail_notice.hide()
        details_layout.addWidget(self.detail_notice)

        self.detail_table = QTableWidget(0, 5)
        self.detail_table.setHorizontalHeaderLabels(
            ("Change", "Identity", "Property", "Before", "After")
        )
        self.detail_table.setAlternatingRowColors(True)
        self.detail_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.detail_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.verticalHeader().setDefaultSectionSize(34)
        self.detail_table.setShowGrid(False)
        self.detail_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.detail_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.detail_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        details_layout.addWidget(self.detail_table)
        self.details_panel.hide()
        root.addWidget(self.details_panel)

    def apply_status(self, item: FamilyChangeStatus, cutoff: datetime):
        self._family = item.family
        self._status = item
        self._details = item.summary if item.summary and item.summary.details else None
        self.title_label.setText(family_display_name(item.family))
        self.subtitle_label.setText(item.family)
        self.subtitle_label.setToolTip(item.family)

        if item.status == "changed":
            self.status_badge.setText("Changes found")
            self.status_badge.setStyleSheet(
                f"color: {COLORS['green']}; font-size: 11px; font-weight: 700;"
            )
        elif item.status == "unchanged":
            self.status_badge.setText("No changes")
            self.status_badge.setStyleSheet(
                f"color: {COLORS['blue']}; font-size: 11px; font-weight: 700;"
            )
        else:
            self.status_badge.setText("No historical data")
            self.status_badge.setStyleSheet(
                f"color: {COLORS['amber']}; font-size: 11px; font-weight: 700;"
            )

        if item.summary:
            self.counts_label.setText(
                f"{item.summary.added} added · {item.summary.removed} removed · "
                f"{item.summary.changed} changed"
            )
            self.counts_label.show()
        else:
            self.counts_label.hide()

        coverage_parts = [f"Period cutoff: {cutoff.strftime('%d %b %Y · %H:%M')}"]
        show_baseline = item.baseline is not None and item.reason != REASON_NO_BASELINE
        if show_baseline:
            coverage_parts.append(f"Baseline: {item.baseline.label}")
        elif item.latest:
            coverage_parts.append(f"Latest on disk: {item.latest.label}")
        if item.latest and (show_baseline or item.status in {"changed", "unchanged"}):
            coverage_parts.append(f"Latest: {item.latest.label}")
        self.coverage_label.setText("  ·  ".join(coverage_parts))

        if item.reason:
            self.reason_label.setText(item.reason)
            self.reason_label.show()
        else:
            self.reason_label.hide()

        comparable = item.status in {"changed", "unchanged"} and item.baseline and item.latest
        self.compare_button.setEnabled(bool(comparable))
        self.toggle_button.setEnabled(bool(comparable))
        self.toggle_button.setVisible(bool(comparable))

        if comparable and self._details:
            self._apply_detail_filters()
        else:
            self.details_panel.hide()
            self._expanded = False
            self.toggle_button.setText("Show details")

    def set_details(self, summary: ComparisonSummary):
        self._details = summary
        if self._expanded:
            self._apply_detail_filters()

    def _emit_open_in_compare(self):
        if not self._status or not self._status.baseline or not self._status.latest:
            return
        self.open_in_compare_requested.emit(
            self._family,
            self._status.baseline,
            self._status.latest,
            self._status.key_column,
        )

    def _toggle_details(self):
        if not self._status or self._status.status == "no_data":
            return
        self._expanded = not self._expanded
        self.toggle_button.setText("Hide details" if self._expanded else "Show details")
        self.details_panel.setVisible(self._expanded)
        if self._expanded and self._details is None:
            self.details_requested.emit(self._family)
        elif self._expanded:
            self._apply_detail_filters()

    def _set_filter(self, value: str):
        self._filter = value
        for button in self.filter_buttons:
            button.setChecked(button.text() == value)
        self._apply_detail_filters()

    def _apply_detail_filters(self):
        if not self._details:
            self.detail_table.setRowCount(0)
            self.detail_notice.hide()
            return
        needle = self.detail_search.text().strip().lower()
        matching: list[dict[str, str]] = []
        has_more = False
        for detail in self._details.details:
            if self._filter != "All" and detail["change"] != self._filter:
                continue
            if needle and needle not in " ".join(detail.values()).lower():
                continue
            if len(matching) >= DETAIL_TABLE_LIMIT:
                has_more = True
                break
            matching.append(detail)

        self.detail_table.setUpdatesEnabled(False)
        self.detail_table.setRowCount(len(matching))
        for row, detail in enumerate(matching):
            values = (
                detail["change"],
                detail["key"],
                detail["column"],
                detail["before"],
                detail["after"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setForeground(
                        QColor(
                            {
                                "Added": COLORS["green"],
                                "Removed": COLORS["red"],
                                "Changed": COLORS["amber"],
                            }.get(value, COLORS["text"])
                        )
                    )
                    item.setFont(
                        QFont(
                            item.font().family(),
                            item.font().pointSize(),
                            QFont.Weight.Bold,
                        )
                    )
                self.detail_table.setItem(row, column, item)
        self.detail_table.setUpdatesEnabled(True)
        if has_more:
            self.detail_notice.setText(
                f"Showing the first {DETAIL_TABLE_LIMIT:,} matching details for speed. "
                "Refine the filter to narrow results."
            )
            self.detail_notice.show()
        else:
            self.detail_notice.hide()


class RecentChangesPage(QWidget):
    period_changed = pyqtSignal(object, str)
    details_requested = pyqtSignal(str, object, object, str)
    open_in_compare_requested = pyqtSignal(str, object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sections: dict[str, FamilyChangeSection] = {}
        self._current_report: RecentChangesReport | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        controls = QHBoxLayout()
        self.period_selector = PeriodSelector()
        controls.addWidget(self.period_selector)
        controls.addStretch()
        self.cutoff_caption = QLabel("")
        self.cutoff_caption.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        controls.addWidget(self.cutoff_caption)
        layout.addLayout(controls)

        cards = QHBoxLayout()
        self.card_changed = SummaryCard("Reports with changes", accent=COLORS["green"])
        self.card_totals = SummaryCard("Total movement", accent=COLORS["teal"])
        self.card_unchanged = SummaryCard("No changes", accent=COLORS["blue"])
        self.card_no_data = SummaryCard("No comparable history", accent=COLORS["amber"])
        for card in (
            self.card_changed,
            self.card_totals,
            self.card_unchanged,
            self.card_no_data,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        self.empty_label = QLabel(
            "No CSV snapshots found. Generate reports to start collecting tenant history."
        )
        self.empty_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sections_host = QWidget()
        self.sections_layout = QVBoxLayout(self.sections_host)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(12)
        self.sections_layout.addStretch()
        scroll.setWidget(self.sections_host)
        layout.addWidget(scroll, 1)

        self.period_selector.period_changed.connect(self._emit_period_changed)

    def current_period(self) -> tuple[timedelta, str]:
        return self.period_selector.current_period()

    def _emit_period_changed(self):
        period, label = self.current_period()
        self.period_changed.emit(period, label)

    def show_loading(self):
        self.card_changed.set_data("…", "aggregating changes")
        self.card_totals.set_data("…", "across all report families")
        self.card_unchanged.set_data("…", "waiting for results")
        self.card_no_data.set_data("…", "checking snapshot coverage")

    def apply_report(self, report: RecentChangesReport):
        self._current_report = report
        self.cutoff_caption.setText(
            f"Comparing against snapshots collected after "
            f"{report.cutoff.strftime('%d %b %Y · %H:%M')}"
        )
        self.card_changed.set_data(
            f"{report.changed_count:,}",
            f"of {len(report.families):,} report families",
        )
        self.card_totals.set_data(
            f"{report.total_added + report.total_removed + report.total_changed:,}",
            (
                f"{report.total_added:,} added · {report.total_removed:,} removed · "
                f"{report.total_changed:,} changed"
            ),
        )
        self.card_unchanged.set_data(f"{report.unchanged_count:,}", "reports unchanged in period")
        self.card_no_data.set_data(
            f"{report.no_data_count:,}",
            "missing in-period or baseline snapshots",
        )

        has_any_snapshots = any(
            item.latest is not None or item.baseline is not None for item in report.families
        )
        self.empty_label.setVisible(not report.families)
        if report.families and not has_any_snapshots and report.no_data_count == len(report.families):
            self.empty_label.setText(
                "No CSV snapshots found. Generate reports to start collecting tenant history."
            )
            self.empty_label.show()
        elif report.families and report.no_data_count == len(report.families):
            self.empty_label.setText(
                "No report family has both an in-period snapshot and an earlier baseline. "
                "Collect snapshots during the selected period and ensure an older baseline exists "
                "at or before the period cutoff."
            )
            self.empty_label.show()
        else:
            self.empty_label.hide()

        while self.sections_layout.count() > 1:
            item = self.sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._sections.clear()

        for family_status in report.families:
            section = FamilyChangeSection()
            section.apply_status(family_status, report.cutoff)
            section.details_requested.connect(self._on_details_requested)
            section.open_in_compare_requested.connect(self.open_in_compare_requested.emit)
            self._sections[family_status.family] = section
            self.sections_layout.insertWidget(
                self.sections_layout.count() - 1,
                section,
            )

    def set_family_details(self, family: str, summary: ComparisonSummary):
        section = self._sections.get(family)
        if section:
            section.set_details(summary)

    def _on_details_requested(self, family: str):
        section = self._sections.get(family)
        if not section or not section._status:
            return
        status = section._status
        if not status.baseline or not status.latest:
            return
        self.details_requested.emit(
            family,
            status.baseline,
            status.latest,
            status.key_column,
        )
