from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QCloseEvent, QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.entity.feature import persistent_entity_index_enabled
from diffasaurus.core.entity.index_paths import entity_index_path, normalize_reports_path, source_key
from diffasaurus.core.entity.history import reconstruct_entity_state, reconstruct_point_in_time_with_enrichment
from diffasaurus.core.entity.resolution import EntityIndexCancelled, build_entity_resolver
from diffasaurus.core.entity.types import EntityIndexStats
from diffasaurus.core.report_history import (
    ComparisonSummary,
    ReportSnapshot,
    aggregate_recent_changes,
    analyze_snapshot,
    common_headers,
    compare_snapshots,
    filter_history_by_days,
    metric_series,
    recent_movement,
    report_run_health,
    save_analysis_cache,
    scan_report_index,
    schema_changes,
    suggested_key,
)
from diffasaurus.core.paths import project_root
from diffasaurus.core.settings import get_active_reports_dir
from diffasaurus.ui.report_runner import CATALOG_FAMILY_ORDER, RunScriptsDialog
from diffasaurus.ui.source_settings import ReportSourceSettingsDialog
from diffasaurus.ui.charts import ChangeBars, LineChart
from diffasaurus.ui.entity_history import EntityHistoryPage
from diffasaurus.ui.point_in_time import PointInTimePage
from diffasaurus.ui.recent_changes import RecentChangesPage
from diffasaurus.ui.entity_index_controller import EntityIndexController
from diffasaurus.ui.progress_coordinator import ProgressCoordinator
from diffasaurus.ui.snapshot_explorer import SnapshotExplorer


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
ENTITY_INDEX_SHUTDOWN_WAIT_MS = 3_000

logger = logging.getLogger(__name__)


def _build_entity_index_task(
    families: dict[str, list[ReportSnapshot]],
    cancelled: threading.Event,
    progress=None,
):
    family_count = len(families)
    snapshot_count = sum(len(snapshots) for snapshots in families.values())
    started = time.perf_counter()
    stats = EntityIndexStats()
    logger.info(
        "Legacy entity index build started: %d families, %d snapshots",
        family_count,
        snapshot_count,
    )
    try:
        resolver = build_entity_resolver(
            families,
            cancelled=cancelled,
            stats=stats,
            progress=progress,
        )
    except EntityIndexCancelled:
        logger.info(
            "Legacy entity index build cancelled after %.1fs (%d/%d snapshots)",
            time.perf_counter() - started,
            stats.snapshots_scanned,
            snapshot_count,
        )
        return None
    elapsed = time.perf_counter() - started
    logger.info(
        "Legacy entity index build finished in %.1fs: %d entities, "
        "%d parsed, %d cache hits, %d snapshots",
        elapsed,
        stats.entity_count,
        stats.csv_parsed,
        stats.csv_cache_hits,
        stats.snapshots_scanned,
    )
    return resolver, stats


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    partial = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    progress = pyqtSignal(int, int, str)


class BackgroundTask(QRunnable):
    def __init__(
        self,
        function,
        *args,
        with_progress: bool = False,
        with_partial: bool = False,
    ):
        super().__init__()
        self.function = function
        self.args = args
        self.with_progress = with_progress
        self.with_partial = with_partial
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            callbacks = {}
            if self.with_progress:
                callbacks["progress"] = self.signals.progress.emit
            if self.with_partial:
                callbacks["partial"] = self.signals.partial.emit
            result = self.function(*self.args, **callbacks)
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
    partial=None,
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
        if partial and (index == 1 or index == total or index % 20 == 0):
            partial((title, list(history)))
        if progress:
            progress(index, total, snapshot.path.name)
    if cancelled.is_set():
        return None
    if progress:
        progress(total, total, "Comparing recent changes")
    hydrated = [snapshot for snapshot, _metrics in history]
    movement, latest_summary = recent_movement(hydrated, max_intervals=12)
    save_analysis_cache()
    return title, history, movement, latest_summary, schema_changes(hydrated)


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
        self.setMinimumSize(640, 480)
        self._screen_fitted = False
        self._screen_signal_connected = False
        self.report_dir = get_active_reports_dir()
        self.families: dict[str, list[ReportSnapshot]] = {}
        self.current_history: list[tuple[ReportSnapshot, dict[str, float]]] = []
        self.current_schema_changes = []
        self.current_comparison: ComparisonSummary | None = None
        self.current_filter = "All"
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(2)
        self._entity_index_pool = QThreadPool(self)
        self._entity_index_pool.setMaxThreadCount(1)
        self._workers: set[BackgroundTask] = set()
        self._entity_index_workers: set[BackgroundTask] = set()
        self._entity_index_worker: BackgroundTask | None = None
        self._index_generation = 0
        self._family_generation = 0
        self._comparison_generation = 0
        self._recent_changes_generation = 0
        self._recent_detail_generation = 0
        self._entity_index_generation = 0
        self._entity_changes_generation = 0
        self._pit_generation = 0
        self._shutdown_requested = False
        self._entity_resolver = None
        self._entity_resolver_index_generation = -1
        self._entity_index_building = False
        self._entity_index_target_report_generation = -1
        self._entity_index_cancelled = threading.Event()
        self._entity_index_cancelled.set()
        self._persistent_entity_index = persistent_entity_index_enabled()
        logger.info(
            "Entity index mode: %s",
            "persistent SQLite" if self._persistent_entity_index else "legacy in-memory",
        )
        self._progress_coordinator = ProgressCoordinator()
        self._entity_index_controller: EntityIndexController | None = None
        if self._persistent_entity_index:
            self._entity_index_controller = EntityIndexController(self)
            self._entity_index_controller.progress.connect(self._entity_sync_progress)
            self._entity_index_controller.finished.connect(self._entity_sync_finished)
            self._entity_index_controller.failed.connect(self._entity_sync_failed)
            self._progress_coordinator.set_global_handler(self._coordinated_show_progress)
            self._progress_coordinator.set_entity_handler(self._entity_sync_detail)
        self._pending_family = ""
        self._preferred_metric = ""
        self._family_cancelled = threading.Event()
        self._family_timer = QTimer(self)
        self._family_timer.setSingleShot(True)
        self._family_timer.setInterval(250)
        self._family_timer.timeout.connect(self._start_family_analysis)
        self._build_ui()
        self._wire()
        if self._persistent_entity_index and self._entity_index_controller is not None:
            self._log_persistent_entity_index_paths(self.report_dir)
            repository = self._entity_index_controller.open_existing(self.report_dir)
            if repository is not None:
                self._apply_entity_repository(repository)
            else:
                self._request_persistent_entity_sync(
                    cold=True,
                    reason="missing database on startup",
                )
        self.refresh_history()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(246)
        self.sidebar = sidebar
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
        for label in (
            "◉   Recent changes",
            "◇   Entity history",
            "◷   Point-in-Time",
            "◈   Dig site",
            "▦   Run health",
            "▤   Fossil library",
            "⇄   Compare snapshots",
            "▥   Explore snapshots",
        ):
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
        self.content_layout = outer

        top = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading_box.setSpacing(2)
        self.page_title = QLabel("Recent changes")
        self.page_title.setStyleSheet("font-size: 28px; font-weight: 750; letter-spacing:-0.5px;")
        self.page_subtitle = QLabel(
            "See what changed across every supported report since your last collections."
        )
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
        self.family_label = QLabel("REPORT FAMILY")
        self.family_label.setObjectName("fieldLabel")
        family_box.addWidget(self.family_label)
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
        self.recent_changes_page = RecentChangesPage()
        self.stack.addWidget(self.recent_changes_page)
        self.entity_history_page = EntityHistoryPage()
        self.stack.addWidget(self.entity_history_page)
        self.point_in_time_page = PointInTimePage()
        self.stack.addWidget(self.point_in_time_page)
        self.overview_page = self._build_overview()
        overview_scroll = QScrollArea()
        overview_scroll.setObjectName("overviewScroll")
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        overview_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        overview_scroll.setWidget(self.overview_page)
        self.stack.addWidget(overview_scroll)
        self.stack.addWidget(self._build_run_health())
        self.stack.addWidget(self._build_library())
        self.stack.addWidget(self._build_compare())
        self.snapshot_explorer = SnapshotExplorer()
        self.stack.addWidget(self.snapshot_explorer)
        outer.addWidget(self.stack, 1)
        shell.addWidget(content, 1)
        self.family_label.setVisible(False)
        self.family_combo.setVisible(False)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._screen_signal_connected and self.windowHandle():
            self.windowHandle().screenChanged.connect(self._fit_to_screen)
            self._screen_signal_connected = True
        if not self._screen_fitted:
            self._screen_fitted = True
            QTimer.singleShot(0, self._fit_to_screen)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width(), event.size().height())

    def _fit_to_screen(self, screen=None):
        screen = screen or self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        target_width = max(720, int(available.width() * 0.94))
        target_height = max(600, int(available.height() * 0.90))
        width = min(1440, target_width, available.width())
        height = min(900, target_height, available.height())
        self.resize(width, height)
        frame = self.frameGeometry()
        extra_width = max(0, frame.width() - self.width())
        extra_height = max(0, frame.height() - self.height())
        if frame.width() > available.width() or frame.height() > available.height():
            width = min(width, max(480, available.width() - extra_width))
            height = min(height, max(400, available.height() - extra_height))
            self.resize(width, height)
            frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())
        self._apply_responsive_layout(width, height)

    def _apply_responsive_layout(self, width: int, height: int | None = None):
        if not hasattr(self, "sidebar"):
            return
        height = height or self.height()
        narrow = width < 1_000
        compact = width < 1_250
        short = height < 1_000
        very_short = height < 720
        self.sidebar.setFixedWidth(180 if narrow else 210 if compact else 246)
        horizontal_margin = 16 if narrow else 24 if compact else 34
        vertical_margin = 18 if compact else 28
        self.content_layout.setContentsMargins(
            horizontal_margin,
            vertical_margin,
            horizontal_margin,
            20 if compact else 30,
        )
        self.content_layout.setSpacing(14 if narrow else 17 if compact else 22)
        self.family_combo.setMinimumWidth(180 if narrow else 240 if compact else 330)
        self.metric_combo.setMinimumWidth(150 if narrow else 200 if compact else 260)
        self.metric_combo.setMaximumWidth(190 if narrow else 280 if compact else 16777215)
        self.range_combo.setMaximumWidth(110 if narrow else 140)
        self.aggregation_combo.setMaximumWidth(140)
        self.aggregation_combo.setVisible(not narrow)
        self.source_badge.setVisible(not narrow)
        self.page_subtitle.setVisible(not narrow)
        if hasattr(self, "line_chart"):
            self.overview_layout.setSpacing(8 if very_short else 10 if short else 16)
            self.overview_page.setMinimumHeight(
                520 if very_short else 630 if short else 800
            )
            line_height = 140 if very_short else 190 if short else 260
            movement_height = 120 if very_short else 170 if short else 230
            self.line_chart.setMinimumHeight(line_height)
            self.change_bars.setMinimumHeight(movement_height)
            self.line_chart.setMaximumHeight(175 if very_short else 220 if short else 16777215)
            self.change_bars.setMaximumHeight(
                145 if very_short else 190 if short else 16777215
            )
            self.movement_title.setFixedHeight(24)
            for card in (
                self.card_current,
                self.card_delta,
                self.card_changes,
                self.card_snapshots,
                self.health_coverage,
                self.health_observed,
                self.health_missing,
                self.health_latest,
            ):
                card.setMinimumHeight(88 if very_short else 100 if short else 118)
                card.setMaximumHeight(96 if very_short else 108 if short else 16777215)

    def _build_overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.overview_layout = layout
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
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
        self.range_combo = QComboBox()
        self.range_combo.setToolTip("Limit the visible history without deleting snapshots")
        for label, days in (
            ("30 days", 30),
            ("90 days", 90),
            ("1 year", 365),
            ("2 years", 730),
            ("All history", None),
        ):
            self.range_combo.addItem(label, days)
        self.range_combo.setCurrentText("1 year")
        chart_header.addWidget(self.range_combo)
        self.aggregation_combo = QComboBox()
        self.aggregation_combo.setToolTip(
            "Auto keeps daily detail for short ranges and summarizes long ranges"
        )
        for label, value in (
            ("Auto detail", "auto"),
            ("Daily", "daily"),
            ("Weekly", "weekly"),
            ("Monthly", "monthly"),
        ):
            self.aggregation_combo.addItem(label, value)
        chart_header.addWidget(self.aggregation_combo)
        layout.addLayout(chart_header)
        self.schema_badge = QLabel("Schema pending")
        self.schema_badge.setObjectName("schemaBadge")
        layout.addWidget(self.schema_badge, 0, Qt.AlignmentFlag.AlignRight)
        self.line_chart = LineChart()
        layout.addWidget(self.line_chart, 3)
        self.movement_title = QLabel("Movement between snapshots")
        self.movement_title.setObjectName("sectionTitle")
        self.movement_title.setContentsMargins(0, 3, 0, 0)
        layout.addWidget(self.movement_title)
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
        self.range_combo.currentIndexChanged.connect(self.metric_changed)
        self.aggregation_combo.currentIndexChanged.connect(self.metric_changed)
        self.library_search.textChanged.connect(self.filter_library)
        self.library_table.cellDoubleClicked.connect(self.open_library_snapshot)
        self.baseline_combo.currentIndexChanged.connect(self.snapshot_selection_changed)
        self.latest_combo.currentIndexChanged.connect(self.snapshot_selection_changed)
        self.compare_button.clicked.connect(self.run_comparison)
        self.change_search.textChanged.connect(self.apply_change_filters)
        self.recent_changes_page.period_changed.connect(self._refresh_recent_changes)
        self.recent_changes_page.details_requested.connect(self._load_recent_details)
        self.recent_changes_page.open_in_compare_requested.connect(self._open_recent_in_compare)
        self.entity_history_page.period_changed.connect(self._refresh_entity_period_changes)
        self.entity_history_page.entity_selected.connect(self._refresh_entity_period_changes)
        self.entity_history_page.refresh_requested.connect(
            lambda: self._ensure_entity_index(force=True, user_requested=True)
        )
        self.entity_history_page.view_at_date_requested.connect(self._open_point_in_time)
        self.point_in_time_page.refresh_requested.connect(
            lambda: self._ensure_entity_index(force=True, user_requested=True)
        )
        self.point_in_time_page.reconstruct_requested.connect(self._reconstruct_point_in_time)

    def show_page(self, index: int):
        for current, button in enumerate(self.nav_buttons):
            button.setChecked(current == index)
        self.stack.setCurrentIndex(index)
        on_landing = index in (0, 1, 2)
        self.family_label.setVisible(not on_landing)
        self.family_combo.setVisible(not on_landing)
        titles = (
            (
                "Recent changes",
                "See what changed across every supported report since your last collections.",
            ),
            (
                "Entity history",
                "Trace one user, device, or shared mailbox across every snapshot that knows about it.",
            ),
            (
                "Point-in-Time",
                "Reconstruct what was known about an entity at a selected date.",
            ),
            ("The dig site", "Unearthing your Microsoft 365 history, one CSV fossil at a time."),
            ("Scheduled run health", "See which weekday collections produced evidence—and which outputs are missing."),
            ("Fossil library", "Browse the CSV snapshots buried in your tenant timeline."),
            ("Compare snapshots", "Explain exactly what appeared, disappeared, or changed."),
            (
                "Snapshot explorer",
                "Inspect raw tenant data, combine filters, and open report-aware dashboards.",
            ),
        )
        self.page_title.setText(titles[index][0])
        self.page_subtitle.setText(titles[index][1])
        if index == 7:
            self.snapshot_explorer.activate()
        if index == 0:
            self._refresh_recent_changes()
        if index in (1, 2):
            self._ensure_entity_index()

    def _run_background(
        self,
        function,
        args,
        on_result,
        on_error,
        on_progress=None,
        on_partial=None,
        with_progress: bool = False,
        with_partial: bool = False,
    ):
        if self._shutdown_requested:
            return
        worker = BackgroundTask(
            function,
            *args,
            with_progress=with_progress,
            with_partial=with_partial,
        )
        self._workers.add(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        if on_partial:
            worker.signals.partial.connect(on_partial)
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        self.thread_pool.start(worker)

    def _run_entity_index_background(
        self,
        function,
        args,
        on_result,
        on_error,
        on_progress=None,
    ):
        if self._shutdown_requested:
            return
        worker = BackgroundTask(function, *args, with_progress=on_progress is not None)
        self._entity_index_workers.add(worker)
        self._entity_index_worker = worker
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(lambda w=worker: self._release_entity_index_worker(w))
        self._entity_index_pool.start(worker)

    def _release_entity_index_worker(self, worker: BackgroundTask) -> None:
        self._entity_index_workers.discard(worker)
        if self._entity_index_worker is worker:
            self._entity_index_worker = None

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

    def _coordinated_show_progress(self, current: int, total: int, label: str):
        if label:
            self._show_progress(current, total, label)
        else:
            self._hide_progress()

    def _log_persistent_entity_index_paths(self, reports_dir: Path) -> None:
        normalized = normalize_reports_path(reports_dir)
        db_path = entity_index_path(normalized)
        logger.info(
            "Persistent entity index paths: reports_dir=%s normalized=%s source_key=%s "
            "db_path=%s db_exists=%s",
            reports_dir,
            normalized,
            source_key(normalized),
            db_path,
            db_path.is_file(),
        )

    def _request_persistent_entity_sync(
        self,
        *,
        cold: bool = False,
        force: bool = False,
        reason: str = "",
    ) -> None:
        if self._entity_index_controller is None:
            return
        self.report_dir = get_active_reports_dir()
        self._log_persistent_entity_index_paths(self.report_dir)
        logger.info(
            "Requesting persistent entity index sync (%s) cold=%s force=%s",
            reason or "unspecified",
            cold,
            force,
        )
        self._entity_index_building = True
        self.entity_history_page.show_indexing()
        self.point_in_time_page.show_indexing()
        self._entity_index_controller.start_sync(
            self.report_dir,
            force=force,
            cold=cold,
        )
        self._progress_coordinator.start_task(
            "entity_sync",
            self._entity_index_controller.generation,
            foreground=True,
        )

    def _ensure_persistent_entity_sync_after_scan(self) -> None:
        if self._entity_index_controller is None:
            return
        self.report_dir = get_active_reports_dir()
        self._log_persistent_entity_index_paths(self.report_dir)
        if self._entity_index_controller.sync_state == "running":
            logger.info(
                "Persistent entity index sync already running (generation=%d)",
                self._entity_index_controller.generation,
            )
            return
        db_path = entity_index_path(self.report_dir)
        repository = self._entity_index_controller.open_existing(self.report_dir)
        if repository is not None:
            self._apply_entity_repository(repository)
            self._request_persistent_entity_sync(reason="incremental sync after report scan")
            return
        self._request_persistent_entity_sync(
            cold=True,
            reason="missing database after report scan",
        )

    def _entity_sync_detail(self, detail: str):
        self.entity_history_page.show_sync_progress(detail)
        self.point_in_time_page.show_sync_progress(detail)

    def _entity_sync_progress(self, payload: dict):
        generation = int(payload.get("generation", -1))
        if self._entity_index_controller is None:
            return
        if generation != self._entity_index_controller.generation:
            return
        phase = payload.get("phase", "indexing")
        if phase == "failed":
            self._entity_sync_failed(str(payload.get("label", "Entity index synchronization failed")))
            return
        discovered = int(payload.get("discovered", 0))
        total = int(payload.get("total", 0))
        parsed = int(payload.get("parsed", 0))
        reused = int(payload.get("reused", 0))
        failed = int(payload.get("failed", 0))
        unresolved = int(payload.get("unresolved", 0))
        label = payload.get("label") or phase
        phase_labels = {
            "discovering": "Discovering snapshots",
            "checking": "Checking indexed files",
            "repairing_projections": "Repairing entity search index",
            "building_user_device_links": "Building historical user-device links",
            "indexing": "Indexing files",
            "resolving_identities": "Resolving dependent identities",
            "recomputing_entities": "Recomputing affected entities",
            "checkpointing": "Checkpointing database",
            "publishing": "Publishing entity index",
            "finalizing": "Finalizing entity index",
            "complete": "Entity index complete",
            "completed_with_errors": "Entity index complete",
        }
        progress_label = phase_labels.get(phase, label)
        detail = (
            f"{discovered}/{total} discovered · parsed {parsed} · reused {reused} · "
            f"failed {failed} · unresolved {unresolved} · {label}"
        )
        if phase in ("indexing", "discovering", "checking"):
            current = parsed + reused + failed
            total_progress = max(total, 1)
        elif phase in (
            "repairing_projections",
            "building_user_device_links",
            "resolving_identities",
            "recomputing_entities",
            "checkpointing",
            "publishing",
            "finalizing",
        ):
            current = 1
            total_progress = 1
        else:
            current = parsed + reused + failed
            total_progress = max(total, 1)
        self._progress_coordinator.report_progress(
            "entity_sync",
            generation,
            current,
            total_progress,
            progress_label,
        )
        self._progress_coordinator.report_entity_detail(detail)

    def _entity_sync_finished(self, payload: dict):
        generation = int(payload.get("generation", -1))
        self._entity_index_building = False
        self._progress_coordinator.finish_task("entity_sync", generation)
        if self._entity_index_controller is None:
            return
        if generation != self._entity_index_controller.generation:
            return
        repository = self._entity_index_controller.open_existing(self.report_dir)
        if repository is not None:
            self._apply_entity_repository(repository)
            logger.info(
                "Persistent entity index repository refreshed after sync generation=%d",
                generation,
            )
        status = payload.get("status", "complete")
        if status in ("complete", "completed_with_errors"):
            failed = int(payload.get("failed", 0))
            suffix = f" ({failed} failed)" if failed else ""
            self.entity_history_page.entity_selector.status_label.setText(
                f"Index ready{suffix}."
            )
            self.point_in_time_page.entity_selector.status_label.setText(
                f"Index ready{suffix}."
            )

    def _entity_sync_failed(self, message: str):
        self._entity_index_building = False
        self._hide_progress()
        self._progress_coordinator.finish_task(
            "entity_sync",
            self._entity_index_controller.generation if self._entity_index_controller else -1,
        )
        if self._entity_index_controller and self._entity_index_controller.repository:
            self._apply_entity_repository(self._entity_index_controller.repository)
        self.entity_history_page.show_index_error(message)
        self.point_in_time_page.show_index_error(message)
        logger.error("Persistent entity index sync failed in UI: %s", message)

    def _apply_entity_repository(self, repository) -> None:
        self.entity_history_page.set_repository(repository)
        self.point_in_time_page.set_repository(repository)

    def refresh_history(self):
        selected = self.family_combo.currentText()
        self.report_dir = get_active_reports_dir()
        self._family_generation += 1
        self._family_cancelled.set()
        self._family_timer.stop()
        self._comparison_generation += 1
        self._index_generation += 1
        generation = self._index_generation
        self._progress_coordinator.start_task("history_scan", generation, foreground=True)
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
            lambda current, total, label: self._progress_coordinator.report_progress(
                "history_scan",
                generation,
                current,
                total,
                f"Indexing · {label}",
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
        self._refresh_recent_changes()
        if self._persistent_entity_index and self._entity_index_controller is not None:
            self._ensure_persistent_entity_sync_after_scan()
        else:
            self._ensure_entity_index(force=True)
        self.family_changed()
        self._progress_coordinator.finish_task("history_scan", generation)

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
        self.snapshot_explorer.set_snapshots(snapshots)
        if self.stack.currentIndex() == 7:
            self.snapshot_explorer.activate()
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
            lambda payload: self._family_partial(generation, family, payload),
            with_progress=True,
            with_partial=True,
        )

    def _family_partial(self, generation: int, family: str, payload):
        if generation != self._family_generation or family != self.family_combo.currentText():
            return
        _title, history = payload
        self.current_history = history
        available = []
        for _snapshot, metrics in history:
            for metric in metrics:
                if metric not in available:
                    available.append(metric)
        selected = self._preferred_metric or self.metric_combo.currentText()
        current_items = [
            self.metric_combo.itemText(index)
            for index in range(self.metric_combo.count())
        ]
        if current_items != available:
            self.metric_combo.blockSignals(True)
            self.metric_combo.clear()
            self.metric_combo.addItems(available)
            if selected in available:
                self.metric_combo.setCurrentText(selected)
            self.metric_combo.blockSignals(False)
        self.metric_combo.setEnabled(bool(available))
        self.metric_changed()

    def _family_ready(self, generation: int, family: str, payload):
        if generation != self._family_generation or family != self.family_combo.currentText():
            return
        if payload is None:
            return
        (
            _title,
            self.current_history,
            movement,
            latest_summary,
            self.current_schema_changes,
        ) = payload
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
        self._show_schema_changes()

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
        days = self.range_combo.currentData()
        aggregation = self.aggregation_combo.currentData() or "auto"
        values, labels, effective, visible_count = metric_series(
            self.current_history,
            metric,
            days=days,
            aggregation=aggregation,
        )
        self.line_chart.set_series(values, labels)
        raw_values = [
            metrics[metric]
            for _snapshot, metrics in filter_history_by_days(self.current_history, days)
            if metric in metrics
        ]
        current = raw_values[-1] if raw_values else 0
        previous = raw_values[-2] if len(raw_values) > 1 else current
        delta = current - previous
        delta_text = f"{delta:+,.0f}" if len(raw_values) > 1 else "—"
        self.card_current.set_data(f"{current:,.0f}", metric or "No metric")
        self.card_delta.set_data(delta_text, "versus previous snapshot")
        total = len(self.families.get(self.family_combo.currentText(), []))
        resolution = effective.capitalize()
        self.card_snapshots.set_data(
            str(total),
            f"{visible_count:,} in range · {resolution} chart",
        )

    def _show_schema_changes(self):
        changes = self.current_schema_changes
        if not changes:
            self.schema_badge.setText("✓  Schema stable")
            self.schema_badge.setStyleSheet(f"color:{COLORS['green']};")
            self.schema_badge.setToolTip("No column additions or removals found in this history.")
            return
        self.schema_badge.setText(
            f"△  {len(changes)} schema shift{'s' if len(changes) != 1 else ''}"
        )
        self.schema_badge.setStyleSheet(f"color:{COLORS['amber']};")
        descriptions = []
        for captured_at, added, removed in changes[-20:]:
            parts = []
            if added:
                parts.append("added " + ", ".join(added))
            if removed:
                parts.append("removed " + ", ".join(removed))
            descriptions.append(f"{captured_at:%d %b %Y}: {'; '.join(parts)}")
        self.schema_badge.setToolTip("\n".join(descriptions))

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
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, snapshot)
                self.library_table.setItem(row, column, item)
        self.filter_library()

    def open_library_snapshot(self, row: int, _column: int):
        item = self.library_table.item(row, 0)
        snapshot = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(snapshot, ReportSnapshot):
            return
        self.show_page(7)
        self.snapshot_explorer.select_snapshot(snapshot)

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

    def _ensure_entity_index(self, force: bool = False, user_requested: bool = False) -> None:
        if self._shutdown_requested or not self.families:
            return
        if self._persistent_entity_index:
            if self._entity_index_controller is None:
                return
            repository = self._entity_index_controller.repository
            if repository is not None:
                self._apply_entity_repository(repository)
            if force and self._entity_index_controller.sync_state != "running":
                self._entity_index_building = True
                self._entity_index_controller.start_sync(self.report_dir, force=True)
            return
        if (
            not force
            and self._entity_resolver is not None
            and self._entity_resolver_index_generation == self._index_generation
        ):
            self._apply_entity_resolver(self._entity_resolver)
            return
        if (
            self._entity_index_building
            and not user_requested
            and self._entity_index_target_report_generation == self._index_generation
        ):
            return
        if self._entity_index_building and not force:
            return
        self._start_entity_index_build(force=force)

    def _start_entity_index_build(self, force: bool = False) -> None:
        if self._shutdown_requested or not self.families:
            return
        if self._entity_index_building and not force:
            return
        if self._entity_index_building:
            logger.info(
                "Cancelling in-flight legacy entity index build (generation %d)",
                self._entity_index_generation,
            )
            self._entity_index_cancelled.set()
        self._entity_index_generation += 1
        generation = self._entity_index_generation
        self._entity_index_building = True
        self._entity_index_target_report_generation = self._index_generation
        self._entity_index_cancelled = threading.Event()
        cancelled = self._entity_index_cancelled
        family_count = len(self.families)
        snapshot_count = sum(len(snapshots) for snapshots in self.families.values())
        logger.info(
            "Starting legacy entity index build generation=%d report_generation=%d "
            "(%d families, %d snapshots)",
            generation,
            self._index_generation,
            family_count,
            snapshot_count,
        )
        self.entity_history_page.show_indexing()
        self.point_in_time_page.show_indexing()

        def _legacy_index_progress_ui(current: int, total: int, label: str) -> None:
            if generation != self._entity_index_generation:
                return
            detail = f"{current}/{total} snapshots · {label}"
            self.entity_history_page.show_index_progress(detail)
            self.point_in_time_page.show_index_progress(detail)

        self._run_entity_index_background(
            _build_entity_index_task,
            (self.families, cancelled),
            lambda result: self._entity_index_ready(generation, result),
            lambda message: self._entity_index_failed(generation, message),
            on_progress=_legacy_index_progress_ui,
        )

    def _clear_entity_index_ui(self) -> None:
        if self._entity_resolver is not None:
            self._apply_entity_resolver(self._entity_resolver)
            return
        self.entity_history_page.clear_index_state()
        self.point_in_time_page.clear_index_state()

    def _apply_entity_resolver(self, resolver) -> None:
        self.entity_history_page.set_resolver(resolver)
        self.point_in_time_page.set_resolver(resolver)

    def _entity_index_ready(self, generation: int, result):
        current_generation = self._entity_index_generation
        if generation != current_generation:
            logger.info(
                "Ignoring stale legacy entity index result "
                "(worker generation=%d current=%d building=%s)",
                generation,
                current_generation,
                self._entity_index_building,
            )
            return
        if self._shutdown_requested:
            logger.info("Ignoring legacy entity index result during shutdown")
            self._entity_index_building = False
            return
        self._entity_index_building = False
        if result is None:
            logger.warning(
                "Legacy entity index build generation=%d returned no result",
                generation,
            )
            self._clear_entity_index_ui()
            return
        resolver, stats = result
        self._entity_resolver = resolver
        self._entity_resolver_index_generation = self._index_generation
        self._apply_entity_resolver(resolver)
        logger.info(
            "Legacy entity index generation=%d installed on entity pages "
            "(%d entities, report generation=%d)",
            generation,
            stats.entity_count if stats is not None else len(resolver.records),
            self._index_generation,
        )
        if self.entity_history_page._selected:
            self._refresh_entity_period_changes()

    def _entity_index_failed(self, generation: int, message: str):
        if generation != self._entity_index_generation:
            logger.info(
                "Ignoring stale legacy entity index failure "
                "(worker generation=%d current=%d): %s",
                generation,
                self._entity_index_generation,
                message,
            )
            return
        self._entity_index_building = False
        if self._shutdown_requested:
            return
        logger.error("Legacy entity index build generation=%d failed: %s", generation, message)
        self.entity_history_page.show_index_error(message)
        self.point_in_time_page.show_index_error(message)
        QMessageBox.warning(self, "Entity index", message)

    def _open_point_in_time(self, record):
        self.show_page(2)
        self.point_in_time_page.select_entity(
            record,
            datetime.now().replace(microsecond=0),
        )

    def _reconstruct_point_in_time(self, record, target):
        self._pit_generation += 1
        generation = self._pit_generation
        self.point_in_time_page.show_loading()
        if (
            self._persistent_entity_index
            and self._entity_index_controller is not None
            and self._entity_index_controller.repository is not None
        ):
            repository = self._entity_index_controller.repository
            self._run_background(
                repository.reconstruct_point_in_time,
                (record.key, target),
                lambda result: self._point_in_time_ready(generation, record, result),
                lambda message: self._point_in_time_failed(generation, message),
            )
            return
        self._run_background(
            reconstruct_point_in_time_with_enrichment,
            (record.key, self.families, target),
            lambda result: self._point_in_time_ready(generation, record, result),
            lambda message: self._point_in_time_failed(generation, message),
        )

    def _point_in_time_ready(self, generation: int, record, result):
        if generation != self._pit_generation:
            return
        self.point_in_time_page.apply_reconstruction(result, record)

    def _point_in_time_failed(self, generation: int, message: str):
        if generation != self._pit_generation:
            return
        QMessageBox.warning(self, "Point-in-Time", message)

    def _refresh_entity_period_changes(self, _record=None):
        record = self.entity_history_page._selected
        if not record:
            return
        period, _label = self.entity_history_page.current_period()
        self._entity_changes_generation += 1
        generation = self._entity_changes_generation
        self.entity_history_page._load_period_changes(record)
        if (
            self._persistent_entity_index
            and self._entity_index_controller is not None
            and self._entity_index_controller.repository is not None
        ):
            repository = self._entity_index_controller.repository
            self._run_background(
                repository.period_changes,
                (record.key, period),
                lambda changes: self._entity_period_changes_ready(generation, changes),
                lambda message: self._entity_period_changes_failed(generation, message),
            )
            return
        self._run_background(
            EntityHistoryPage.compute_period_changes,
            (record, self.families, period),
            lambda changes: self._entity_period_changes_ready(generation, changes),
            lambda message: self._entity_period_changes_failed(generation, message),
        )

    def _entity_period_changes_ready(self, generation: int, changes):
        if generation != self._entity_changes_generation:
            return
        self.entity_history_page.apply_period_changes(changes)

    def _entity_period_changes_failed(self, generation: int, message: str):
        if generation != self._entity_changes_generation:
            return
        QMessageBox.warning(self, "Entity history", message)

    def _refresh_recent_changes(self):
        period, label = self.recent_changes_page.current_period()
        self._recent_changes_generation += 1
        generation = self._recent_changes_generation
        self.recent_changes_page.show_loading()
        self._run_background(
            lambda families, selected_period, period_label: aggregate_recent_changes(
                families,
                selected_period,
                period_label=period_label,
                family_order=CATALOG_FAMILY_ORDER,
            ),
            (self.families, period, label),
            lambda report: self._recent_changes_ready(generation, report),
            lambda message: self._recent_changes_failed(generation, message),
        )

    def _recent_changes_ready(self, generation: int, report):
        if generation != self._recent_changes_generation:
            return
        self.recent_changes_page.apply_report(report)

    def _recent_changes_failed(self, generation: int, message: str):
        if generation != self._recent_changes_generation:
            return
        QMessageBox.warning(self, "Recent changes", message)

    def _load_recent_details(self, family: str, baseline, latest, key_column: str):
        self._recent_detail_generation += 1
        generation = self._recent_detail_generation
        self._run_background(
            compare_snapshots,
            (baseline, latest, key_column),
            lambda summary: self._recent_detail_ready(generation, family, summary),
            lambda message: self._recent_detail_failed(generation, family, message),
        )

    def _recent_detail_ready(self, generation: int, family: str, summary: ComparisonSummary):
        if generation != self._recent_detail_generation:
            return
        self.recent_changes_page.set_family_details(family, summary)

    def _recent_detail_failed(self, generation: int, family: str, message: str):
        if generation != self._recent_detail_generation:
            return
        QMessageBox.warning(self, f"Recent changes · {family}", message)

    def _open_recent_in_compare(
        self,
        family: str,
        baseline: ReportSnapshot,
        latest: ReportSnapshot,
        key_column: str,
    ):
        if family in self.families:
            self.family_combo.setCurrentText(family)
        self.show_page(6)
        self._select_snapshot_in_combo(self.baseline_combo, baseline)
        self._select_snapshot_in_combo(self.latest_combo, latest)
        self.key_combo.setCurrentText(key_column)
        self.run_comparison()

    @staticmethod
    def _select_snapshot_in_combo(combo: QComboBox, snapshot: ReportSnapshot):
        for index in range(combo.count()):
            item = combo.itemData(index)
            if item == snapshot:
                combo.setCurrentIndex(index)
                return
            if isinstance(item, ReportSnapshot) and item.path == snapshot.path:
                combo.setCurrentIndex(index)
                return

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

    def closeEvent(self, event: QCloseEvent):
        self._shutdown_requested = True
        self._family_cancelled.set()
        self._entity_index_cancelled.set()
        self._index_generation += 1
        self._entity_index_generation += 1
        self._entity_index_building = False
        self._recent_changes_generation += 1
        self._entity_changes_generation += 1
        self._pit_generation += 1
        self._comparison_generation += 1
        self._family_generation += 1
        if self._entity_index_controller is not None:
            self._entity_index_controller.shutdown(ENTITY_INDEX_SHUTDOWN_WAIT_MS)
            self._entity_index_controller.close_repository()
        self._entity_index_cancelled.set()
        self._entity_index_pool.clear()
        self._entity_index_pool.waitForDone(ENTITY_INDEX_SHUTDOWN_WAIT_MS)
        self.thread_pool.clear()
        self.thread_pool.waitForDone(ENTITY_INDEX_SHUTDOWN_WAIT_MS)
        event.accept()

    def open_report_runner(self):
        dialog = RunScriptsDialog(self)
        dialog.exec()
        self.refresh_history()

    def open_source_settings(self):
        dialog = ReportSourceSettingsDialog(self)
        if dialog.exec():
            if self._persistent_entity_index and self._entity_index_controller is not None:
                self._entity_index_controller.close_repository()
                self.report_dir = get_active_reports_dir()
                repository = self._entity_index_controller.open_existing(self.report_dir)
                if repository is not None:
                    self._apply_entity_repository(repository)
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
        QFrame#runtimeCard {{
            background: #10212c;
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
        QLabel#schemaBadge {{
            background: #101f2b;
            border: 1px solid {COLORS['border']};
            border-radius: 8px;
            padding: 5px 9px;
            font-size: 10px;
            font-weight: 650;
        }}
        QLabel#dashboardSection {{
            background:#11202c;
            border-left:4px solid {COLORS['blue']};
            border-radius:7px;
            padding:8px 11px;
            margin-top:7px;
            font-size:13px;
            font-weight:750;
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
        QTableWidget, QTableView, QListWidget {{
            background: #0d1924;
            alternate-background-color: #101f2c;
            border: 1px solid {COLORS['border']};
            border-radius: 10px;
            selection-background-color: #23465a;
            selection-color: white;
        }}
        QListWidget::item {{ padding:5px 7px; }}
        QListWidget::item:hover {{ background:#162b39; }}
        QTableView::item {{ padding:3px 7px; border:0; }}
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
    import logging
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("diffasaurus.ui.main_window").setLevel(logging.INFO)
    logging.getLogger("diffasaurus.ui.entity_index_controller").setLevel(logging.INFO)
    logging.getLogger("diffasaurus.core.entity.index_sync").setLevel(logging.INFO)

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
