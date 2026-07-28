def looks_like_role_assignments_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}
    required_any = {
        "userprincipalname",
        "displayname",
        "rolename",
        "rolestate",
        "assignmentsource",
        "sourcegroup",
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
    val = model.data(idx)
    return "" if val is None else str(val).strip()


def build_role_assignments_stats(model, headers: list[str]) -> list[dict]:
    idx_upn = find_header_index(headers, ["UserPrincipalName"])
    idx_role = find_header_index(headers, ["RoleName"])
    idx_state = find_header_index(headers, ["RoleState"])
    idx_source = find_header_index(headers, ["AssignmentSource"])
    idx_group = find_header_index(headers, ["SourceGroup"])
    idx_enabled = find_header_index(headers, ["AccountEnabled"])

    total_rows = model.rowCount()

    users = set()
    roles = set()
    groups = set()

    active = 0
    eligible = 0
    direct = 0
    group_based = 0
    enabled_users = 0
    disabled_users = 0

    direct_active = 0
    group_active = 0
    direct_eligible = 0
    group_eligible = 0

    for r in range(total_rows):
        upn = cell_str(model, r, idx_upn) if idx_upn is not None else ""
        role = cell_str(model, r, idx_role) if idx_role is not None else ""
        state = cell_str(model, r, idx_state).lower() if idx_state is not None else ""
        source = cell_str(model, r, idx_source).lower() if idx_source is not None else ""
        group = cell_str(model, r, idx_group) if idx_group is not None else ""
        enabled = cell_str(model, r, idx_enabled).lower() if idx_enabled is not None else ""

        if upn:
            users.add(upn)
        if role:
            roles.add(role)
        if group:
            groups.add(group)

        if state == "active":
            active += 1
        elif state == "eligible":
            eligible += 1

        if source == "direct":
            direct += 1
        elif source == "group":
            group_based += 1

        if enabled == "true":
            enabled_users += 1
        elif enabled == "false":
            disabled_users += 1

        if source == "direct" and state == "active":
            direct_active += 1
        elif source == "group" and state == "active":
            group_active += 1
        elif source == "direct" and state == "eligible":
            direct_eligible += 1
        elif source == "group" and state == "eligible":
            group_eligible += 1

    return [
        {
            "title": "Role Rows",
            "value": total_rows,
            "subtitle": "Total rows",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Distinct Users",
            "value": len(users),
            "subtitle": "Users with roles",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "UserPrincipalName",
            },
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Distinct Roles",
            "value": len(roles),
            "subtitle": "Unique admin roles",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "RoleName",
            },
            "kind": "accent",
            "section": "Overview",
        },
        {
            "title": "Source Groups",
            "value": len(groups),
            "subtitle": "Groups granting roles",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "SourceGroup",
            },
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "Active",
            "value": active,
            "subtitle": "Active assignments",
            "filter_spec": {"RoleState": ["Active"]},
            "kind": "danger",
            "section": "Assignment State",
        },
        {
            "title": "Eligible",
            "value": eligible,
            "subtitle": "Eligible assignments",
            "filter_spec": {"RoleState": ["Eligible"]},
            "kind": "warning",
            "section": "Assignment State",
        },

        {
            "title": "Direct",
            "value": direct,
            "subtitle": "Direct assignments",
            "filter_spec": {"AssignmentSource": ["Direct"]},
            "kind": "danger",
            "section": "Assignment Source",
        },
        {
            "title": "Group",
            "value": group_based,
            "subtitle": "Group-based assignments",
            "filter_spec": {"AssignmentSource": ["Group"]},
            "kind": "accent",
            "section": "Assignment Source",
        },

        {
            "title": "Direct + Active",
            "value": direct_active,
            "subtitle": "Direct active assignments",
            "filter_spec": {
                "AssignmentSource": ["Direct"],
                "RoleState": ["Active"],
            },
            "kind": "danger",
            "section": "Risk Breakdown",
        },
        {
            "title": "Group + Active",
            "value": group_active,
            "subtitle": "Group-based active assignments",
            "filter_spec": {
                "AssignmentSource": ["Group"],
                "RoleState": ["Active"],
            },
            "kind": "accent",
            "section": "Risk Breakdown",
        },
        {
            "title": "Direct + Eligible",
            "value": direct_eligible,
            "subtitle": "Direct eligible assignments",
            "filter_spec": {
                "AssignmentSource": ["Direct"],
                "RoleState": ["Eligible"],
            },
            "kind": "warning",
            "section": "Risk Breakdown",
        },
        {
            "title": "Group + Eligible",
            "value": group_eligible,
            "subtitle": "Group-based eligible assignments",
            "filter_spec": {
                "AssignmentSource": ["Group"],
                "RoleState": ["Eligible"],
            },
            "kind": "info",
            "section": "Risk Breakdown",
        },

        {
            "title": "Enabled Users",
            "value": enabled_users,
            "subtitle": "Enabled rows",
            "filter_spec": {"AccountEnabled": ["True"]},
            "kind": "good",
            "section": "Account State",
        },
        {
            "title": "Disabled Users",
            "value": disabled_users,
            "subtitle": "Disabled rows",
            "filter_spec": {"AccountEnabled": ["False"]},
            "kind": "danger",
            "section": "Account State",
        },
    ]