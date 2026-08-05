from __future__ import annotations

from dataclasses import dataclass

from diffasaurus.core.entity.types import EntityType, FamilyCoverage, ScopedRelationship

PropertyBindingKey = tuple[EntityType, str, str]


@dataclass(frozen=True)
class NormalizedFieldSpec:
    key: str
    label: str
    entity_types: frozenset[EntityType]
    source_families: frozenset[str]
    section_id: str


@dataclass(frozen=True)
class RelationshipCollectionSpec:
    collection_id: str
    title: str
    source_family: str
    entity_types: frozenset[EntityType]
    section_id: str
    complete_relationship_inventory: bool = True
    primary_property: str = ""
    secondary_property: str = ""
    detail_property: str = ""
    detail_scope_key: str = ""
    dedup_key: str = ""


CollectionCoverageStatus = str  # re-exported by pit_presentation


def _spec(
    key: str,
    label: str,
    entity_type: EntityType,
    family: str,
    section_id: str,
) -> NormalizedFieldSpec:
    return NormalizedFieldSpec(
        key=key,
        label=label,
        entity_types=frozenset({entity_type}),
        source_families=frozenset({family}),
        section_id=section_id,
    )


def _add_user_bindings(bindings: dict[PropertyBindingKey, NormalizedFieldSpec]) -> None:
    identity = "identity"
    org = "organization"
    auth = "authentication"

    def bind(family: str, column: str, key: str, label: str, section: str) -> None:
        bindings[("user", family, column)] = _spec(key, label, "user", family, section)

    props = "Entra_Users_Properties"
    activity = "Entra_Users_Activity"
    auth_methods = "Entra_Users_AuthenticationMethods"

    for family in (props, activity):
        id_col = "Id" if family == props else "UserId"
        bind(family, id_col, "user_immutable_id", "Immutable ID", identity)

    for family in (props, activity, auth_methods):
        bind(family, "UPN", "upn", "UPN", identity)
        bind(family, "DisplayName", "display_name", "Display name", identity)

    for family in (props, activity, auth_methods, "Entra_Role_Assignments"):
        if family == "Entra_Role_Assignments":
            bind(family, "UserPrincipalName", "upn", "UPN", identity)
            bind(family, "DisplayName", "display_name", "Display name", identity)
        if family in (props, activity, auth_methods):
            bind(family, "UserType", "user_type", "User type", identity)
            bind(family, "AccountEnabled", "account_enabled", "Account enabled", identity)

    bind("Entra_Group_User_Memberships", "UserPrincipalName", "upn", "UPN", identity)
    bind("Entra_AccessPackage_User_Assignments", "UserPrincipalName", "upn", "UPN", identity)

    bind(props, "GivenName", "given_name", "Given name", identity)
    bind(props, "Surname", "surname", "Surname", identity)
    bind(props, "MailNickname", "mail_nickname", "Mail nickname", identity)
    bind(props, "Identities", "identities", "Identities", identity)
    bind(props, "OnPremisesImmutableId", "on_premises_immutable_id", "On-premises immutable ID", identity)
    bind(props, "OnPremisesDistinguishedName", "on_premises_dn", "On-premises DN", identity)

    for family in (props, activity):
        bind(family, "Mail", "mail", "Mail", org)

    for family in (props, activity, auth_methods):
        bind(family, "JobTitle", "job_title", "Job title", org)
        bind(family, "CompanyName", "company_name", "Company", org)
        bind(family, "Department", "department", "Department", org)
    bind(props, "OfficeLocation", "office_location", "Office location", org)
    bind(props, "EmployeeId", "employee_id", "Employee ID", org)
    bind(props, "EmployeeType", "employee_type", "Employee type", org)
    bind(props, "EmployeeHireDate", "employee_hire_date", "Employee hire date", org)
    bind(props, "UsageLocation", "usage_location", "Usage location", org)
    bind(props, "PreferredLanguage", "preferred_language", "Preferred language", org)
    bind(props, "PreferredDataLocation", "preferred_data_location", "Preferred data location", org)
    bind(props, "ManagerDisplayName", "manager_display_name", "Manager", org)
    bind(props, "ManagerUPN", "manager_upn", "Manager UPN", org)
    bind(props, "Sponsors", "sponsors", "Sponsors", org)

    for family in (props, activity, auth_methods):
        bind(family, "Country", "country", "Country", org)
    bind(activity, "City", "city", "City", org)
    bind(auth_methods, "City", "city", "City", org)

    bind(props, "StreetAddress", "street_address", "Street address", org)
    bind(props, "City", "city", "City", org)
    bind(props, "State", "state", "State / province", org)
    bind(props, "PostalCode", "postal_code", "Postal code", org)
    bind(props, "BusinessPhones", "business_phones", "Business phones", org)
    bind(props, "MobilePhone", "mobile_phone", "Mobile phone", org)
    bind(props, "OtherMails", "other_mails", "Other mails", org)
    bind(props, "ProxyAddresses", "proxy_addresses", "Proxy addresses", org)
    bind(props, "IMAddresses", "im_addresses", "IM addresses", org)
    bind(props, "OnPremisesSyncEnabled", "on_premises_sync_enabled", "On-premises sync enabled", org)
    bind(props, "OnPremisesLastSyncDateTime", "on_premises_last_sync", "On-premises last sync", org)
    bind(props, "ExtensionAttributes", "extension_attributes", "Extension attributes", org)

    for family in (props, activity):
        bind(family, "CreatedDateTime", "created_date", "Created date", identity)

    bind(activity, "LastPasswordChangeDateTime", "last_password_change", "Last password change", auth)
    bind(activity, "OnPremisesSyncEnabled", "on_premises_sync_enabled", "On-premises sync enabled", auth)
    bind(activity, "LastInteractiveSignInDateTime", "last_interactive_sign_in", "Last interactive sign-in", auth)
    bind(activity, "LastNonInteractiveSignInDateTime", "last_non_interactive_sign_in", "Last non-interactive sign-in", auth)
    bind(activity, "LastSuccessfulSignInDateTime", "last_successful_sign_in", "Last successful sign-in", auth)

    bind(auth_methods, "IsMfaRegistered", "mfa_registered", "MFA registered", auth)
    bind(auth_methods, "IsMfaCapable", "mfa_capable", "MFA capable", auth)
    bind(auth_methods, "IsPasswordlessCapable", "passwordless_capable", "Passwordless capable", auth)
    bind(auth_methods, "DefaultMfaMethod", "default_mfa_method", "Default MFA method", auth)
    bind(auth_methods, "AuthenticationMethods", "authentication_methods", "Authentication methods", auth)
    bind(auth_methods, "MethodsRegistered", "authentication_methods", "Authentication methods", auth)
    bind(auth_methods, "IsSsprRegistered", "sspr_registered", "SSPR registered", auth)
    bind(auth_methods, "IsSsprEnabled", "sspr_enabled", "SSPR enabled", auth)
    bind(auth_methods, "IsSsprCapable", "sspr_capable", "SSPR capable", auth)
    bind(auth_methods, "IsAdmin", "is_admin", "Is admin", auth)
    bind(auth_methods, "IsSystemPreferredAuthenticationMethodEnabled", "system_preferred_auth_enabled", "System preferred auth enabled", auth)
    bind(auth_methods, "UserPreferredMethodForSecondaryAuthentication", "user_preferred_secondary_auth", "User preferred secondary auth", auth)
    bind(auth_methods, "SystemPreferredAuthenticationMethod", "system_preferred_auth_method", "System preferred auth method", auth)
    bind(auth_methods, "SystemPreferredAuthenticationMethods", "system_preferred_auth_method", "System preferred auth method", auth)
    bind(auth_methods, "LastUpdatedDateTime", "auth_report_last_updated", "Auth report last updated", auth)


SCALAR_EXCLUSIONS: frozenset[PropertyBindingKey] = frozenset(
    {
        ("user", "Entra_Users_Properties", "ManagerStatus"),
        ("user", "Entra_Users_Properties", "ManagerError"),
        ("user", "Entra_Users_Properties", "SponsorsStatus"),
        ("user", "Entra_Users_Properties", "SponsorsError"),
        ("user", "Entra_Users_AuthenticationMethods", "PrefsStatus"),
        ("user", "Entra_Users_AuthenticationMethods", "PrefsError"),
        ("user", "Entra_Users_AuthenticationMethods", "MethodsStatus"),
        ("user", "Entra_Users_AuthenticationMethods", "MethodsError"),
        ("user", "Entra_Users_AuthenticationMethods", "MicrosoftReportId"),
        ("user", "Entra_Users_AuthenticationMethods", "ReportSource"),
        ("user", "Entra_Group_User_Memberships", "UserId"),
        ("user", "Entra_AccessPackage_User_Assignments", "UserId"),
    }
)


def is_scalar_excluded(entity_type: EntityType, family: str, property_name: str) -> bool:
    return (entity_type, family, property_name) in SCALAR_EXCLUSIONS


def _build_property_bindings() -> dict[PropertyBindingKey, NormalizedFieldSpec]:
    bindings: dict[PropertyBindingKey, NormalizedFieldSpec] = {}

    def add(entity_type: EntityType, family: str, column: str, spec: NormalizedFieldSpec) -> None:
        bindings[(entity_type, family, column)] = spec

    _add_user_bindings(bindings)

    # --- device ---
    device_identity = "identity"
    device_os = "os_compliance"
    device_mgmt = "management"

    add("device", "Intune_ManagedDevices_Compliance", "AzureADDeviceId", _spec("device_entra_id", "Entra device ID", "device", "Intune_ManagedDevices_Compliance", device_identity))
    add("device", "Intune_Devices_Autopilot", "AzureADDeviceId", _spec("device_entra_id", "Entra device ID", "device", "Intune_Devices_Autopilot", device_identity))
    add("device", "Intune_iOS_Devices", "EntraDeviceId", _spec("device_entra_id", "Entra device ID", "device", "Intune_iOS_Devices", device_identity))

    add("device", "Intune_ManagedDevices_Compliance", "ManagedDeviceId", _spec("device_managed_id", "Managed device ID", "device", "Intune_ManagedDevices_Compliance", device_identity))
    add("device", "Intune_iOS_Devices", "IntuneDeviceId", _spec("intune_device_id", "Intune device ID", "device", "Intune_iOS_Devices", device_mgmt))

    for family, col in (
        ("Intune_ManagedDevices_Compliance", "DeviceName"),
        ("Intune_iOS_Devices", "DeviceName"),
        ("Intune_Devices_Autopilot", "DisplayName"),
    ):
        add("device", family, col, _spec("device_name", "Device name", "device", family, device_identity))

    for family in ("Intune_ManagedDevices_Compliance", "Intune_Devices_Autopilot", "Intune_iOS_Devices"):
        add("device", family, "SerialNumber", _spec("serial_number", "Serial number", "device", family, "ownership"))

    add("device", "Intune_Devices_Autopilot", "AutopilotObjectId", _spec("autopilot_id", "Autopilot ID", "device", "Intune_Devices_Autopilot", device_mgmt))
    add("device", "Intune_Devices_Autopilot", "Manufacturer", _spec("manufacturer", "Manufacturer", "device", "Intune_Devices_Autopilot", device_mgmt))
    add("device", "Intune_Devices_Autopilot", "Model", _spec("model", "Model", "device", "Intune_Devices_Autopilot", device_mgmt))
    add("device", "Intune_Devices_Autopilot", "EnrollmentState", _spec("enrollment_state", "Enrollment state", "device", "Intune_Devices_Autopilot", device_mgmt))

    for family in ("Intune_ManagedDevices_Compliance", "Intune_iOS_Devices"):
        add("device", family, "ComplianceState", _spec("compliance_state", "Compliance state", "device", family, device_os))
        add("device", family, "OperatingSystem", _spec("operating_system", "Operating system", "device", family, device_os))

    # --- shared mailbox ---
    mb_identity = "identity"
    mb_addresses = "addresses"
    mb_delegation = "delegation"
    mb_settings = "settings"

    add("shared_mailbox", "Exchange_SharedMailboxes", "ExternalDirectoryObjectId", _spec("mailbox_immutable_id", "Immutable ID", "shared_mailbox", "Exchange_SharedMailboxes", mb_identity))
    add("shared_mailbox", "Exchange_SharedMailboxes", "DisplayName", _spec("display_name", "Display name", "shared_mailbox", "Exchange_SharedMailboxes", mb_identity))
    add("shared_mailbox", "Exchange_SharedMailboxes", "PrimarySmtpAddress", _spec("primary_smtp", "Primary SMTP", "shared_mailbox", "Exchange_SharedMailboxes", mb_addresses))
    add("shared_mailbox", "Exchange_SharedMailboxes", "Alias", _spec("alias", "Alias", "shared_mailbox", "Exchange_SharedMailboxes", mb_addresses))
    add("shared_mailbox", "Exchange_SharedMailboxes", "HasForwarding", _spec("has_forwarding", "Has forwarding", "shared_mailbox", "Exchange_SharedMailboxes", mb_delegation))
    add("shared_mailbox", "Exchange_SharedMailboxes", "ForwardingSmtpAddress", _spec("forwarding_smtp", "Forwarding SMTP", "shared_mailbox", "Exchange_SharedMailboxes", mb_delegation))
    add("shared_mailbox", "Exchange_SharedMailboxes", "HasFullAccessDelegates", _spec("has_full_access_delegates", "Has full access delegates", "shared_mailbox", "Exchange_SharedMailboxes", mb_delegation))
    add("shared_mailbox", "Exchange_SharedMailboxes", "FullAccessDelegates", _spec("full_access_delegates", "Full access delegates", "shared_mailbox", "Exchange_SharedMailboxes", mb_delegation))
    add("shared_mailbox", "Exchange_SharedMailboxes", "LitigationHoldEnabled", _spec("litigation_hold_enabled", "Litigation hold", "shared_mailbox", "Exchange_SharedMailboxes", mb_settings))

    return bindings


PROPERTY_BINDINGS: dict[PropertyBindingKey, NormalizedFieldSpec] = _build_property_bindings()

AUTHORITY_ORDER: dict[tuple[EntityType, str], tuple[str, ...]] = {
    ("user", "upn"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
        "Entra_Role_Assignments",
        "Entra_Group_User_Memberships",
        "Entra_AccessPackage_User_Assignments",
    ),
    ("user", "display_name"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
        "Entra_Role_Assignments",
    ),
    ("user", "user_immutable_id"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
    ),
    ("user", "department"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
    ),
    ("user", "mail"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
    ),
    ("user", "account_enabled"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "created_date"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
    ),
    ("user", "company_name"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "country"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "city"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "job_title"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "user_type"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "authentication_methods"): (
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "system_preferred_auth_method"): (
        "Entra_Users_AuthenticationMethods",
    ),
    ("user", "on_premises_sync_enabled"): (
        "Entra_Users_Properties",
        "Entra_Users_Activity",
    ),
    ("device", "device_entra_id"): (
        "Intune_ManagedDevices_Compliance",
        "Intune_Devices_Autopilot",
        "Intune_iOS_Devices",
    ),
    ("device", "device_name"): (
        "Intune_ManagedDevices_Compliance",
        "Intune_iOS_Devices",
        "Intune_Devices_Autopilot",
    ),
    ("device", "serial_number"): (
        "Intune_ManagedDevices_Compliance",
        "Intune_Devices_Autopilot",
        "Intune_iOS_Devices",
    ),
    ("device", "compliance_state"): (
        "Intune_ManagedDevices_Compliance",
        "Intune_iOS_Devices",
    ),
    ("device", "operating_system"): (
        "Intune_ManagedDevices_Compliance",
        "Intune_iOS_Devices",
    ),
    ("shared_mailbox", "display_name"): ("Exchange_SharedMailboxes",),
    ("shared_mailbox", "primary_smtp"): ("Exchange_SharedMailboxes",),
    ("shared_mailbox", "mailbox_immutable_id"): ("Exchange_SharedMailboxes",),
}

RELATIONSHIP_COLLECTIONS: tuple[RelationshipCollectionSpec, ...] = (
    RelationshipCollectionSpec(
        collection_id="roles",
        title="Roles",
        source_family="Entra_Role_Assignments",
        entity_types=frozenset({"user"}),
        section_id="roles",
        primary_property="RoleName",
        secondary_property="RoleState",
        dedup_key="RoleName",
    ),
    RelationshipCollectionSpec(
        collection_id="groups",
        title="Groups",
        source_family="Entra_Group_User_Memberships",
        entity_types=frozenset({"user"}),
        section_id="groups",
        primary_property="GroupName",
        secondary_property="MembershipType",
        detail_property="GroupId",
        detail_scope_key="GroupId",
        dedup_key="GroupId",
    ),
    RelationshipCollectionSpec(
        collection_id="access_packages",
        title="Access packages",
        source_family="Entra_AccessPackage_User_Assignments",
        entity_types=frozenset({"user"}),
        section_id="access_packages",
        primary_property="AccessPackageName",
        secondary_property="AssignmentState",
        detail_property="AccessPackageId",
        detail_scope_key="AccessPackageId",
        dedup_key="AccessPackageId",
    ),
)

SECTION_ORDER: dict[EntityType, tuple[str, ...]] = {
    "user": (
        "identity",
        "organization",
        "authentication",
        "additional_details",
        "roles",
        "groups",
        "access_packages",
    ),
    "device": ("identity", "ownership", "os_compliance", "management", "activity"),
    "shared_mailbox": ("identity", "addresses", "delegation", "settings", "activity"),
}

SECTION_TITLES: dict[str, str] = {
    "identity": "Identity",
    "organization": "Organization",
    "authentication": "Authentication and activity",
    "roles": "Roles",
    "groups": "Groups",
    "access_packages": "Access packages",
    "ownership": "Ownership",
    "os_compliance": "OS & compliance",
    "management": "Management",
    "activity": "Activity",
    "addresses": "Addresses",
    "delegation": "Delegation",
    "settings": "Settings",
    "additional_details": "Additional details",
}


def lookup_property_binding(
    entity_type: EntityType,
    family: str,
    property_name: str,
) -> NormalizedFieldSpec | None:
    return PROPERTY_BINDINGS.get((entity_type, family, property_name))


def parse_row_scope(row_scope: str) -> dict[str, str]:
    if not row_scope:
        return {}
    result: dict[str, str] = {}
    for segment in row_scope.split(" / "):
        if ": " not in segment:
            continue
        column, value = segment.split(": ", 1)
        column = column.strip()
        value = value.strip()
        if column:
            result[column] = value
    return result


def resolve_collection_coverage(
    spec: RelationshipCollectionSpec,
    entity_type: EntityType,
    coverage_by_family: dict[str, FamilyCoverage],
    relationships: tuple[ScopedRelationship, ...],
) -> str:
    if entity_type not in spec.entity_types:
        return "not_applicable"
    if len(relationships) > 0:
        return "populated"
    if spec.source_family not in coverage_by_family:
        return "unknown"
    coverage = coverage_by_family[spec.source_family]
    if coverage.status == "no_snapshot":
        return "no_coverage"
    if coverage.snapshot_at is None:
        return "no_coverage"
    if spec.complete_relationship_inventory and coverage.snapshot_at is not None and (
        coverage.status == "entity_absent"
        or (coverage.status == "snapshot_used" and not relationships)
    ):
        return "known_empty"
    return "unknown"
