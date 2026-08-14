from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHeaderView,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
)

from diffasaurus.core.report_history import detail_identity

MEMBERSHIP_FAMILY = "Entra_Group_User_Memberships"
MEMBERSHIP_IDENTITY_MIN_WIDTH = 300
MEMBERSHIP_ROW_MIN_HEIGHT = 48
IDENTITY_ARROW = " → "

CHANGE_COLORS = {
    "Added": "#4fd1a5",
    "Removed": "#fb7185",
    "Changed": "#f5b942",
}


class MembershipIdentityDelegate(QStyledItemDelegate):
    def initStyleOption(self, option: QStyleOptionViewItem, index):
        super().initStyleOption(option, index)
        if index.column() == 1:
            option.textElideMode = Qt.TextElideMode.ElideNone
            option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText


def membership_identity_display_text(identity: str) -> str:
    if IDENTITY_ARROW in identity:
        user, group = identity.split(IDENTITY_ARROW, 1)
        return f"{user}\n→ {group}"
    return identity


def identity_display_text(detail: dict[str, str], family: str | None) -> str:
    identity = detail_identity(detail)
    if family == MEMBERSHIP_FAMILY:
        return membership_identity_display_text(identity)
    return identity


def identity_tooltip(detail: dict[str, str]) -> str:
    identity = detail_identity(detail)
    parts = [identity]
    if detail.get("user_id"):
        parts.append(f"UserId: {detail['user_id']}")
    if detail.get("group_id"):
        parts.append(f"GroupId: {detail['group_id']}")
    if detail.get("access_package_id"):
        parts.append(f"AccessPackageId: {detail['access_package_id']}")
    if detail.get("policy_id"):
        parts.append(f"PolicyId: {detail['policy_id']}")
    if identity != detail.get("key", ""):
        parts.append(f"Key: {detail['key']}")
    return "\n".join(parts)


def configure_comparison_detail_table(
    table: QTableWidget,
    family: str | None,
) -> None:
    header = table.horizontalHeader()
    if family == MEMBERSHIP_FAMILY:
        table.setWordWrap(True)
        table.setItemDelegateForColumn(1, MembershipIdentityDelegate(table))
        table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.verticalHeader().setMinimumSectionSize(MEMBERSHIP_ROW_MIN_HEIGHT)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        if table.columnWidth(1) < MEMBERSHIP_IDENTITY_MIN_WIDTH:
            table.setColumnWidth(1, MEMBERSHIP_IDENTITY_MIN_WIDTH)
        return
    table.setWordWrap(False)
    table.setItemDelegateForColumn(1, None)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    table.verticalHeader().setDefaultSectionSize(34)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)


def populate_comparison_detail_table(
    table: QTableWidget,
    details: list[dict[str, str]],
    *,
    family: str | None = None,
    default_text_color: str = "#f2f7fb",
) -> None:
    table.setUpdatesEnabled(False)
    table.setRowCount(len(details))
    for row, detail in enumerate(details):
        identity = identity_display_text(detail, family)
        values = (
            detail["change"],
            identity,
            detail["column"],
            detail["before"],
            detail["after"],
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 1:
                item.setToolTip(identity_tooltip(detail))
            if column == 0:
                item.setForeground(
                    QColor(CHANGE_COLORS.get(value, default_text_color))
                )
                item.setFont(
                    QFont(
                        item.font().family(),
                        item.font().pointSize(),
                        QFont.Weight.Bold,
                    )
                )
            table.setItem(row, column, item)
    if family == MEMBERSHIP_FAMILY:
        table.resizeRowsToContents()
        for row in range(table.rowCount()):
            table.setRowHeight(row, max(table.rowHeight(row), MEMBERSHIP_ROW_MIN_HEIGHT))
    table.setUpdatesEnabled(True)
