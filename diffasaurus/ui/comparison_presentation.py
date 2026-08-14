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

from diffasaurus.core.report_history import (
    delegate_collection_delta_summary,
    detail_identity,
    is_android_devices_family,
    is_autopilot_devices_family,
    is_ios_devices_family,
    is_managed_devices_family,
    is_role_assignments_family,
    is_shared_mailboxes_family,
)

MEMBERSHIP_FAMILY = "Entra_Group_User_Memberships"
USER_ACTIVITY_FAMILY = "Entra_Users_Activity"
AUTH_METHODS_HYBRID_FAMILY = "Entra_Users_AuthenticationMethods_Hybrid"
USER_PROPERTIES_FAMILY = "Entra_Users_Properties"
ROLE_ASSIGNMENTS_FAMILY = "Entra_Role_Assignments"
USER_ORIENTED_FAMILIES = frozenset(
    {
        USER_ACTIVITY_FAMILY,
        AUTH_METHODS_HYBRID_FAMILY,
        USER_PROPERTIES_FAMILY,
    }
)
MEMBERSHIP_IDENTITY_MIN_WIDTH = 300
USER_ORIENTED_PROPERTY_MIN_WIDTH = 240
ROLE_ASSIGNMENT_IDENTITY_MIN_WIDTH = 380
ROLE_ASSIGNMENT_PATH_MIN_WIDTH = 220
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

IOS_DEVICE_PROPERTY_LABELS: dict[str, str] = {
    "DeviceName": "Device name",
    "ManagementName": "Management name",
    "IntuneDeviceId": "Intune device ID",
    "EntraDeviceId": "Entra device ID",
    "UDID": "UDID",
    "SerialNumber": "Serial number",
    "IMEI": "IMEI",
    "MEID": "MEID",
    "Manufacturer": "Manufacturer",
    "Model": "Model",
    "OperatingSystem": "Operating system",
    "OSVersion": "OS version",
    "UserDisplayName": "User display name",
    "UserPrincipalName": "User principal name",
    "EmailAddress": "Email address",
    "PhoneNumber": "Phone number",
    "OwnerType": "Ownership",
    "ManagementAgent": "Management agent",
    "ManagementState": "Management state",
    "DeviceEnrollmentType": "Enrollment type",
    "EnrollmentProfileName": "Enrollment profile",
    "EnrolledDateTime": "Enrolled",
    "LastSyncDateTime": "Last sync",
    "DaysSinceLastSync": "Days since last sync",
    "DeviceActivityStatus": "Activity status",
    "ComplianceState": "Compliance state",
    "AzureADRegistered": "Entra registered",
    "IsSupervised": "Supervised",
    "IsEncrypted": "Encrypted",
    "JailBroken": "Jailbroken",
    "EASActivated": "Exchange ActiveSync enabled",
    "EASActivationId": "Exchange ActiveSync activation ID",
    "EASActivationDateTime": "Exchange ActiveSync activation",
    "SubscriberCarrier": "Subscriber carrier",
    "CellularTechnology": "Cellular technology",
    "WiFiMacAddress": "Wi-Fi MAC address",
    "EthernetMacAddress": "Ethernet MAC address",
    "ICCID": "ICCID",
    "TotalStorageGB": "Total storage (GB)",
    "FreeStorageGB": "Free storage (GB)",
    "HasActivationBypassCode": "Has activation bypass code",
}

MANAGED_DEVICE_PROPERTY_LABELS: dict[str, str] = {
    "UserPrincipalName": "User principal name",
    "UserDisplayName": "User display name",
    "UserId": "User ID",
    "DeviceName": "Device name",
    "ManagedDeviceId": "Managed device ID",
    "AzureADDeviceId": "Entra device ID",
    "SerialNumber": "Serial number",
    "Manufacturer": "Manufacturer",
    "Model": "Model",
    "OperatingSystem": "Operating system",
    "OSVersion": "OS version",
    "ManagementAgent": "Management agent",
    "EnrolledDateTime": "Enrolled",
    "LastSyncDateTime": "Last sync",
    "ComplianceState": "Compliance state",
    "JailBroken": "Jailbroken",
    "OwnerType": "Ownership",
    "DaysSinceLastSync": "Days since last sync",
    "DeviceActivityStatus": "Activity status",
    "EmailAddress": "Email address",
    "PhoneNumber": "Phone number",
}

SHARED_MAILBOX_PROPERTY_LABELS: dict[str, str] = {
    "DisplayName": "Display name",
    "PrimarySmtpAddress": "Primary SMTP address",
    "Alias": "Alias",
    "ExternalDirectoryObjectId": "Entra object ID",
    "RecipientTypeDetails": "Recipient type",
    "HiddenFromAddressListsEnabled": "Hidden from address lists",
    "WhenCreated": "Created",
    "HasFullAccessDelegates": "Has Full Access delegates",
    "FullAccessDelegates": "Full Access delegates",
    "FullAccessDelegatesCount": "Full Access delegate count",
    "HasSendAsDelegates": "Has Send As delegates",
    "SendAsDelegates": "Send As delegates",
    "SendAsDelegatesCount": "Send As delegate count",
    "HasSendOnBehalfDelegates": "Has Send on behalf delegates",
    "SendOnBehalfDelegates": "Send on behalf delegates",
    "SendOnBehalfDelegatesCount": "Send on behalf delegate count",
    "HasAnyDelegation": "Has any delegation",
    "ForwardingAddress": "Forwarding address",
    "ForwardingSmtpAddress": "Forwarding SMTP address",
    "DeliverToMailboxAndForward": "Deliver to mailbox and forward",
    "HasForwarding": "Has forwarding",
    "LitigationHoldEnabled": "Litigation hold enabled",
    "RetentionPolicy": "Retention policy",
}

AUTOPILOT_DEVICE_PROPERTY_LABELS: dict[str, str] = {
    "DisplayName": "Display name",
    "SerialNumber": "Serial number",
    "Manufacturer": "Manufacturer",
    "Model": "Model",
    "GroupTag": "Group tag",
    "PurchaseOrderIdentifier": "Purchase order identifier",
    "EnrollmentState": "Enrollment state",
    "LastContactedDateTime": "Last contacted",
    "UserPrincipalName": "User principal name",
    "AddressableUserName": "Addressable user name",
    "ResourceName": "Resource name",
    "SkuNumber": "SKU number",
    "SystemFamily": "System family",
    "AzureADDeviceId": "Entra device ID",
    "ManagedDeviceId": "Managed device ID",
    "AutopilotObjectId": "Autopilot object ID",
    "AssignedUser": "Assigned user",
    "AssignmentStatus": "Assignment status",
    "RecommendedAction": "Recommended action",
}

ROLE_ASSIGNMENT_PROPERTY_LABELS: dict[str, str] = {
    "UserPrincipalName": "User principal name",
    "DisplayName": "Display name",
    "Mail": "Mail",
    "AccountEnabled": "Account enabled",
    "RoleName": "Role",
    "RoleState": "Role state",
    "AssignmentSource": "Assignment source",
    "SourceGroup": "Source group",
    "UserId": "User ID",
    "RoleDefinitionId": "Role definition ID",
    "AssignmentScheduleId": "Assignment schedule ID",
    "SourcePrincipalId": "Source principal ID",
    "SourceGroupId": "Source group ID",
    "DirectoryScopeId": "Directory scope",
    "AppScopeId": "App scope",
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
    if is_ios_devices_family(family):
        return IOS_DEVICE_PROPERTY_LABELS
    if is_managed_devices_family(family):
        return MANAGED_DEVICE_PROPERTY_LABELS
    if is_shared_mailboxes_family(family):
        return SHARED_MAILBOX_PROPERTY_LABELS
    if is_autopilot_devices_family(family):
        return AUTOPILOT_DEVICE_PROPERTY_LABELS
    if is_role_assignments_family(family):
        return ROLE_ASSIGNMENT_PROPERTY_LABELS
    return {}


def _is_device_identity_family(family: str | None) -> bool:
    return (
        is_android_devices_family(family)
        or is_ios_devices_family(family)
        or is_managed_devices_family(family)
        or is_autopilot_devices_family(family)
    )


def _is_semantic_detail_family(family: str | None) -> bool:
    return family in USER_ORIENTED_FAMILIES or _is_device_identity_family(
        family
    ) or is_shared_mailboxes_family(family) or is_role_assignments_family(family)


def property_display_text(column: str, family: str | None, *, change: str = "") -> str:
    if is_shared_mailboxes_family(family):
        if not column and change in {"Added", "Removed"}:
            return "Shared mailbox"
        return SHARED_MAILBOX_PROPERTY_LABELS.get(column, column)
    if is_role_assignments_family(family):
        if not column and change in {"Added", "Removed"}:
            return "Role assignment"
        return ROLE_ASSIGNMENT_PROPERTY_LABELS.get(column, column)
    if _is_device_identity_family(family):
        if not column and change in {"Added", "Removed"}:
            return "Device"
        labels = _family_property_labels(family)
        return labels.get(column, column)
    if family not in USER_ORIENTED_FAMILIES:
        return column
    if not column and change in {"Added", "Removed"}:
        return "User"
    return _family_property_labels(family).get(column, column)


def property_tooltip(column: str, family: str | None) -> str:
    labels = _family_property_labels(family)
    if not _is_semantic_detail_family(family) or not column:
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
    if is_ios_devices_family(family):
        parts = [identity]
        if detail.get("device_name"):
            parts.append(f"DeviceName: {detail['device_name']}")
        if detail.get("serial_number"):
            parts.append(f"SerialNumber: {detail['serial_number']}")
        if detail.get("entra_device_id"):
            parts.append(f"EntraDeviceId: {detail['entra_device_id']}")
        if detail.get("intune_device_id"):
            parts.append(f"IntuneDeviceId: {detail['intune_device_id']}")
        if detail.get("udid"):
            parts.append(f"UDID: {detail['udid']}")
        upn = detail.get("UserPrincipalName")
        if upn:
            parts.append(f"UserPrincipalName: {upn}")
        return "\n".join(parts)
    if is_shared_mailboxes_family(family):
        parts = [identity]
        if detail.get("display_name"):
            parts.append(f"DisplayName: {detail['display_name']}")
        if detail.get("primary_smtp"):
            parts.append(f"PrimarySmtpAddress: {detail['primary_smtp']}")
        if detail.get("alias"):
            parts.append(f"Alias: {detail['alias']}")
        if detail.get("external_directory_object_id"):
            parts.append(
                f"ExternalDirectoryObjectId: {detail['external_directory_object_id']}"
            )
        return "\n".join(parts)
    if is_managed_devices_family(family):
        parts = [identity]
        if detail.get("device_name"):
            parts.append(f"DeviceName: {detail['device_name']}")
        if detail.get("serial_number"):
            parts.append(f"SerialNumber: {detail['serial_number']}")
        if detail.get("managed_device_id"):
            parts.append(f"ManagedDeviceId: {detail['managed_device_id']}")
        if detail.get("azure_ad_device_id"):
            parts.append(f"AzureADDeviceId: {detail['azure_ad_device_id']}")
        if detail.get("user_display_name"):
            parts.append(f"UserDisplayName: {detail['user_display_name']}")
        upn = detail.get("UserPrincipalName")
        if upn:
            parts.append(f"UserPrincipalName: {upn}")
        if detail.get("user_id"):
            parts.append(f"UserId: {detail['user_id']}")
        return "\n".join(parts)
    if is_autopilot_devices_family(family):
        parts = [identity]
        if detail.get("display_name"):
            parts.append(f"DisplayName: {detail['display_name']}")
        if detail.get("serial_number"):
            parts.append(f"SerialNumber: {detail['serial_number']}")
        if detail.get("autopilot_object_id"):
            parts.append(f"AutopilotObjectId: {detail['autopilot_object_id']}")
        if detail.get("azure_ad_device_id"):
            parts.append(f"AzureADDeviceId: {detail['azure_ad_device_id']}")
        if detail.get("managed_device_id"):
            parts.append(f"ManagedDeviceId: {detail['managed_device_id']}")
        upn = detail.get("UserPrincipalName")
        if upn:
            parts.append(f"UserPrincipalName: {upn}")
        return "\n".join(parts)
    if is_role_assignments_family(family):
        parts = [identity]
        if detail.get("display_name"):
            parts.append(f"DisplayName: {detail['display_name']}")
        if detail.get("UPN"):
            parts.append(f"UserPrincipalName: {detail['UPN']}")
        if detail.get("user_id"):
            parts.append(f"UserId: {detail['user_id']}")
        if detail.get("role_name"):
            parts.append(f"RoleName: {detail['role_name']}")
        if detail.get("role_definition_id"):
            parts.append(f"RoleDefinitionId: {detail['role_definition_id']}")
        if detail.get("role_state"):
            parts.append(f"RoleState: {detail['role_state']}")
        if detail.get("assignment_source"):
            parts.append(f"AssignmentSource: {detail['assignment_source']}")
        if detail.get("source_group"):
            parts.append(f"SourceGroup: {detail['source_group']}")
        if detail.get("assignment_schedule_id"):
            parts.append(f"AssignmentScheduleId: {detail['assignment_schedule_id']}")
        if detail.get("source_principal_id"):
            parts.append(f"SourcePrincipalId: {detail['source_principal_id']}")
        if detail.get("source_group_id"):
            parts.append(f"SourceGroupId: {detail['source_group_id']}")
        if detail.get("directory_scope_id"):
            parts.append(f"DirectoryScopeId: {detail['directory_scope_id']}")
        if detail.get("app_scope_id"):
            parts.append(f"AppScopeId: {detail['app_scope_id']}")
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
    if _is_semantic_detail_family(family):
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
        if is_role_assignments_family(family):
            if table.columnWidth(1) < ROLE_ASSIGNMENT_IDENTITY_MIN_WIDTH:
                table.setColumnWidth(1, ROLE_ASSIGNMENT_IDENTITY_MIN_WIDTH)
            if table.columnWidth(3) < ROLE_ASSIGNMENT_PATH_MIN_WIDTH:
                table.setColumnWidth(3, ROLE_ASSIGNMENT_PATH_MIN_WIDTH)
            if table.columnWidth(4) < ROLE_ASSIGNMENT_PATH_MIN_WIDTH:
                table.setColumnWidth(4, ROLE_ASSIGNMENT_PATH_MIN_WIDTH)
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
            if column in {3, 4} and is_shared_mailboxes_family(family):
                delegate_columns = {
                    "FullAccessDelegates",
                    "SendAsDelegates",
                    "SendOnBehalfDelegates",
                }
                if detail.get("column") in delegate_columns:
                    delta = delegate_collection_delta_summary(
                        detail.get("before", ""),
                        detail.get("after", ""),
                    )
                    if delta:
                        existing = item.toolTip()
                        item.setToolTip(f"{existing}\n\n{delta}" if existing else delta)
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
