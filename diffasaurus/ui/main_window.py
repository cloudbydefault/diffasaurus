from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon
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
    QProgressBar,
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
    analyze_snapshot,
    common_headers,
    compare_snapshots,
    recent_movement,
    report_run_health,
    save_analysis_cache,
    scan_report_index,
    suggested_key,
)
from diffasaurus.core.paths import project_root
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
DETAIL_TABLE_LIMIT = 2_000


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    progress = pyqtSignal(int, int, str)


class BackgroundTask(QRunnable):
    def __init__(self, function, *args, with_progress: bool = False):
        super().__init__()
        self.function = function
        self.args = args
        self.with_progress = with_progress
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            if self.with_progress:
                result = self.function(*self.args, progress=self.signals.progress.emit)
            else:
                result = self.function(*self.args)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


def analyze_family(
    snapshots: list[ReportSnapshot],
    cancelled: threading.Event,
    progress=None,
):
    title = snapshots[0].family if snapshots else "Report history"
    history = []
    total = len(snapshots)
    if progress:
        progress(0, total, "Preparing snapshot analysis")
    for index, snapshot in enumerate(snapshots, start=1):
        if cancelled.is_set():
            return None
        hydrated, title, metrics = analyze_snapshot(snapshot)
        history.append((hydrated, metrics))
        if progress:
            progress(index, total, snapshot.path.name)
    if cancelled.is_set():
        return None
    if progress:
        progress(total, total, "Comparing recent changes")
    hydrated = [snapshot for snapshot, _metrics in history]
    movement, latest_summary = recent_movement(hydrated, max_intervals=12)
    save_analysis_cache()
    return title, history, movement, latest_summary


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
        icon_path = application_icon_path()
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1440, 880)
        self.report_dir = get_active_reports_dir()
        self.families: dict[str, list[ReportSnapshot]] = {}
        self.current_history: list[tuple[ReportSnapshot, dict[str, float]]] = []
        self.current_comparison: ComparisonSummary | None = None
        self.current_filter = "All"
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(2)
        self._workers: set[BackgroundTask] = set()
        self._index_generation = 0
        self._family_generation = 0
        self._comparison_generation = 0
        self._pending_family = ""
        self._preferred_metric = ""
        self._family_cancelled = threading.Event()
        self._family_timer = QTimer(self)
        self._family_timer.setSingleShot(True)
        self._family_timer.setInterval(250)
        self._family_timer.timeout.connect(self._start_family_analysis)
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

        self.loading_bar = QProgressBar()
        self.loading_bar.setObjectName("loadingBar")
        self.loading_bar.setMinimumHeight(22)
        self.loading_bar.setTextVisible(True)
        self.loading_bar.hide()
        outer.addWidget(self.loading_bar)

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
        self.diff_notice = QLabel("")
        self.diff_notice.setStyleSheet(f"color:{COLORS['muted']}; font-size:11px;")
        self.diff_notice.hide()
        layout.addWidget(self.diff_notice)
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

    def _run_background(
        self,
        function,
        args,
        on_result,
        on_error,
        on_progress=None,
        with_progress: bool = False,
    ):
        worker = BackgroundTask(function, *args, with_progress=with_progress)
        self._workers.add(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        self.thread_pool.start(worker)

    def _show_progress(self, current: int, total: int, label: str):
        self.loading_bar.show()
        if total > 0:
            self.loading_bar.setRange(0, total)
            self.loading_bar.setValue(min(current, total))
            percent = round((current / total) * 100)
            self.loading_bar.setFormat(f"{percent}%  ·  {label}")
        else:
            self.loading_bar.setRange(0, 0)
            self.loading_bar.setFormat(label)

    def _hide_progress(self):
        self.loading_bar.hide()
        self.loading_bar.setRange(0, 1)
        self.loading_bar.setValue(0)

    def _update_source_badge(self, prefix: str = "●"):
        source_name = "LOCAL DATABASE" if self.report_dir.name == "reports" else self.report_dir.name.upper()
        self.source_badge.setText(
            f"{prefix}  {source_name}  ·  {sum(map(len, self.families.values()))} CSV"
        )

    def refresh_history(self):
        selected = self.family_combo.currentText()
        self.report_dir = get_active_reports_dir()
        self._family_generation += 1
        self._family_cancelled.set()
        self._family_timer.stop()
        self._comparison_generation += 1
        self._index_generation += 1
        generation = self._index_generation
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Indexing…")
        self.family_combo.setEnabled(False)
        self.source_badge.setText("◌  INDEXING CSV FOSSILS…")
        self._show_progress(0, 0, "Finding CSV snapshots…")
        self._run_background(
            scan_report_index,
            (self.report_dir,),
            lambda families: self._index_ready(generation, selected, families),
            lambda message: self._index_failed(generation, message),
            lambda current, total, label: (
                self._show_progress(current, total, f"Indexing · {label}")
                if generation == self._index_generation
                else None
            ),
            with_progress=True,
        )

    def _index_ready(self, generation: int, selected: str, families):
        if generation != self._index_generation:
            return
        self.families = families
        self.family_combo.blockSignals(True)
        self.family_combo.clear()
        self.family_combo.addItems(self.families)
        if selected in self.families:
            self.family_combo.setCurrentText(selected)
        self.family_combo.blockSignals(False)
        self.family_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh")
        self._update_source_badge()
        self._refresh_run_health()
        self.family_changed()

    def _index_failed(self, generation: int, message: str):
        if generation != self._index_generation:
            return
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh")
        self.family_combo.setEnabled(True)
        self.source_badge.setText("×  INDEX FAILED")
        self._hide_progress()
        QMessageBox.warning(self, "Report indexing", message)

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
        current_metric = self.metric_combo.currentText()
        if current_metric and current_metric != "Analyzing snapshots…":
            self._preferred_metric = current_metric
        snapshots = self.families.get(self.family_combo.currentText(), [])
        self._populate_library(snapshots)
        self._populate_snapshot_combos(snapshots)
        self._family_generation += 1
        self._family_cancelled.set()
        self._family_cancelled = threading.Event()
        self._pending_family = self.family_combo.currentText()
        self._family_timer.stop()
        if not snapshots:
            self.current_history = []
            self.line_chart.set_series([], [])
            self.change_bars.set_series([])
            self._hide_progress()
            return

        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        self.metric_combo.addItem("Analyzing snapshots…")
        self.metric_combo.blockSignals(False)
        self.metric_combo.setEnabled(False)
        self.compare_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Analyzing…")
        self.card_current.set_data("…", "reading selected report family")
        self.card_delta.set_data("…", "the window stays responsive")
        self.card_changes.set_data("…", "checking the latest 12 intervals")
        self.card_snapshots.set_data(str(len(snapshots)), "snapshots queued")
        self._show_progress(0, len(snapshots), "Preparing snapshot analysis")
        self._family_timer.start()

    def _start_family_analysis(self):
        family = self._pending_family
        generation = self._family_generation
        snapshots = self.families.get(family, [])
        if not snapshots:
            return
        self._run_background(
            analyze_family,
            (snapshots, self._family_cancelled),
            lambda payload: self._family_ready(generation, family, payload),
            lambda message: self._family_failed(generation, family, message),
            lambda current, total, label: (
                self._show_progress(current, total, f"Analyzing · {label}")
                if generation == self._family_generation
                else None
            ),
            with_progress=True,
        )

    def _family_ready(self, generation: int, family: str, payload):
        if generation != self._family_generation or family != self.family_combo.currentText():
            return
        if payload is None:
            return
        _title, self.current_history, movement, latest_summary = payload
        snapshots = [snapshot for snapshot, _metrics in self.current_history]
        self.families[family] = snapshots
        self._populate_library(snapshots)
        self._populate_snapshot_combos(snapshots)
        available = []
        for _snapshot, metrics in self.current_history:
            for metric in metrics:
                if metric not in available:
                    available.append(metric)
        previous = self._preferred_metric
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        self.metric_combo.addItems(available)
        if previous in available:
            self.metric_combo.setCurrentText(previous)
        self.metric_combo.blockSignals(False)
        self.metric_combo.setEnabled(True)
        self.compare_button.setEnabled(len(snapshots) > 1)
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh")
        self._update_source_badge()
        self._hide_progress()
        self.metric_changed()
        self._show_movement(movement, latest_summary)

    def _family_failed(self, generation: int, family: str, message: str):
        if generation != self._family_generation or family != self.family_combo.currentText():
            return
        self.metric_combo.clear()
        self.metric_combo.addItem("Analysis unavailable")
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh")
        self._update_source_badge("!")
        self._hide_progress()
        self.card_current.set_data("—", message)

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

    def _show_movement(self, series, latest_summary):
        self.change_bars.set_series(series)
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
                f"{snapshot.row_count:,}" if snapshot.row_count >= 0 else "On demand",
                str(len(snapshot.headers)) if snapshot.headers else "On demand",
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
        self._comparison_generation += 1
        generation = self._comparison_generation
        self.compare_button.setEnabled(False)
        self.compare_button.setText("Comparing…")
        self._show_progress(0, 0, "Comparing two CSV snapshots…")
        self._run_background(
            compare_snapshots,
            (baseline, latest, self.key_combo.currentText()),
            lambda summary: self._comparison_ready(generation, summary),
            lambda message: self._comparison_failed(generation, message),
        )

    def _comparison_ready(self, generation: int, summary: ComparisonSummary):
        if generation != self._comparison_generation:
            return
        self.current_comparison = summary
        self.compare_button.setEnabled(True)
        self.compare_button.setText("Compare")
        self._hide_progress()
        for title, value in (
            ("Added", summary.added),
            ("Removed", summary.removed),
            ("Changed", summary.changed),
            ("Stable", summary.stable),
        ):
            self.compare_cards[title].set_data(f"{value:,}", "rows")
        self.set_change_filter("All")

    def _comparison_failed(self, generation: int, message: str):
        if generation != self._comparison_generation:
            return
        self.compare_button.setEnabled(True)
        self.compare_button.setText("Compare")
        self._hide_progress()
        QMessageBox.warning(self, "Compare snapshots", message)

    def set_change_filter(self, value: str):
        self.current_filter = value
        for button in self.filter_buttons:
            button.setChecked(button.text() == value)
        self.apply_change_filters()

    def apply_change_filters(self):
        if not self.current_comparison:
            self.diff_table.setRowCount(0)
            self.diff_notice.hide()
            return
        needle = self.change_search.text().strip().lower()
        matching = []
        has_more = False
        for detail in self.current_comparison.details:
            if not self._detail_matches(detail, needle):
                continue
            if len(matching) >= DETAIL_TABLE_LIMIT:
                has_more = True
                break
            matching.append(detail)

        self.diff_table.setUpdatesEnabled(False)
        self.diff_table.setRowCount(len(matching))
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
                self.diff_table.setItem(row, column, item)
        self.diff_table.setUpdatesEnabled(True)
        if has_more:
            self.diff_notice.setText(
                f"Showing the first {DETAIL_TABLE_LIMIT:,} matching details for speed. "
                "Refine the filter, or export to include every match."
            )
            self.diff_notice.show()
        else:
            self.diff_notice.hide()

    def _detail_matches(self, detail: dict[str, str], needle: str) -> bool:
        if self.current_filter != "All" and detail["change"] != self.current_filter:
            return False
        if not needle:
            return True
        return needle in " ".join(detail.values()).lower()

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
            needle = self.change_search.text().strip().lower()
            for detail in self.current_comparison.details:
                if not self._detail_matches(detail, needle):
                    continue
                writer.writerow(
                    (
                        detail["change"],
                        detail["key"],
                        detail["column"],
                        detail["before"],
                        detail["after"],
                    )
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


def application_icon_path() -> Path:
    return project_root() / "assets" / "diffasaurus-icon.png"


def main():
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName("Diffasaurus")
    icon_path = application_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    app.setFont(QFont(preferred_ui_font(), 11))
    app.setStyleSheet(diffasaurus_stylesheet())
    window = DiffasaurusWindow()
    window.show()
    sys.exit(app.exec())
