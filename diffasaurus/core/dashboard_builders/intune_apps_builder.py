def looks_like_intune_apps_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}
    required = {
        "appname",
        "platformguess",
        "appgraphtype",
        "publishingstate",
        "isassigned",
        "assignmentcount",
    }
    return required.issubset(normalized)


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


def build_intune_apps_stats(model, headers: list[str]) -> list[dict]:
    idx_platform = find_header_index(headers, ["PlatformGuess"])
    idx_type = find_header_index(headers, ["AppGraphType"])
    idx_state = find_header_index(headers, ["PublishingState"])
    idx_featured = find_header_index(headers, ["IsFeatured"])
    idx_assigned = find_header_index(headers, ["IsAssigned"])
    idx_assignment_count = find_header_index(headers, ["AssignmentCount"])
    idx_intents = find_header_index(headers, ["AssignmentIntents"])
    idx_targets = find_header_index(headers, ["AssignmentTargets"])
    idx_required = find_header_index(headers, ["RequiredGroups"])
    idx_available = find_header_index(headers, ["AvailableGroups"])
    idx_uninstall = find_header_index(headers, ["UninstallGroups"])
    idx_excluded = find_header_index(headers, ["ExcludedGroups"])

    total_rows = model.rowCount()

    ios = 0
    android = 0
    windows = 0
    macos = 0
    other = 0

    assigned = 0
    unassigned = 0
    published = 0
    processing = 0
    featured = 0

    required_apps = 0
    available_apps = 0
    uninstall_apps = 0
    excluded_apps = 0

    group_targeted = 0
    all_devices = 0
    all_users = 0

    total_assignment_rows = 0

    app_types = set()

    for r in range(total_rows):
        platform = cell_str(model, r, idx_platform).lower() if idx_platform is not None else ""
        app_type = cell_str(model, r, idx_type) if idx_type is not None else ""
        state = cell_str(model, r, idx_state).lower() if idx_state is not None else ""
        is_featured = cell_str(model, r, idx_featured).lower() if idx_featured is not None else ""
        is_assigned = cell_str(model, r, idx_assigned).lower() if idx_assigned is not None else ""
        intents = cell_str(model, r, idx_intents).lower() if idx_intents is not None else ""
        targets = cell_str(model, r, idx_targets).lower() if idx_targets is not None else ""
        required_groups = cell_str(model, r, idx_required) if idx_required is not None else ""
        available_groups = cell_str(model, r, idx_available) if idx_available is not None else ""
        uninstall_groups = cell_str(model, r, idx_uninstall) if idx_uninstall is not None else ""
        excluded_groups = cell_str(model, r, idx_excluded) if idx_excluded is not None else ""

        if app_type:
            app_types.add(app_type)

        if platform == "ios/ipados":
            ios += 1
        elif platform == "android":
            android += 1
        elif platform == "windows":
            windows += 1
        elif platform == "macos":
            macos += 1
        else:
            other += 1

        if state == "published":
            published += 1
        elif state == "processing":
            processing += 1

        if is_featured == "true":
            featured += 1

        if is_assigned == "true":
            assigned += 1
        elif is_assigned == "false":
            unassigned += 1

        if required_groups:
            required_apps += 1
        if available_groups:
            available_apps += 1
        if uninstall_groups:
            uninstall_apps += 1
        if excluded_groups:
            excluded_apps += 1

        if "included group" in targets or "excluded group" in targets:
            group_targeted += 1
        if "all devices" in targets:
            all_devices += 1
        if "all licensed users" in targets:
            all_users += 1

        if idx_assignment_count is not None:
            raw = cell_str(model, r, idx_assignment_count)
            try:
                total_assignment_rows += int(raw)
            except Exception:
                pass

    return [
        {
            "title": "Apps",
            "value": total_rows,
            "subtitle": "Total apps",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "App Types",
            "value": len(app_types),
            "subtitle": "Distinct graph types",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "AppGraphType",
            },
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Assignment Rows",
            "value": total_assignment_rows,
            "subtitle": "Total assignments",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "Published",
            "value": published,
            "subtitle": "Ready apps",
            "filter_spec": {"PublishingState": ["published"]},
            "kind": "good",
            "section": "Publishing State",
        },
        {
            "title": "Processing",
            "value": processing,
            "subtitle": "Still processing",
            "filter_spec": {"PublishingState": ["processing"]},
            "kind": "warning",
            "section": "Publishing State",
        },

        {
            "title": "Assigned",
            "value": assigned,
            "subtitle": "Apps with assignments",
            "filter_spec": {"IsAssigned": ["True"]},
            "kind": "good",
            "section": "Assignment Status",
        },
        {
            "title": "Unassigned",
            "value": unassigned,
            "subtitle": "Apps without assignments",
            "filter_spec": {"IsAssigned": ["False"]},
            "kind": "warning",
            "section": "Assignment Status",
        },

        {
            "title": "Required",
            "value": required_apps,
            "subtitle": "Required deployments",
            "filter_spec": {
                "__mode__": "nonblank",
                "column": "RequiredGroups",
            },
            "kind": "danger",
            "section": "Deployment Intent",
        },
        {
            "title": "Available",
            "value": available_apps,
            "subtitle": "Available deployments",
            "filter_spec": {
                "__mode__": "nonblank",
                "column": "AvailableGroups",
            },
            "kind": "info",
            "section": "Deployment Intent",
        },
        {
            "title": "Uninstall",
            "value": uninstall_apps,
            "subtitle": "Uninstall deployments",
            "filter_spec": {
                "__mode__": "nonblank",
                "column": "UninstallGroups",
            },
            "kind": "warning",
            "section": "Deployment Intent",
        },
        {
            "title": "Excluded",
            "value": excluded_apps,
            "subtitle": "Exclusion groups",
            "filter_spec": {
                "__mode__": "nonblank",
                "column": "ExcludedGroups",
            },
            "kind": "danger",
            "section": "Deployment Intent",
        },

        {
            "title": "Group Targeted",
            "value": group_targeted,
            "subtitle": "Assigned to groups",
            "filter_spec": {
                "__mode__": "contains_any",
                "column": "AssignmentTargets",
                "values": ["Included group", "Excluded group"],
            },
            "kind": "accent",
            "section": "Targeting Scope",
        },
        {
            "title": "All Devices",
            "value": all_devices,
            "subtitle": "Tenant-wide devices",
            "filter_spec": {
                "__mode__": "contains",
                "column": "AssignmentTargets",
                "value": "All devices",
            },
            "kind": "warning",
            "section": "Targeting Scope",
        },
        {
            "title": "All Licensed Users",
            "value": all_users,
            "subtitle": "Tenant-wide users",
            "filter_spec": {
                "__mode__": "contains",
                "column": "AssignmentTargets",
                "value": "All licensed users",
            },
            "kind": "warning",
            "section": "Targeting Scope",
        },

        {
            "title": "Windows",
            "value": windows,
            "subtitle": "Windows apps",
            "filter_spec": {"PlatformGuess": ["Windows"]},
            "kind": "info",
            "section": "Platforms",
        },
        {
            "title": "iOS/iPadOS",
            "value": ios,
            "subtitle": "Apple mobile apps",
            "filter_spec": {"PlatformGuess": ["iOS/iPadOS"]},
            "kind": "accent",
            "section": "Platforms",
        },
        {
            "title": "Android",
            "value": android,
            "subtitle": "Android apps",
            "filter_spec": {"PlatformGuess": ["Android"]},
            "kind": "good",
            "section": "Platforms",
        },
        {
            "title": "macOS",
            "value": macos,
            "subtitle": "Mac apps",
            "filter_spec": {"PlatformGuess": ["macOS"]},
            "kind": "accent",
            "section": "Platforms",
        },
        {
            "title": "Other",
            "value": other,
            "subtitle": "Other platforms",
            "filter_spec": {"PlatformGuess": ["Other / Unknown"]},
            "kind": "neutral",
            "section": "Platforms",
        },
    ]