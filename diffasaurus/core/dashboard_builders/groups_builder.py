from PyQt6.QtCore import Qt


def looks_like_groups_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    required_any = {
        "groupid",
        "displayname",
        "friendlygrouptype",
        "membershiptype",
        "securityenabled",
        "mailenabled",
        "isunified",
        "isdynamic",
        "isteamsteam",
        "ownerscount",
        "memberscount",
        "usedinconditionalaccess",
        "assignedroles",
        "referencedinapproles",
        "referencedinaccesspackages",
    }

    return len(normalized.intersection(required_any)) >= 3


def find_header_index(headers: list[str], candidates: list[str]) -> int | None:
    normalized_headers = [(str(h).strip().lower(), i) for i, h in enumerate(headers)]
    normalized_candidates = {c.strip().lower() for c in candidates}

    for h, i in normalized_headers:
        if h in normalized_candidates:
            return i

    return None


def cell_str(model, row: int, col: int) -> str:
    idx = model.index(row, col)
    val = model.data(idx, Qt.ItemDataRole.DisplayRole)
    return "" if val is None else str(val).strip()


def is_true(value: str) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1")


def is_non_empty(value: str) -> bool:
    return bool(str(value).strip())


def int_or_none(value: str) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value).strip()))
    except Exception:
        return None


def build_groups_stats(model, headers: list[str]) -> list[dict]:
    idx_security = find_header_index(headers, ["SecurityEnabled"])
    idx_mail = find_header_index(headers, ["MailEnabled"])
    idx_unified = find_header_index(headers, ["IsUnified"])
    idx_dynamic = find_header_index(headers, ["IsDynamic"])
    idx_sync = find_header_index(headers, ["OnPremisesSyncEnabled"])
    idx_ca = find_header_index(headers, ["UsedInConditionalAccess"])
    idx_owners = find_header_index(headers, ["OwnersCount"])
    idx_members = find_header_index(headers, ["MembersCount"])

    idx_friendly_type = find_header_index(headers, ["FriendlyGroupType"])
    idx_membership_type = find_header_index(headers, ["MembershipType"])
    idx_teams = find_header_index(headers, ["IsTeamsTeam"])
    idx_roles = find_header_index(headers, ["AssignedRoles"])
    idx_app_roles = find_header_index(headers, ["ReferencedInAppRoles"])
    idx_access_packages = find_header_index(headers, ["ReferencedInAccessPackages"])

    total = model.rowCount()

    security_groups = 0
    mail_enabled = 0
    unified_groups = 0
    dynamic_groups = 0
    assigned_membership = 0
    synced_groups = 0
    teams_groups = 0

    used_in_ca = 0
    assigned_roles = 0
    referenced_in_app_roles = 0
    referenced_in_access_packages = 0

    with_owners = 0
    without_owners = 0
    empty_groups = 0
    groups_with_members = 0

    for r in range(total):
        if idx_security is not None and is_true(cell_str(model, r, idx_security)):
            security_groups += 1

        if idx_mail is not None and is_true(cell_str(model, r, idx_mail)):
            mail_enabled += 1

        if idx_unified is not None and is_true(cell_str(model, r, idx_unified)):
            unified_groups += 1

        if idx_dynamic is not None and is_true(cell_str(model, r, idx_dynamic)):
            dynamic_groups += 1

        if idx_membership_type is not None:
            if cell_str(model, r, idx_membership_type).lower() == "assigned":
                assigned_membership += 1

        if idx_sync is not None and is_true(cell_str(model, r, idx_sync)):
            synced_groups += 1

        if idx_teams is not None and is_true(cell_str(model, r, idx_teams)):
            teams_groups += 1

        if idx_ca is not None and is_true(cell_str(model, r, idx_ca)):
            used_in_ca += 1

        if idx_roles is not None and is_non_empty(cell_str(model, r, idx_roles)):
            assigned_roles += 1

        if idx_app_roles is not None and is_non_empty(cell_str(model, r, idx_app_roles)):
            referenced_in_app_roles += 1

        if idx_access_packages is not None and is_non_empty(cell_str(model, r, idx_access_packages)):
            referenced_in_access_packages += 1

        if idx_owners is not None:
            n = int_or_none(cell_str(model, r, idx_owners))
            if n is not None:
                if n > 0:
                    with_owners += 1
                else:
                    without_owners += 1

        if idx_members is not None:
            n = int_or_none(cell_str(model, r, idx_members))
            if n is not None:
                if n > 0:
                    groups_with_members += 1
                else:
                    empty_groups += 1

    stats = [
        {
            "title": "Groups Total",
            "value": total,
            "subtitle": "All groups",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "Security Groups",
            "value": security_groups,
            "subtitle": "Security enabled",
            "filter_spec": {"SecurityEnabled": ["True"]},
            "kind": "good",
            "section": "Group Types",
        },
        {
            "title": "Microsoft 365 Groups",
            "value": unified_groups,
            "subtitle": "Unified groups",
            "filter_spec": {"IsUnified": ["True"]},
            "kind": "info",
            "section": "Group Types",
        },
        {
            "title": "Teams",
            "value": teams_groups,
            "subtitle": "Teams-enabled groups",
            "filter_spec": {"IsTeamsTeam": ["True"]},
            "kind": "accent",
            "section": "Group Types",
        },
        {
            "title": "Mail-enabled",
            "value": mail_enabled,
            "subtitle": "Mail capable",
            "filter_spec": {"MailEnabled": ["True"]},
            "kind": "accent",
            "section": "Group Types",
        },
        {
            "title": "Synced",
            "value": synced_groups,
            "subtitle": "On-prem synced",
            "filter_spec": {"OnPremisesSyncEnabled": ["True"]},
            "kind": "info",
            "section": "Group Types",
        },

        {
            "title": "Dynamic Groups",
            "value": dynamic_groups,
            "subtitle": "Rule-based membership",
            "filter_spec": {"IsDynamic": ["True"]},
            "kind": "warning",
            "section": "Membership",
        },
        {
            "title": "Assigned Groups",
            "value": assigned_membership,
            "subtitle": "Manual membership",
            "filter_spec": {"MembershipType": ["Assigned"]},
            "kind": "info",
            "section": "Membership",
        },
        {
            "title": "Groups with Members",
            "value": groups_with_members,
            "subtitle": "Has members",
            "filter_spec": {"__mode__": "gt0", "column": "MembersCount"},
            "kind": "good",
            "section": "Membership",
        },
        {
            "title": "Empty Groups",
            "value": empty_groups,
            "subtitle": "0 members",
            "filter_spec": {"__mode__": "eq0", "column": "MembersCount"},
            "kind": "warning",
            "section": "Membership",
        },

        {
            "title": "With Owners",
            "value": with_owners,
            "subtitle": "Owner assigned",
            "filter_spec": {"__mode__": "gt0", "column": "OwnersCount"},
            "kind": "good",
            "section": "Governance",
        },
        {
            "title": "Without Owners",
            "value": without_owners,
            "subtitle": "No owner",
            "filter_spec": {"__mode__": "eq0", "column": "OwnersCount"},
            "kind": "danger",
            "section": "Governance",
        },

        {
            "title": "Used in CA",
            "value": used_in_ca,
            "subtitle": "Conditional Access",
            "filter_spec": {"UsedInConditionalAccess": ["True"]},
            "kind": "danger",
            "section": "Dependencies / Risk",
        },
        {
            "title": "Assigned Roles",
            "value": assigned_roles,
            "subtitle": "Privileged role assignment",
            "filter_spec": {"__mode__": "not_empty", "column": "AssignedRoles"},
            "kind": "danger",
            "section": "Dependencies / Risk",
        },
        {
            "title": "Enterprise Apps",
            "value": referenced_in_app_roles,
            "subtitle": "App role dependencies",
            "filter_spec": {"__mode__": "not_empty", "column": "ReferencedInAppRoles"},
            "kind": "warning",
            "section": "Dependencies / Risk",
        },
        {
            "title": "Access Packages",
            "value": referenced_in_access_packages,
            "subtitle": "Entitlement dependencies",
            "filter_spec": {"__mode__": "not_empty", "column": "ReferencedInAccessPackages"},
            "kind": "warning",
            "section": "Dependencies / Risk",
        },
    ]
    return stats