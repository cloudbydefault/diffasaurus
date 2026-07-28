from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.report_history import (
    ComparisonSummary,
    ReportSnapshot,
    common_headers,
    compare_snapshots,
    history_metrics,
    report_run_health,
    scan_report_history,
    suggested_key,
)
from diffasaurus.core.settings import get_active_reports_dir
from diffasaurus.ui.report_runner import RunScriptsDialog
from diffasaurus.ui.source_settings import ReportSourceSettingsDialog
from diffasaurus.ui.charts import ChangeBars, LineChart


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


class MetricCard(QFrame):
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
        self.eyebrow = QLabel(eyebrow.upper())
        self.eyebrow.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: 700; letter-spacing:1px;")
        self.value = QLabel(value)
        self.value.setStyleSheet("font-size: 29px; font-weight: 700;")
        self.detail = QLabel(detail)
        self.detail.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        layout.addWidget(self.eyebrow)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        shell.addWidget(content, 1)

    def set_data(self, value: str, detail: str):
        self.value.setText(value)
        self.detail.setText(detail)


class DiffasaurusWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Diffasaurus")
        self.resize(1440, 880)
        self.report_dir = get_active_reports_dir()
        self.families: dict[str, list[ReportSnapshot]] = {}
        self.current_history: list[tuple[ReportSnapshot, dict[str, float]]] = []
        self.current_comparison: ComparisonSummary | None = None
        self.current_filter = "All"
        self._build_ui()
        self._wire()
        self.refresh_history()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(246)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 22, 22)
        side.setSpacing(8)
        brand = QLabel("DIFFA<span style='color:#8bd450'>SAURUS</span>")
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand.setStyleSheet("font-size: 22px; font-weight: 800; letter-spacing: .5px;")
        product = QLabel("TENANT CHANGE ARCHAEOLOGY")
        product.setStyleSheet(
            f"color:{COLORS['muted']}; font-size:8px; font-weight:700; letter-spacing:1.2px;"
        )
        side.addWidget(brand)
        side.addWidget(product)
        side.addSpacing(24)

        self.nav_buttons = []
        for label in ("◈   Dig site", "▦   Run health", "▤   Fossil library", "⇄   Compare snapshots"):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("navButton")
            self.nav_buttons.append(button)
            side.addWidget(button)
        self.nav_buttons[0].setChecked(True)
        side.addStretch()
        generate = QPushButton("＋  Generate reports")
        generate.setObjectName("primaryButton")
        generate.clicked.connect(self.open_report_runner)
        source = QPushButton("Report source")
        source.setObjectName("secondaryButton")
        source.clicked.connect(self.open_source_settings)
        side.addWidget(generate)
        side.addWidget(source)
        shell.addWidget(sidebar)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(34, 28, 34, 30)
        outer.setSpacing(22)

        top = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading_box.setSpacing(2)
        self.page_title = QLabel("The dig site")
        self.page_title.setStyleSheet("font-size: 28px; font-weight: 750; letter-spacing:-0.5px;")
        self.page_subtitle = QLabel("Unearthing your Microsoft 365 history, one CSV fossil at a time.")
        self.page_subtitle.setStyleSheet(f"color:{COLORS['muted']};")
        heading_box.addWidget(self.page_title)
        heading_box.addWidget(self.page_subtitle)
        top.addLayout(heading_box)
        top.addStretch()
        self.source_badge = QLabel("●  LOCAL DATABASE")
        self.source_badge.setObjectName("sourceBadge")
        top.addWidget(self.source_badge)
        family_box = QVBoxLayout()
        family_box.setSpacing(4)
        family_label = QLabel("REPORT FAMILY")
        family_label.setObjectName("fieldLabel")
        family_box.addWidget(family_label)
        self.family_combo = QComboBox()
        self.family_combo.setMinimumWidth(330)
        family_box.addWidget(self.family_combo)
        top.addLayout(family_box)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryButton")
        top.addWidget(self.refresh_button)
        outer.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_overview())
        self.stack.addWidget(self._build_run_health())
        self.stack.addWidget(self._build_library())
        self.stack.addWidget(self._build_compare())
        outer.addWidget(self.stack, 1)
        shell.addWidget(content, 1)

    def _build_overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_current = MetricCard("Current inventory", accent=COLORS["teal"])
        self.card_delta = MetricCard("Since previous", accent=COLORS["blue"])
        self.card_changes = MetricCard("Latest movement", accent=COLORS["amber"])
        self.card_snapshots = MetricCard("History depth", accent=COLORS["green"])
        for card in (self.card_current, self.card_delta, self.card_changes, self.card_snapshots):
            cards.addWidget(card)
        layout.addLayout(cards)

        chart_header = QHBoxLayout()
        title = QLabel("Metric over time")
        title.setObjectName("sectionTitle")
        chart_header.addWidget(title)
        chart_header.addStretch()
        self.metric_combo = QComboBox()
        self.metric_combo.setMinimumWidth(260)
        chart_header.addWidget(self.metric_combo)
        layout.addLayout(chart_header)
        self.line_chart = LineChart()
        layout.addWidget(self.line_chart, 3)
        movement_title = QLabel("Movement between snapshots")
        movement_title.setObjectName("sectionTitle")
        layout.addWidget(movement_title)
        self.change_bars = ChangeBars()
        layout.addWidget(self.change_bars, 2)
        return page

    def _build_run_health(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        notice = QFrame()
        notice.setObjectName("noticeCard")
        notice_layout = QHBoxLayout(notice)
        notice_layout.setContentsMargins(18, 13, 18, 13)
        notice_title = QLabel("Scheduled collection")
        notice_title.setStyleSheet("font-weight:700;")
        notice_text = QLabel(
            "Monday–Friday · 01:00 · A run is counted only when its CSV output is present."
        )
        notice_text.setStyleSheet(f"color:{COLORS['muted']};")
        notice_layout.addWidget(notice_title)
        notice_layout.addSpacing(16)
        notice_layout.addWidget(notice_text)
        notice_layout.addStretch()
        layout.addWidget(notice)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.health_coverage = MetricCard("Schedule coverage", accent=COLORS["teal"])
        self.health_observed = MetricCard("Outputs observed", accent=COLORS["green"])
        self.health_missing = MetricCard("Missing outputs", accent=COLORS["red"])
        self.health_latest = MetricCard("Latest output", accent=COLORS["blue"])
        for card in (self.health_coverage, self.health_observed, self.health_missing, self.health_latest):
            cards.addWidget(card)
        layout.addLayout(cards)

        title = QLabel("Family health")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.health_table = self._table(
            ("Report family", "Latest output", "Expected", "Observed", "Missing", "Late", "Status")
        )
        self.health_table.setMaximumHeight(270)
        self.health_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.health_table)

        calendar_title = QLabel("Last 10 scheduled business days")
        calendar_title.setObjectName("sectionTitle")
        layout.addWidget(calendar_title)
        self.health_calendar = self._table(("Report family",))
        self.health_calendar.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.health_calendar, 1)
        return page

    def _build_library(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        intro = QLabel("Every CSV is a dated, read-only tenant snapshot.")
        intro.setStyleSheet(f"color:{COLORS['muted']};")
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText("Search snapshots…")
        self.library_search.setMinimumWidth(300)
        tools.addWidget(intro)
        tools.addStretch()
        tools.addWidget(self.library_search)
        layout.addLayout(tools)
        self.library_table = self._table(("Snapshot", "Captured", "Rows", "Columns", "File"))
        self.library_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.library_table)
        return page

    def _build_compare(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        selectors = QHBoxLayout()
        self.baseline_combo = QComboBox()
        self.latest_combo = QComboBox()
        self.key_combo = QComboBox()
        for label, widget in (
            ("Baseline", self.baseline_combo),
            ("Latest", self.latest_combo),
            ("Identity key", self.key_combo),
        ):
            group = QVBoxLayout()
            caption = QLabel(label.upper())
            caption.setObjectName("fieldLabel")
            group.addWidget(caption)
            group.addWidget(widget)
            selectors.addLayout(group, 1)
        self.compare_button = QPushButton("Compare")
        self.compare_button.setObjectName("primaryButton")
        self.compare_button.setMinimumHeight(42)
        selectors.addWidget(self.compare_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(selectors)

        cards = QHBoxLayout()
        self.compare_cards = {}
        for title, color in (
            ("Added", COLORS["green"]),
            ("Removed", COLORS["red"]),
            ("Changed", COLORS["amber"]),
            ("Stable", COLORS["blue"]),
        ):
            card = MetricCard(title, "0", "rows", color)
            self.compare_cards[title] = card
            cards.addWidget(card)
        layout.addLayout(cards)

        filter_row = QHBoxLayout()
        self.filter_buttons = []
        for label in ("All", "Added", "Removed", "Changed"):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("filterButton")
            button.clicked.connect(lambda _checked=False, value=label: self.set_change_filter(value))
            self.filter_buttons.append(button)
            filter_row.addWidget(button)
        self.filter_buttons[0].setChecked(True)
        filter_row.addStretch()
        self.change_search = QLineEdit()
        self.change_search.setPlaceholderText("Search keys or changed values…")
        self.change_search.setMinimumWidth(330)
        filter_row.addWidget(self.change_search)
        export = QPushButton("Export visible CSV")
        export.setObjectName("secondaryButton")
        export.clicked.connect(self.export_visible_changes)
        filter_row.addWidget(export)
        layout.addLayout(filter_row)
        self.diff_table = self._table(("Change", "Identity", "Property", "Before", "After"))
        self.diff_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.diff_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.diff_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.diff_table)
        return page

    @staticmethod
    def _table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(38)
        table.setShowGrid(False)
        return table

    def _wire(self):
        for index, button in enumerate(self.nav_buttons):
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
        self.refresh_button.clicked.connect(self.refresh_history)
        self.family_combo.currentIndexChanged.connect(self.family_changed)
        self.metric_combo.currentTextChanged.connect(self.metric_changed)
        self.library_search.textChanged.connect(self.filter_library)
        self.baseline_combo.currentIndexChanged.connect(self.snapshot_selection_changed)
        self.latest_combo.currentIndexChanged.connect(self.snapshot_selection_changed)
        self.compare_button.clicked.connect(self.run_comparison)
        self.change_search.textChanged.connect(self.apply_change_filters)

    def show_page(self, index: int):
        for current, button in enumerate(self.nav_buttons):
            button.setChecked(current == index)
        self.stack.setCurrentIndex(index)
        titles = (
            ("The dig site", "Unearthing your Microsoft 365 history, one CSV fossil at a time."),
            ("Scheduled run health", "See which weekday collections produced evidence—and which outputs are missing."),
            ("Fossil library", "Browse the CSV snapshots buried in your tenant timeline."),
            ("Compare snapshots", "Explain exactly what appeared, disappeared, or changed."),
        )
        self.page_title.setText(titles[index][0])
        self.page_subtitle.setText(titles[index][1])

    def refresh_history(self):
        selected = self.family_combo.currentText()
        self.report_dir = get_active_reports_dir()
        self.families = scan_report_history(self.report_dir)
        source_name = "LOCAL DATABASE" if self.report_dir.name == "reports" else self.report_dir.name.upper()
        self.source_badge.setText(
            f"●  {source_name}  ·  {sum(map(len, self.families.values()))} CSV"
        )
        self.family_combo.blockSignals(True)
        self.family_combo.clear()
        self.family_combo.addItems(self.families)
        if selected in self.families:
            self.family_combo.setCurrentText(selected)
        self.family_combo.blockSignals(False)
        self.family_changed()
        self._refresh_run_health()

    def _refresh_run_health(self):
        health = report_run_health(self.families, business_day_count=10)
        total_expected = sum(item.expected for item in health)
        total_observed = sum(item.observed for item in health)
        total_missing = sum(item.missing for item in health)
        coverage = total_observed / total_expected if total_expected else 0
        latest = max((item.latest for item in health if item.latest), default=None)
        self.health_coverage.set_data(f"{coverage:.0%}", "expected CSV outputs present")
        self.health_observed.set_data(f"{total_observed:,}", f"of {total_expected:,} scheduled outputs")
        self.health_missing.set_data(f"{total_missing:,}", "outputs without CSV evidence")
        if latest:
            age_days = max((datetime.now() - latest).days, 0)
            detail = "today" if age_days == 0 else f"{age_days} day{'s' if age_days != 1 else ''} ago"
            self.health_latest.set_data(latest.strftime("%d %b · %H:%M"), detail)
        else:
            self.health_latest.set_data("—", "no CSV output found")

        self.health_table.setRowCount(len(health))
        status_colors = {
            "Healthy": COLORS["green"],
            "Completed late": COLORS["amber"],
            "Attention": COLORS["amber"],
            "Missing runs": COLORS["red"],
        }
        for row, item in enumerate(health):
            values = (
                item.family.replace("_", " "),
                item.latest.strftime("%d %b %Y · %H:%M") if item.latest else "Never",
                str(item.expected),
                str(item.observed),
                str(item.missing),
                str(item.late),
                item.status,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 6:
                    cell.setForeground(QColor(status_colors.get(item.status, COLORS["muted"])))
                    cell.setFont(
                        QFont(cell.font().family(), cell.font().pointSize(), QFont.Weight.DemiBold)
                    )
                self.health_table.setItem(row, column, cell)

        days = list(health[0].days) if health else []
        headers = ["Report family"] + [day.strftime("%a\n%d %b") for day, _ in days]
        self.health_calendar.setColumnCount(len(headers))
        self.health_calendar.setHorizontalHeaderLabels(headers)
        self.health_calendar.setRowCount(len(health))
        for row, item in enumerate(health):
            self.health_calendar.setItem(row, 0, QTableWidgetItem(item.family.replace("_", " ")))
            for column, (_day, snapshot) in enumerate(item.days, start=1):
                cell = QTableWidgetItem("●" if snapshot else "—")
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setForeground(QColor(COLORS["green"] if snapshot else COLORS["red"]))
                cell.setToolTip(
                    snapshot.captured_at.strftime("CSV observed at %H:%M")
                    if snapshot
                    else "No CSV output observed"
                )
                self.health_calendar.setItem(row, column, cell)
        for column in range(1, len(headers)):
            self.health_calendar.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )

    def family_changed(self):
        snapshots = self.families.get(self.family_combo.currentText(), [])
        self._populate_library(snapshots)
        self._populate_snapshot_combos(snapshots)
        if not snapshots:
            self.current_history = []
            self.line_chart.set_series([], [])
            self.change_bars.set_series([])
            return

        _title, self.current_history = history_metrics(snapshots)
        available = []
        for _snapshot, metrics in self.current_history:
            for metric in metrics:
                if metric not in available:
                    available.append(metric)
        previous = self.metric_combo.currentText()
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        self.metric_combo.addItems(available)
        if previous in available:
            self.metric_combo.setCurrentText(previous)
        self.metric_combo.blockSignals(False)
        self.metric_changed()
        self._update_movement(snapshots)

    def metric_changed(self):
        metric = self.metric_combo.currentText()
        values, labels = [], []
        for snapshot, metrics in self.current_history:
            if metric in metrics:
                values.append(metrics[metric])
                labels.append(snapshot.captured_at.strftime("%d %b"))
        self.line_chart.set_series(values, labels)
        current = values[-1] if values else 0
        previous = values[-2] if len(values) > 1 else current
        delta = current - previous
        delta_text = f"{delta:+,.0f}" if len(values) > 1 else "—"
        self.card_current.set_data(f"{current:,.0f}", metric or "No metric")
        self.card_delta.set_data(delta_text, "versus previous snapshot")
        self.card_snapshots.set_data(str(len(self.current_history)), "CSV snapshots available")

    def _update_movement(self, snapshots: list[ReportSnapshot]):
        series = []
        latest_summary = None
        for baseline, latest in zip(snapshots[:-1], snapshots[1:]):
            headers = common_headers(baseline, latest)
            key = suggested_key(headers)
            if not key:
                continue
            try:
                summary = compare_snapshots(baseline, latest, key)
            except Exception:
                continue
            latest_summary = summary
            series.append(
                (
                    latest.captured_at.strftime("%d %b"),
                    summary.added,
                    summary.removed,
                    summary.changed,
                )
            )
        self.change_bars.set_series(series[-12:])
        if latest_summary:
            self.card_changes.set_data(
                f"{latest_summary.total_changes:,}",
                f"{latest_summary.added} added · {latest_summary.removed} removed · {latest_summary.changed} changed",
            )
        else:
            self.card_changes.set_data("—", "needs at least two comparable snapshots")

    def _populate_library(self, snapshots: list[ReportSnapshot]):
        self.library_table.setRowCount(len(snapshots))
        for row, snapshot in enumerate(reversed(snapshots)):
            values = (
                snapshot.label,
                snapshot.captured_at.strftime("%Y-%m-%d %H:%M"),
                f"{snapshot.row_count:,}",
                str(len(snapshot.headers)),
                snapshot.path.name,
            )
            for column, value in enumerate(values):
                self.library_table.setItem(row, column, QTableWidgetItem(value))
        self.filter_library()

    def filter_library(self):
        needle = self.library_search.text().strip().lower()
        for row in range(self.library_table.rowCount()):
            text = " ".join(
                self.library_table.item(row, column).text()
                for column in range(self.library_table.columnCount())
                if self.library_table.item(row, column)
            ).lower()
            self.library_table.setRowHidden(row, bool(needle) and needle not in text)

    def _populate_snapshot_combos(self, snapshots: list[ReportSnapshot]):
        for combo in (self.baseline_combo, self.latest_combo):
            combo.blockSignals(True)
            combo.clear()
            for snapshot in snapshots:
                combo.addItem(snapshot.label, snapshot)
            combo.blockSignals(False)
        if snapshots:
            self.baseline_combo.setCurrentIndex(max(0, len(snapshots) - 2))
            self.latest_combo.setCurrentIndex(len(snapshots) - 1)
        self.snapshot_selection_changed()

    def snapshot_selection_changed(self):
        baseline = self.baseline_combo.currentData()
        latest = self.latest_combo.currentData()
        headers = common_headers(baseline, latest) if baseline and latest else []
        selected = self.key_combo.currentText()
        self.key_combo.clear()
        self.key_combo.addItems(headers)
        preferred = suggested_key(headers)
        self.key_combo.setCurrentText(selected if selected in headers else preferred)

    def run_comparison(self):
        baseline = self.baseline_combo.currentData()
        latest = self.latest_combo.currentData()
        if not baseline or not latest:
            return
        if baseline.path == latest.path:
            QMessageBox.information(self, "Compare snapshots", "Choose two different snapshots.")
            return
        try:
            self.current_comparison = compare_snapshots(baseline, latest, self.key_combo.currentText())
        except Exception as exc:
            QMessageBox.warning(self, "Compare snapshots", str(exc))
            return

        summary = self.current_comparison
        for title, value in (
            ("Added", summary.added),
            ("Removed", summary.removed),
            ("Changed", summary.changed),
            ("Stable", summary.stable),
        ):
            self.compare_cards[title].set_data(f"{value:,}", "rows")
        self.diff_table.setRowCount(len(summary.details))
        for row, detail in enumerate(summary.details):
            values = (detail["change"], detail["key"], detail["column"], detail["before"], detail["after"])
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
                    item.setFont(QFont(item.font().family(), item.font().pointSize(), QFont.Weight.Bold))
                self.diff_table.setItem(row, column, item)
        self.set_change_filter("All")

    def set_change_filter(self, value: str):
        self.current_filter = value
        for button in self.filter_buttons:
            button.setChecked(button.text() == value)
        self.apply_change_filters()

    def apply_change_filters(self):
        needle = self.change_search.text().strip().lower()
        for row in range(self.diff_table.rowCount()):
            change = self.diff_table.item(row, 0).text() if self.diff_table.item(row, 0) else ""
            text = " ".join(
                self.diff_table.item(row, column).text()
                for column in range(self.diff_table.columnCount())
                if self.diff_table.item(row, column)
            ).lower()
            hidden = self.current_filter != "All" and change != self.current_filter
            hidden = hidden or (bool(needle) and needle not in text)
            self.diff_table.setRowHidden(row, hidden)

    def export_visible_changes(self):
        if not self.current_comparison:
            QMessageBox.information(self, "Export", "Run a comparison first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export visible changes",
            f"Diffasaurus_{datetime.now():%Y%m%d-%H%M%S}.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        import csv

        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("Change", "Identity", "Property", "Before", "After"))
            for row in range(self.diff_table.rowCount()):
                if self.diff_table.isRowHidden(row):
                    continue
                writer.writerow(
                    self.diff_table.item(row, column).text() if self.diff_table.item(row, column) else ""
                    for column in range(self.diff_table.columnCount())
                )
        QMessageBox.information(self, "Export", f"Changes exported to:\n{Path(path).name}")

    def open_report_runner(self):
        dialog = RunScriptsDialog(self)
        dialog.exec()
        self.refresh_history()

    def open_source_settings(self):
        dialog = ReportSourceSettingsDialog(self)
        if dialog.exec():
            self.refresh_history()


def diffasaurus_stylesheet() -> str:
    return f"""
        QMainWindow, QWidget {{ background: #09121b; color: {COLORS['text']}; }}
        QLabel {{ background: transparent; }}
        QFrame#sidebar {{ background: #0e1924; border-right: 1px solid {COLORS['border']}; }}
        QFrame#metricCard {{
            background: #121f2b;
            border: 1px solid {COLORS['border']};
            border-radius: 13px;
        }}
        QFrame#metricCard QWidget {{ background: transparent; }}
        QFrame#noticeCard {{
            background: #102631;
            border: 1px solid #214352;
            border-radius: 11px;
        }}
        QLabel#sectionTitle {{
            font-size: 17px;
            font-weight: 700;
            color: #f4f8fb;
        }}
        QLabel#fieldLabel {{
            color: #7f93a6;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        QLabel#sourceBadge {{
            color: #7fd9ce;
            background: #102b32;
            border: 1px solid #214850;
            border-radius: 12px;
            padding: 6px 10px;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: .5px;
        }}
        QPushButton {{
            background: #142330;
            color: {COLORS['text']};
            border: 1px solid {COLORS['border']};
            border-radius: 8px;
            padding: 9px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background:#182b3a; border-color: #4b7f87; }}
        QPushButton#navButton {{
            text-align: left;
            border: 0;
            background: transparent;
            padding: 13px 14px;
            color: #91a4b6;
            font-weight: 550;
        }}
        QPushButton#navButton:checked {{
            background: #162b39;
            color: #effffc;
            border-left: 3px solid #8bd450;
            font-weight: 650;
        }}
        QPushButton#primaryButton {{
            background: {COLORS['teal']};
            color: #071319;
            border: 0;
            font-weight: 700;
        }}
        QPushButton#primaryButton:hover {{ background:#83e0d5; }}
        QPushButton#secondaryButton {{ background: transparent; }}
        QPushButton#filterButton:checked {{
            background: #23465a;
            border-color: {COLORS['teal']};
        }}
        QComboBox, QLineEdit {{
            background: #0f1d29;
            color: {COLORS['text']};
            border: 1px solid {COLORS['border']};
            border-radius: 8px;
            padding: 9px 11px;
            selection-background-color: #23465a;
        }}
        QComboBox::drop-down {{ border: 0; width: 26px; }}
        QTableWidget {{
            background: #0d1924;
            alternate-background-color: #101f2c;
            border: 1px solid {COLORS['border']};
            border-radius: 10px;
            selection-background-color: #23465a;
            selection-color: white;
        }}
        QHeaderView::section {{
            background: #142430;
            color: #9db0c0;
            border: 0;
            border-bottom: 1px solid {COLORS['border']};
            padding: 9px 10px;
            font-size: 10px;
            font-weight: 700;
        }}
        QTableCornerButton::section {{ background:#142430; border:0; }}
        QProgressBar {{
            background:#0f1d29; border:1px solid {COLORS['border']};
            border-radius:6px; text-align:center;
        }}
        QProgressBar::chunk {{ background:{COLORS['teal']}; border-radius:5px; }}
        QToolTip {{
            background:#172837; color:white; border:1px solid #345066;
            padding:5px;
        }}
        QScrollBar:vertical {{ background: transparent; width: 10px; }}
        QScrollBar::handle:vertical {{ background: #2b4153; border-radius: 5px; min-height: 30px; }}
    """


def preferred_ui_font() -> str:
    available = set(QFontDatabase.families())
    for family in (
        "SF Pro Display",
        "SF Pro Text",
        "Inter",
        "Aptos",
        "Segoe UI Variable",
        "Segoe UI",
        "Avenir Next",
    ):
        if family in available:
            return family
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def main():
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName("Diffasaurus")
    app.setStyle("Fusion")
    app.setFont(QFont(preferred_ui_font(), 11))
    app.setStyleSheet(diffasaurus_stylesheet())
    window = DiffasaurusWindow()
    window.show()
    sys.exit(app.exec())
