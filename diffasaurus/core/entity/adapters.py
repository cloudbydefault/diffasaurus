from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from diffasaurus.core.entity.types import CanonicalEntityKey, EntityType


def _row_value(row: dict[str, str], column: str) -> str:
    if not column:
        return ""
    normalized = {header.lower(): header for header in row}
    actual = normalized.get(column.lower())
    if not actual:
        return ""
    return str(row.get(actual, "") or "").strip()


@dataclass(frozen=True)
class AliasColumn:
    kind: str
    column: str


@dataclass(frozen=True)
class ReportFamilyAdapter:
    family: str
    entity_type: EntityType
    canonical_columns: tuple[str, ...]
    alias_columns: tuple[AliasColumn, ...]
    display_name_column: str
    row_scope_columns: tuple[str, ...] = ()
    card_columns: tuple[str, ...] = ()
    fallback_key: Callable[[dict[str, str], str], str | None] | None = None
    authoritative_inventory: bool = False

    def canonical_value(self, row: dict[str, str]) -> str:
        for column in self.canonical_columns:
            value = _row_value(row, column)
            if value:
                return value
        return ""

    def build_key(self, row: dict[str, str]) -> CanonicalEntityKey | None:
        canonical = self.canonical_value(row)
        if canonical:
            if self.entity_type == "user":
                return CanonicalEntityKey("user", canonical)
            if self.entity_type == "device":
                return CanonicalEntityKey("device", f"aad:{canonical}")
            return CanonicalEntityKey("shared_mailbox", canonical)

        if self.fallback_key:
            fallback = self.fallback_key(row, self.family)
            if fallback:
                return CanonicalEntityKey(self.entity_type, fallback)

        return None

    def display_name(self, row: dict[str, str]) -> str:
        value = _row_value(row, self.display_name_column)
        if value:
            return value
        canonical = self.canonical_value(row)
        return canonical or "Unknown"

    def row_scope(self, row: dict[str, str]) -> str:
        if not self.row_scope_columns:
            return ""
        parts = []
        for column in self.row_scope_columns:
            value = _row_value(row, column)
            if value:
                parts.append(f"{column}: {value}")
        return " / ".join(parts)

    def card_properties(self, row: dict[str, str], observed_at) -> list[tuple[str, str]]:
        columns = self.card_columns or tuple(row.keys())
        properties: list[tuple[str, str]] = []
        for column in columns:
            if column in self.canonical_columns:
                continue
            value = _row_value(row, column)
            if value:
                properties.append((column, value))
        return properties

    def headers_supported(self, headers: tuple[str, ...] | list[str]) -> bool:
        headers_lower = {header.lower() for header in headers}
        if self.canonical_columns:
            return any(column.lower() in headers_lower for column in self.canonical_columns)
        if self.alias_columns:
            return any(alias.column.lower() in headers_lower for alias in self.alias_columns)
        return self.fallback_key is not None

    def is_upn_dependent(self) -> bool:
        if self.canonical_columns:
            return False
        return any(alias.kind == "upn" for alias in self.alias_columns)


def _autopilot_fallback(row: dict[str, str], family: str) -> str | None:
    value = _row_value(row, "AutopilotObjectId")
    if value:
        return f"autopilot:{value}"
    return None


def _managed_device_fallback(row: dict[str, str], family: str) -> str | None:
    value = _row_value(row, "ManagedDeviceId")
    if value:
        return f"intune:{family}:{value}"
    return None


def _ios_device_fallback(row: dict[str, str], family: str) -> str | None:
    value = _row_value(row, "IntuneDeviceId")
    if value:
        return f"ios_intune:{value}"
    return None


def _mailbox_smtp_fallback(row: dict[str, str], family: str) -> str | None:
    value = _row_value(row, "PrimarySmtpAddress")
    if value:
        return f"smtp:{value.lower()}"
    return None


USER_PROPERTIES = ReportFamilyAdapter(
    family="Entra_Users_Properties",
    entity_type="user",
    authoritative_inventory=True,
    canonical_columns=("Id",),
    alias_columns=(
        AliasColumn("upn", "UPN"),
        AliasColumn("mail", "Mail"),
    ),
    display_name_column="DisplayName",
    card_columns=(
        "Id",
        "UPN",
        "DisplayName",
        "Mail",
        "Department",
        "JobTitle",
        "AccountEnabled",
        "UserType",
    ),
)

USER_ACTIVITY = ReportFamilyAdapter(
    family="Entra_Users_Activity",
    entity_type="user",
    canonical_columns=("UserId",),
    alias_columns=(AliasColumn("upn", "UPN"), AliasColumn("mail", "Mail")),
    display_name_column="DisplayName",
    card_columns=("UserId", "UPN", "DisplayName", "Mail", "AccountEnabled", "Department"),
)

USER_AUTH_METHODS = ReportFamilyAdapter(
    family="Entra_Users_AuthenticationMethods",
    entity_type="user",
    canonical_columns=(),
    alias_columns=(AliasColumn("upn", "UPN"),),
    display_name_column="DisplayName",
    card_columns=("UPN", "DisplayName", "IsMfaRegistered", "DefaultMfaMethod"),
    fallback_key=lambda row, family: (
        f"upn_only:{family}:{_row_value(row, 'UPN').lower()}"
        if _row_value(row, "UPN")
        else None
    ),
)

USER_ROLE_ASSIGNMENTS = ReportFamilyAdapter(
    family="Entra_Role_Assignments",
    entity_type="user",
    canonical_columns=(),
    alias_columns=(AliasColumn("upn", "UserPrincipalName"),),
    display_name_column="DisplayName",
    row_scope_columns=("RoleName",),
    card_columns=("UserPrincipalName", "DisplayName", "RoleName", "RoleState"),
    fallback_key=lambda row, family: (
        f"upn_only:{family}:{_row_value(row, 'UserPrincipalName').lower()}"
        if _row_value(row, "UserPrincipalName")
        else None
    ),
)

USER_MEMBERSHIPS = ReportFamilyAdapter(
    family="Entra_Group_User_Memberships",
    entity_type="user",
    canonical_columns=("UserId",),
    alias_columns=(AliasColumn("upn", "UserPrincipalName"),),
    display_name_column="UserDisplayName",
    row_scope_columns=("GroupId", "GroupName"),
    card_columns=("UserId", "UserPrincipalName", "GroupName", "MembershipType"),
)

USER_ACCESS_ASSIGNMENTS = ReportFamilyAdapter(
    family="Entra_AccessPackage_User_Assignments",
    entity_type="user",
    canonical_columns=("UserId",),
    alias_columns=(AliasColumn("upn", "UserPrincipalName"),),
    display_name_column="UserDisplayName",
    row_scope_columns=("AccessPackageId", "AccessPackageName"),
    card_columns=("UserId", "UserPrincipalName", "AccessPackageName", "AssignmentState"),
)

DEVICE_MANAGED = ReportFamilyAdapter(
    family="Intune_ManagedDevices_Compliance",
    entity_type="device",
    authoritative_inventory=True,
    canonical_columns=("AzureADDeviceId",),
    alias_columns=(
        AliasColumn("serial_number", "SerialNumber"),
        AliasColumn("device_name", "DeviceName"),
        AliasColumn("managed_device_id", "ManagedDeviceId"),
    ),
    display_name_column="DeviceName",
    card_columns=(
        "AzureADDeviceId",
        "ManagedDeviceId",
        "DeviceName",
        "SerialNumber",
        "ComplianceState",
        "OperatingSystem",
    ),
    fallback_key=_managed_device_fallback,
)

DEVICE_AUTOPILOT = ReportFamilyAdapter(
    family="Intune_Devices_Autopilot",
    entity_type="device",
    canonical_columns=("AzureADDeviceId",),
    alias_columns=(
        AliasColumn("serial_number", "SerialNumber"),
        AliasColumn("device_name", "DisplayName"),
        AliasColumn("autopilot_id", "AutopilotObjectId"),
    ),
    display_name_column="DisplayName",
    card_columns=(
        "AzureADDeviceId",
        "AutopilotObjectId",
        "SerialNumber",
        "Manufacturer",
        "Model",
        "EnrollmentState",
    ),
    fallback_key=_autopilot_fallback,
)

DEVICE_IOS = ReportFamilyAdapter(
    family="Intune_iOS_Devices",
    entity_type="device",
    canonical_columns=("EntraDeviceId",),
    alias_columns=(
        AliasColumn("serial_number", "SerialNumber"),
        AliasColumn("device_name", "DeviceName"),
        AliasColumn("intune_device_id", "IntuneDeviceId"),
    ),
    display_name_column="DeviceName",
    card_columns=(
        "EntraDeviceId",
        "IntuneDeviceId",
        "DeviceName",
        "SerialNumber",
        "ComplianceState",
        "OperatingSystem",
    ),
    fallback_key=_ios_device_fallback,
)

MAILBOX_SHARED = ReportFamilyAdapter(
    family="Exchange_SharedMailboxes",
    entity_type="shared_mailbox",
    authoritative_inventory=True,
    canonical_columns=("ExternalDirectoryObjectId",),
    alias_columns=(
        AliasColumn("primary_smtp", "PrimarySmtpAddress"),
        AliasColumn("alias", "Alias"),
    ),
    display_name_column="DisplayName",
    card_columns=(
        "ExternalDirectoryObjectId",
        "PrimarySmtpAddress",
        "DisplayName",
        "Alias",
        "HasForwarding",
        "ForwardingSmtpAddress",
        "HasFullAccessDelegates",
        "FullAccessDelegates",
        "LitigationHoldEnabled",
    ),
    fallback_key=_mailbox_smtp_fallback,
)

ALL_ADAPTERS: tuple[ReportFamilyAdapter, ...] = (
    USER_PROPERTIES,
    USER_ACTIVITY,
    USER_AUTH_METHODS,
    USER_ROLE_ASSIGNMENTS,
    USER_MEMBERSHIPS,
    USER_ACCESS_ASSIGNMENTS,
    DEVICE_MANAGED,
    DEVICE_AUTOPILOT,
    DEVICE_IOS,
    MAILBOX_SHARED,
)
