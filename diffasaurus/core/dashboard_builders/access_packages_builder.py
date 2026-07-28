def looks_like_access_packages_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    required_any = {
        "accesspackagename",
        "accesspackageid",
        "policyname",
        "policyid",
        "policystatus",
        "catalogid",
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


def build_access_packages_stats(model, headers: list[str]) -> list[dict]:
    idx_pkg_name = find_header_index(headers, ["AccessPackageName"])
    idx_pkg_id = find_header_index(headers, ["AccessPackageId"])
    idx_policy_name = find_header_index(headers, ["PolicyName"])
    idx_policy_id = find_header_index(headers, ["PolicyId"])
    idx_policy_status = find_header_index(headers, ["PolicyStatus"])
    idx_catalog_id = find_header_index(headers, ["CatalogId"])

    total_rows = model.rowCount()

    package_names = set()
    package_ids = set()
    policy_names = set()
    policy_ids = set()
    catalog_ids = set()

    enabled_policies = 0
    disabled_policies = 0
    blank_policy_rows = 0

    packages_with_enabled = set()
    packages_with_disabled = set()
    packages_without_policy = set()

    for r in range(total_rows):
        pkg_name = cell_str(model, r, idx_pkg_name) if idx_pkg_name is not None else ""
        pkg_id = cell_str(model, r, idx_pkg_id) if idx_pkg_id is not None else ""
        policy_name = cell_str(model, r, idx_policy_name) if idx_policy_name is not None else ""
        policy_id = cell_str(model, r, idx_policy_id) if idx_policy_id is not None else ""
        policy_status = cell_str(model, r, idx_policy_status) if idx_policy_status is not None else ""
        catalog_id = cell_str(model, r, idx_catalog_id) if idx_catalog_id is not None else ""

        if pkg_name:
            package_names.add(pkg_name)
        if pkg_id:
            package_ids.add(pkg_id)
        if policy_name:
            policy_names.add(policy_name)
        if policy_id:
            policy_ids.add(policy_id)
        if catalog_id:
            catalog_ids.add(catalog_id)

        if not policy_name and not policy_id:
            blank_policy_rows += 1
            if pkg_name:
                packages_without_policy.add(pkg_name)
            continue

        status_norm = policy_status.strip().lower()
        if status_norm == "enabled":
            enabled_policies += 1
            if pkg_name:
                packages_with_enabled.add(pkg_name)
        elif status_norm == "disabled":
            disabled_policies += 1
            if pkg_name:
                packages_with_disabled.add(pkg_name)

    total_packages = max(len(package_names), len(package_ids))
    total_policies = max(len(policy_names), len(policy_ids))

    return [
        {
            "title": "Access Packages",
            "value": total_packages,
            "subtitle": "Distinct packages",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Policies",
            "value": total_policies,
            "subtitle": "Distinct policies",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Catalogs",
            "value": len(catalog_ids),
            "subtitle": "Distinct catalogs",
            "filter_spec": {},
            "kind": "accent",
            "section": "Overview",
        },
        {
            "title": "Report Rows",
            "value": total_rows,
            "subtitle": "CSV rows loaded",
            "filter_spec": {},
            "kind": "neutral",
            "section": "Overview",
        },

        {
            "title": "Enabled Policies",
            "value": enabled_policies,
            "subtitle": "Policies enabled",
            "filter_spec": {"PolicyStatus": ["Enabled"]},
            "kind": "good",
            "section": "Healthy State",
        },
        {
            "title": "Packages w/ Enabled",
            "value": len(packages_with_enabled),
            "subtitle": "At least one enabled policy",
            "filter_spec": {"PolicyStatus": ["Enabled"]},
            "kind": "good",
            "section": "Healthy State",
        },

        {
            "title": "Disabled Policies",
            "value": disabled_policies,
            "subtitle": "Policies disabled",
            "filter_spec": {"PolicyStatus": ["Disabled"]},
            "kind": "danger",
            "section": "Risks",
        },
        {
            "title": "Packages w/ Disabled",
            "value": len(packages_with_disabled),
            "subtitle": "At least one disabled policy",
            "filter_spec": {"PolicyStatus": ["Disabled"]},
            "kind": "warning",
            "section": "Risks",
        },
        {
            "title": "No Policy Rows",
            "value": blank_policy_rows,
            "subtitle": "Rows without policy",
            "filter_spec": {},
            "kind": "danger",
            "section": "Risks",
        },
        {
            "title": "Packages w/o Policy",
            "value": len(packages_without_policy),
            "subtitle": "No policy detected",
            "filter_spec": {},
            "kind": "danger",
            "section": "Risks",
        },
    ]