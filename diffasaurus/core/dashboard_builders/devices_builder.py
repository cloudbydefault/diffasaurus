from PyQt6.QtCore import Qt

def looks_like_devices_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}
    required_any = {
        "devicename",
        "operatingsystem",
        "compliancestate",
        "ownertype",
        "dayssincelastsync",
        "deviceactivitystatus",
        "managementagent",
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


def build_devices_stats(model, headers: list[str]) -> list[dict]:
    idx_os = find_header_index(headers, ["OperatingSystem"])
    idx_compliance = find_header_index(headers, ["ComplianceState"])
    idx_owner = find_header_index(headers, ["OwnerType"])
    idx_days = find_header_index(headers, ["DaysSinceLastSync"])
    idx_activity = find_header_index(headers, ["DeviceActivityStatus"])
    idx_agent = find_header_index(headers, ["ManagementAgent"])

    total = model.rowCount()
    windows = 0
    macos = 0
    ios = 0
    android = 0
    compliant = 0
    noncompliant = 0
    company_owned = 0
    personal = 0
    active_30 = 0
    stale_31_90 = 0
    stale_90_plus = 0
    mdm_managed = 0

    for r in range(total):
        if idx_os is not None:
            v = cell_str(model, r, idx_os).lower()
            if v == "windows":
                windows += 1
            elif v == "macos":
                macos += 1
            elif v in ("ios", "ipados"):
                ios += 1
            elif v == "android":
                android += 1

        if idx_compliance is not None:
            v = cell_str(model, r, idx_compliance).lower()
            if v == "compliant":
                compliant += 1
            elif v in ("noncompliant", "non-compliant"):
                noncompliant += 1

        if idx_owner is not None:
            v = cell_str(model, r, idx_owner).lower()
            if v == "company":
                company_owned += 1
            elif v == "personal":
                personal += 1

        if idx_activity is not None:
            v = cell_str(model, r, idx_activity)
            if v == "Active<=30d":
                active_30 += 1
            elif v == "Stale31-90d":
                stale_31_90 += 1
            elif v == "Stale>90d":
                stale_90_plus += 1

        elif idx_days is not None:
            raw = cell_str(model, r, idx_days)
            try:
                d = int(raw)
                if d <= 30:
                    active_30 += 1
                elif d <= 90:
                    stale_31_90 += 1
                else:
                    stale_90_plus += 1
            except Exception:
                pass

        if idx_agent is not None:
            v = cell_str(model, r, idx_agent).lower()
            if v == "mdm":
                mdm_managed += 1

    return [
        {
            "title": "Devices Total",
            "value": total,
            "subtitle": "All devices",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "MDM-managed",
            "value": mdm_managed,
            "subtitle": "Managed by MDM",
            "filter_spec": {"ManagementAgent": ["mdm"]},
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "Compliant",
            "value": compliant,
            "subtitle": "Healthy devices",
            "filter_spec": {"ComplianceState": ["Compliant"]},
            "kind": "good",
            "section": "Compliance",
        },
        {
            "title": "NonCompliant",
            "value": noncompliant,
            "subtitle": "Needs attention",
            "filter_spec": {"ComplianceState": ["NonCompliant", "Non-Compliant"]},
            "kind": "danger",
            "section": "Compliance",
        },

        {
            "title": "Active <= 30d",
            "value": active_30,
            "subtitle": "Recently synced",
            "filter_spec": {"DeviceActivityStatus": ["Active<=30d"]},
            "kind": "good",
            "section": "Activity",
        },
        {
            "title": "Stale 31–90d",
            "value": stale_31_90,
            "subtitle": "Needs review",
            "filter_spec": {"DeviceActivityStatus": ["Stale31-90d"]},
            "kind": "warning",
            "section": "Activity",
        },
        {
            "title": "Stale > 90d",
            "value": stale_90_plus,
            "subtitle": "Very old activity",
            "filter_spec": {"DeviceActivityStatus": ["Stale>90d"]},
            "kind": "danger",
            "section": "Activity",
        },

        {
            "title": "Company-owned",
            "value": company_owned,
            "subtitle": "Corporate devices",
            "filter_spec": {"OwnerType": ["company"]},
            "kind": "good",
            "section": "Ownership",
        },
        {
            "title": "Personal",
            "value": personal,
            "subtitle": "BYOD devices",
            "filter_spec": {"OwnerType": ["personal"]},
            "kind": "warning",
            "section": "Ownership",
        },

        {
            "title": "Windows",
            "value": windows,
            "subtitle": "Windows devices",
            "filter_spec": {"OperatingSystem": ["Windows"]},
            "kind": "info",
            "section": "Operating Systems",
        },
        {
            "title": "macOS",
            "value": macos,
            "subtitle": "Mac devices",
            "filter_spec": {"OperatingSystem": ["macOS"]},
            "kind": "accent",
            "section": "Operating Systems",
        },
        {
            "title": "iOS / iPadOS",
            "value": ios,
            "subtitle": "Apple mobile",
            "filter_spec": {"OperatingSystem": ["iOS", "iPadOS"]},
            "kind": "accent",
            "section": "Operating Systems",
        },
        {
            "title": "Android",
            "value": android,
            "subtitle": "Android mobile",
            "filter_spec": {"OperatingSystem": ["Android"]},
            "kind": "warning",
            "section": "Operating Systems",
        },
    ]