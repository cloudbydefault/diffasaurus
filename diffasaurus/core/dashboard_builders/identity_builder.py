from PyQt6.QtCore import Qt

def looks_like_identity_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}
    required_any = {
        "userprincipalname",
        "accountenabled",
        "usertype",
        "onpremisessyncenabled",
    }
    return len(normalized.intersection(required_any)) >= 2

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


def build_identity_stats(model, headers: list[str]) -> list[dict]:
    idx_enabled = find_header_index(headers, ["AccountEnabled"])
    idx_usertype = find_header_index(headers, ["UserType"])
    idx_sync = find_header_index(headers, ["OnPremisesSyncEnabled"])
    idx_manager = find_header_index(headers, ["ManagerUPN", "ManagerDisplayName"])
    idx_department = find_header_index(headers, ["Department"])
    idx_jobtitle = find_header_index(headers, ["JobTitle"])
    idx_usage_location = find_header_index(headers, ["UsageLocation"])
    idx_company = find_header_index(headers, ["CompanyName"])
    idx_sponsors = find_header_index(headers, ["Sponsors"])

    total = model.rowCount()
    enabled = 0
    disabled = 0
    guests = 0
    members = 0
    cloud_only = 0
    synced = 0
    with_manager = 0
    without_manager = 0
    with_department = 0
    without_department = 0
    with_jobtitle = 0
    without_jobtitle = 0
    with_usage_location = 0
    without_usage_location = 0
    with_company = 0
    without_company = 0
    with_sponsors = 0
    without_sponsors = 0

    for r in range(total):
        if idx_enabled is not None:
            v = cell_str(model, r, idx_enabled).lower()
            if v == "true":
                enabled += 1
            elif v == "false":
                disabled += 1

        if idx_usertype is not None:
            v = cell_str(model, r, idx_usertype).lower()
            if v == "guest":
                guests += 1
            elif v == "member":
                members += 1

        if idx_sync is not None:
            v = cell_str(model, r, idx_sync).lower()
            if v == "true":
                synced += 1
            elif v in ("false", ""):
                cloud_only += 1

        if idx_manager is not None:
            v = cell_str(model, r, idx_manager)
            if v:
                with_manager += 1
            else:
                without_manager += 1

        if idx_department is not None:
            v = cell_str(model, r, idx_department)
            if v:
                with_department += 1
            else:
                without_department += 1

        if idx_jobtitle is not None:
            v = cell_str(model, r, idx_jobtitle)
            if v:
                with_jobtitle += 1
            else:
                without_jobtitle += 1

        if idx_usage_location is not None:
            v = cell_str(model, r, idx_usage_location)
            if v:
                with_usage_location += 1
            else:
                without_usage_location += 1

        if idx_company is not None:
            v = cell_str(model, r, idx_company)
            if v:
                with_company += 1
            else:
                without_company += 1

        if idx_sponsors is not None:
            v = cell_str(model, r, idx_sponsors)
            if v:
                with_sponsors += 1
            else:
                without_sponsors += 1

    return [
        {
            "title": "Identity Total",
            "value": total,
            "subtitle": "Total users",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "Enabled",
            "value": enabled,
            "subtitle": "Active accounts",
            "filter_spec": {"AccountEnabled": ["True"]},
            "kind": "good",
            "section": "Account State",
        },
        {
            "title": "Disabled",
            "value": disabled,
            "subtitle": "Inactive accounts",
            "filter_spec": {"AccountEnabled": ["False"]},
            "kind": "danger",
            "section": "Account State",
        },

        {
            "title": "Members",
            "value": members,
            "subtitle": "Internal users",
            "filter_spec": {"UserType": ["Member"]},
            "kind": "good",
            "section": "User Type",
        },
        {
            "title": "Guests",
            "value": guests,
            "subtitle": "External users",
            "filter_spec": {"UserType": ["Guest"]},
            "kind": "warning",
            "section": "User Type",
        },

        {
            "title": "Synced",
            "value": synced,
            "subtitle": "Hybrid AD",
            "filter_spec": {"OnPremisesSyncEnabled": ["True"]},
            "kind": "info",
            "section": "Directory Source",
        },
        {
            "title": "Cloud-only",
            "value": cloud_only,
            "subtitle": "Not synced",
            "filter_spec": {"OnPremisesSyncEnabled": ["False", ""]},
            "kind": "warning",
            "section": "Directory Source",
        },

        {
            "title": "With Manager",
            "value": with_manager,
            "subtitle": "Manager assigned",
            "filter_spec": {"__mode__": "nonblank", "column": "ManagerUPN"},
            "kind": "good",
            "section": "Profile Completeness",
        },
        {
            "title": "Without Manager",
            "value": without_manager,
            "subtitle": "No manager",
            "filter_spec": {"__mode__": "blank", "column": "ManagerUPN"},
            "kind": "danger",
            "section": "Profile Completeness",
        },
        {
            "title": "With Department",
            "value": with_department,
            "subtitle": "Department set",
            "filter_spec": {"__mode__": "nonblank", "column": "Department"},
            "kind": "good",
            "section": "Profile Completeness",
        },
        {
            "title": "Without Department",
            "value": without_department,
            "subtitle": "Department missing",
            "filter_spec": {"__mode__": "blank", "column": "Department"},
            "kind": "warning",
            "section": "Profile Completeness",
        },
        {
            "title": "With Job Title",
            "value": with_jobtitle,
            "subtitle": "Job title set",
            "filter_spec": {"__mode__": "nonblank", "column": "JobTitle"},
            "kind": "good",
            "section": "Profile Completeness",
        },
        {
            "title": "Without Job Title",
            "value": without_jobtitle,
            "subtitle": "Job title missing",
            "filter_spec": {"__mode__": "blank", "column": "JobTitle"},
            "kind": "warning",
            "section": "Profile Completeness",
        },
        {
            "title": "With Usage Location",
            "value": with_usage_location,
            "subtitle": "Usage location set",
            "filter_spec": {"__mode__": "nonblank", "column": "UsageLocation"},
            "kind": "good",
            "section": "Profile Completeness",
        },
        {
            "title": "Without Usage Location",
            "value": without_usage_location,
            "subtitle": "Usage location missing",
            "filter_spec": {"__mode__": "blank", "column": "UsageLocation"},
            "kind": "warning",
            "section": "Profile Completeness",
        },
        {
            "title": "With Company",
            "value": with_company,
            "subtitle": "Company set",
            "filter_spec": {"__mode__": "nonblank", "column": "CompanyName"},
            "kind": "accent",
            "section": "Profile Completeness",
        },
        {
            "title": "Without Company",
            "value": without_company,
            "subtitle": "Company missing",
            "filter_spec": {"__mode__": "blank", "column": "CompanyName"},
            "kind": "warning",
            "section": "Profile Completeness",
        },

        {
            "title": "With Sponsors",
            "value": with_sponsors,
            "subtitle": "Sponsors set",
            "filter_spec": {"__mode__": "nonblank", "column": "Sponsors"},
            "kind": "accent",
            "section": "Guest Governance",
        },
        {
            "title": "Without Sponsors",
            "value": without_sponsors,
            "subtitle": "Sponsors missing",
            "filter_spec": {"__mode__": "blank", "column": "Sponsors"},
            "kind": "warning",
            "section": "Guest Governance",
        },
    ]