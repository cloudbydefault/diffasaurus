from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.user_dashboards import (
    UserDashboard,
    filter_summary_text,
    validate_dashboard_columns,
)
from diffasaurus.ui.dashboard_view import DashboardView

USER_DASHBOARD_CARD_MENU_STYLE = """
QMenu#userDashboardCardMenu {
    background-color: #121f2b;
    border: 1px solid #2a4559;
    padding: 6px 0px;
}
QMenu#userDashboardCardMenu::item {
    background-color: transparent;
    color: #d8e4ee;
    padding: 8px 18px;
    min-width: 168px;
}
QMenu#userDashboardCardMenu::item:selected {
    background-color: #23465a;
}
QMenu#userDashboardCardMenu::separator {
    height: 1px;
    background: #2a4559;
    margin: 4px 10px;
}
QMenu#userDashboardCardMenu::item:last {
    color: #f87171;
}
"""


def user_dashboard_card_menu_stylesheet() -> str:
    return USER_DASHBOARD_CARD_MENU_STYLE


class UserDashboardCard(QFrame):
    open_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)
    duplicate_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    export_requested = pyqtSignal(str)

    def __init__(
        self,
        dashboard: UserDashboard,
        *,
        missing_columns: list[str],
        parent=None,
    ):
        super().__init__(parent)
        self.dashboard = dashboard
        self.setObjectName("metricCard")
        self.setMinimumHeight(132)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(dashboard.name)
        title.setStyleSheet("font-size:15px; font-weight:700;")
        header.addWidget(title, 1)
        self.menu_button = QToolButton()
        self.menu_button.setText("⋯")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setToolTip("Dashboard actions")
        self.action_menu = QMenu(self.menu_button)
        self.action_menu.setObjectName("userDashboardCardMenu")
        self.action_menu.setStyleSheet(user_dashboard_card_menu_stylesheet())
        self._open_action = self.action_menu.addAction(
            "Open",
            lambda: self.open_requested.emit(dashboard.id),
        )
        self._edit_action = self.action_menu.addAction(
            "Edit",
            lambda: self.edit_requested.emit(dashboard.id),
        )
        self._duplicate_action = self.action_menu.addAction(
            "Duplicate",
            lambda: self.duplicate_requested.emit(dashboard.id),
        )
        self._export_action = self.action_menu.addAction(
            "Export CSV",
            lambda: self.export_requested.emit(dashboard.id),
        )
        self.action_menu.addSeparator()
        self._delete_action = self.action_menu.addAction(
            "Delete",
            lambda: self.delete_requested.emit(dashboard.id),
        )
        self.menu_button.clicked.connect(self._show_action_menu)
        header.addWidget(self.menu_button)
        layout.addLayout(header)

        if dashboard.description:
            description = QLabel(dashboard.description)
            description.setWordWrap(True)
            description.setStyleSheet("color:#8295a8; font-size:11px;")
            layout.addWidget(description)

        summary = QLabel(filter_summary_text(dashboard))
        summary.setWordWrap(True)
        summary.setStyleSheet("color:#c7d4df; font-size:11px;")
        layout.addWidget(summary)

        if missing_columns:
            state = QLabel("Needs attention · Missing: " + ", ".join(missing_columns))
            state.setWordWrap(True)
            state.setStyleSheet("color:#f5b942; font-size:11px; font-weight:650;")
        else:
            state = QLabel("Compatible with current snapshot")
            state.setStyleSheet("color:#4fd1a5; font-size:11px; font-weight:650;")
        layout.addWidget(state)

        open_button = QPushButton("Open")
        open_button.setObjectName("secondaryButton")
        open_button.clicked.connect(lambda: self.open_requested.emit(dashboard.id))
        layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignLeft)

    def _show_action_menu(self) -> None:
        button = self.menu_button
        menu = self.action_menu
        menu_width = menu.sizeHint().width()
        top_right = button.mapToGlobal(button.rect().topRight())
        menu.popup(
            QPoint(top_right.x() - menu_width, top_right.y() + button.height() + 4)
        )

    def menu_action_texts(self) -> list[str]:
        return [
            action.text()
            for action in self.action_menu.actions()
            if action.text() and not action.isSeparator()
        ]


class UserDashboardWorkspace(QWidget):
    open_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)
    duplicate_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    export_requested = pyqtSignal(str)
    new_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.tabs = QTabWidget()
        self.builtin_view = DashboardView()
        self.my_dashboards_page = QWidget()
        my_layout = QVBoxLayout(self.my_dashboards_page)
        my_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_label = QLabel(
            "No custom dashboards for this report family.\n"
            "Create one from scratch, or configure a Table view and use "
            "Save as dashboard."
        )
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color:#8295a8; font-size:13px;")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.grid = QGridLayout(self.scroll_content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.scroll.setWidget(self.scroll_content)
        my_layout.addWidget(self.empty_label)
        my_layout.addWidget(self.scroll, 1)
        self.tabs.addTab(self.builtin_view, "Built-in")
        self.tabs.addTab(self.my_dashboards_page, "My dashboards")
        layout.addWidget(self.tabs, 1)

    @property
    def apply_filter_requested(self):
        return self.builtin_view.apply_filter_requested

    def build_builtin_dashboard(self, title: str, stats: list[dict]) -> None:
        self.builtin_view.build_dashboard(title, stats)

    def set_dashboards(
        self,
        dashboards: list[UserDashboard],
        headers: list[str] | tuple[str, ...],
    ) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        has_dashboards = bool(dashboards)
        self.empty_label.setVisible(not has_dashboards)
        self.scroll.setVisible(has_dashboards)
        for index, dashboard in enumerate(dashboards):
            missing = validate_dashboard_columns(dashboard, headers)
            card = UserDashboardCard(dashboard, missing_columns=missing)
            card.open_requested.connect(self.open_requested.emit)
            card.edit_requested.connect(self.edit_requested.emit)
            card.duplicate_requested.connect(self.duplicate_requested.emit)
            card.delete_requested.connect(self.delete_requested.emit)
            card.export_requested.connect(self.export_requested.emit)
            self.grid.addWidget(card, index // 2, index % 2)
        if has_dashboards:
            self.grid.setRowStretch((len(dashboards) + 1) // 2, 1)
