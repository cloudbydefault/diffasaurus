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

from diffasaurus.core.report_history import detail_identity, is_android_devices_family

MEMBERSHIP_FAMILY = "Entra_Group_User_Memberships"
USER_ACTIVITY_FAMILY = "Entra_Users_Activity"
AUTH_METHODS_HYBRID_FAMILY = "Entra_Users_AuthenticationMethods_Hybrid"
USER_PROPERTIES_FAMILY = "Entra_Users_Properties"
USER_ORIENTED_FAMILIES = frozenset(
    {
        USER_ACTIVITY_FAMILY,
        AUTH_METHODS_HYBRID_FAMILY,
        USER_PROPERTIES_FAMILY,
    }
)
MEMBERSHIP_IDENTITY_MIN_WIDTH = 300
USER_ORIENTED_PROPERTY_MIN_WIDTH = 240
MEMBERSHIP_ROW_MIN_HEIGHT = 48
IDENTITY_ARROW = " → "
IDENTITY_SEPARATOR = " · "

USER_ACTIVITY_PROPERTY_LABELS: dict[str, str] = {
    "DisplayName": "Display name",
    "UPN": "User principal name",
    "Mail": "Mail",
    "UserType": "User type",
    "AccountEnabled": "Account enabled",
    "JobTitle": "Job title",
    "CompanyName": "Company",
    "Department": "Department",
    "Country": "Country",
    "City": "City",
    "CreatedDateTime": "Created",
    "LastPasswordChangeDateTime": "Last password change",
    "OnPremisesSyncEnabled": "On-premises sync enabled",
    "LastInteractiveSignInDateTime": "Last interactive sign-in",
    "LastNonInteractiveSignInDateTime": "Last non-interactive sign-in",
    "LastSuccessfulSignInDateTime": "Last successful sign-in",
}

AUTH_METHODS_HYBRID_PROPERTY_LABELS: dict[str, str] = {
    "DisplayName": "Display name",
    "UPN": "User principal name",
    "UserType": "User type",
    "AccountEnabled": "Account enabled",
    "JobTitle": "Job title",
    "CompanyName": "Company",
    "Department": "Department",
    "Country": "Country",
    "City": "City",
    "IsSystemPreferredAuthenticationMethodEnabled": "System-preferred authentication enabled",
    "UserPreferredMethodForSecondaryAuthentication": "User-preferred secondary authentication",
    "SystemPreferredAuthenticationMethod": "System-preferred authentication method",
    "AuthenticationMethods": "Authentication methods",
    "IsAdmin": "Administrator",
    "IsMfaRegistered": "MFA registered",
    "IsMfaCapable": "MFA capable",
    "IsPasswordlessCapable": "Passwordless capable",
    "IsSsprRegistered": "SSPR registered",
    "IsSsprEnabled": "SSPR enabled",
    "IsSsprCapable": "SSPR capable",
    "DefaultMfaMethod": "Default MFA method",
    "MethodsRegistered": "Methods registered",
    "SystemPreferredAuthenticationMethods": "System-preferred authentication methods",
    "ReportSource": "Report source",
}

USER_PROPERTIES_PROPERTY_LABELS: dict[str, str] = {
    "Id": "User ID",
    "DisplayName": "Display name",
    "GivenName": "Given name",
    "Surname": "Surname",
    "UPN": "User principal name",
    "Mail": "Mail",
    "MailNickname": "Mail nickname",
    "UserType": "User type",
    "AccountEnabled": "Account enabled",
    "CreatedDateTime": "Created",
    "Identities": "Sign-in identities",
    "StreetAddress": "Street address",
    "City": "City",
    "State": "State",
    "PostalCode": "Postal code",
    "Country": "Country",
    "BusinessPhones": "Business phones",
    "MobilePhone": "Mobile phone",
    "OtherMails": "Other email addresses",
    "ProxyAddresses": "Proxy addresses",
    "IMAddresses": "IM addresses",
    "JobTitle": "Job title",
    "CompanyName": "Company",
    "Department": "Department",
    "OfficeLocation": "Office location",
    "EmployeeId": "Employee ID",
    "EmployeeType": "Employee type",
    "EmployeeHireDate": "Employee hire date",
    "UsageLocation": "Usage location",
    "PreferredLanguage": "Preferred language",
    "PreferredDataLocation": "Preferred data location",
    "OnPremisesSyncEnabled": "On-premises sync enabled",
    "OnPremisesLastSyncDateTime": "Last on-premises sync",
    "OnPremisesDistinguishedName": "On-premises distinguished name",
    "OnPremisesImmutableId": "On-premises immutable ID",
    "ExtensionAttributes": "Extension attributes",
    "ManagerDisplayName": "Manager display name",
    "ManagerUPN": "Manager UPN",
    "Sponsors": "Sponsors",
}

ANDROID_DEVICE_PROPERTY_LABELS: dict[str, str] = {
    "DeviceName": "Device name",
    "ManagementName": "Management name",
    "IntuneDeviceId": "Intune device ID",
    "EntraDeviceId": "Entra device ID",
    "SerialNumber": "Serial number",
    "Manufacturer": "Manufacturer",
    "Model": "Model",
    "OperatingSystem": "Operating system",
    "OSVersion": "OS version",
    "AndroidSecurityPatchLevel": "Android security patch level",
    "UserDisplayName": "User display name",
    "UserPrincipalName": "User principal name",
    "EmailAddress": "Email address",
    "PhoneNumber": "Phone number",
    "IMEI": "IMEI",
    "MEID": "MEID",
    "ICCID": "ICCID",
    "SubscriberCarrier": "Subscriber carrier",
    "WiFiMacAddress": "Wi-Fi MAC address",
    "OwnerType": "Ownership",
    "ManagementAgent": "Management agent",
    "DeviceEnrollmentType": "Enrollment type",
    "EnrollmentProfileName": "Enrollment profile",
    "DeviceRegistrationState": "Registration state",
    "EnrolledDateTime": "Enrolled",
    "ManagementCertificateExpiration": "Management certificate expiration",
    "LastSyncDateTime": "Last sync",
    "DaysSinceLastSync": "Days since last sync",
    "DeviceActivityStatus": "Activity status",
    "ComplianceState": "Compliance state",
    "ComplianceGracePeriodExpiration": "Compliance grace period expiration",
    "AzureADRegistered": "Entra registered",
    "IsEncrypted": "Encrypted",
    "Rooted": "Rooted",
    "PartnerReportedThreatState": "Threat state",
    "EASActivated": "Exchange ActiveSync enabled",
    "EASDeviceId": "Exchange ActiveSync device ID",
    "EASActivationDateTime": "Exchange ActiveSync activation",
    "TotalStorageGB": "Total storage (GB)",
    "FreeStorageGB": "Free storage (GB)",
}

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


def _family_property_labels(family: str | None) -> dict[str, str]:
    if family == USER_ACTIVITY_FAMILY:
        return USER_ACTIVITY_PROPERTY_LABELS
    if family == AUTH_METHODS_HYBRID_FAMILY:
        return AUTH_METHODS_HYBRID_PROPERTY_LABELS
    if family == USER_PROPERTIES_FAMILY:
        return USER_PROPERTIES_PROPERTY_LABELS
    if is_android_devices_family(family):
        return ANDROID_DEVICE_PROPERTY_LABELS
    return {}


def property_display_text(column: str, family: str | None, *, change: str = "") -> str:
    if is_android_devices_family(family):
        if not column and change in {"Added", "Removed"}:
            return "Device"
        return ANDROID_DEVICE_PROPERTY_LABELS.get(column, column)
    if family not in USER_ORIENTED_FAMILIES:
        return column
    if not column and change in {"Added", "Removed"}:
        return "User"
    return _family_property_labels(family).get(column, column)


def property_tooltip(column: str, family: str | None) -> str:
    labels = _family_property_labels(family)
    if (family not in USER_ORIENTED_FAMILIES and not is_android_devices_family(family)) or not column:
        return ""
    label = labels.get(column, column)
    if label == column:
        return column
    return f"{label}\nCSV field: {column}"


def identity_tooltip(detail: dict[str, str], family: str | None = None) -> str:
    identity = identity_display_text(detail, family)
    if is_android_devices_family(family):
        parts = [identity]
        if detail.get("device_name"):
            parts.append(f"DeviceName: {detail['device_name']}")
        if detail.get("serial_number"):
            parts.append(f"SerialNumber: {detail['serial_number']}")
        if detail.get("entra_device_id"):
            parts.append(f"EntraDeviceId: {detail['entra_device_id']}")
        if detail.get("intune_device_id"):
            parts.append(f"IntuneDeviceId: {detail['intune_device_id']}")
        upn = detail.get("UserPrincipalName")
        if upn:
            parts.append(f"UserPrincipalName: {upn}")
        return "\n".join(parts)
    if family in USER_ORIENTED_FAMILIES:
        parts = [identity]
        if family == USER_ACTIVITY_FAMILY and detail.get("user_id"):
            parts.append(f"UserId: {detail['user_id']}")
        if family == AUTH_METHODS_HYBRID_FAMILY and detail.get("microsoft_report_id"):
            parts.append(f"MicrosoftReportId: {detail['microsoft_report_id']}")
        if family == USER_PROPERTIES_FAMILY and detail.get("user_id"):
            parts.append(f"Id: {detail['user_id']}")
        upn = detail.get("UPN") or (
            detail.get("key", "") if "@" in detail.get("key", "") else ""
        )
        if upn:
            parts.append(f"UPN: {upn}")
        return "\n".join(parts)
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
    if family in USER_ORIENTED_FAMILIES or is_android_devices_family(family):
        table.setWordWrap(False)
        table.setItemDelegateForColumn(1, None)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.verticalHeader().setDefaultSectionSize(34)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        if table.columnWidth(1) < MEMBERSHIP_IDENTITY_MIN_WIDTH:
            table.setColumnWidth(1, MEMBERSHIP_IDENTITY_MIN_WIDTH)
        if table.columnWidth(2) < USER_ORIENTED_PROPERTY_MIN_WIDTH:
            table.setColumnWidth(2, USER_ORIENTED_PROPERTY_MIN_WIDTH)
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
        property_name = property_display_text(
            detail["column"],
            family,
            change=detail["change"],
        )
        values = (
            detail["change"],
            identity,
            property_name,
            detail["before"],
            detail["after"],
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 1:
                item.setToolTip(identity_tooltip(detail, family))
            if column == 2:
                tip = property_tooltip(detail["column"], family)
                if tip:
                    item.setToolTip(tip)
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
