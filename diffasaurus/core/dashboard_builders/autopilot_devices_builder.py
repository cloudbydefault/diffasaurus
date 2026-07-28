from PyQt6.QtCore import Qt


def looks_like_autopilot_devices_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    has_autopilot_id = (
        "autopilotobjectid" in normalized
        or "azureaddeviceid" in normalized
        or "manageddeviceid" in normalized
    )

    has_autopilot_core = (
        "serialnumber" in normalized
        and "manufacturer" in normalized
        and "model" in normalized
        and "grouptag" in normalized
    )

    has_custom_columns = (
        "assignmentstatus" in normalized
        or "recommendedaction" in normalized
        or "isuserassigned" in normalized
    )

    return has_autopilot_core and has_autopilot_id and has_custom_columns


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


def build_autopilot_devices_stats(model, headers: list[str]) -> list[dict]:
    idx_assignment = find_header_index(headers, ["AssignmentStatus"])
    idx_recommendation = find_header_index(headers, ["RecommendedAction"])
    idx_enrollment = find_header_index(headers, ["EnrollmentState"])
    idx_manufacturer = find_header_index(headers, ["Manufacturer"])

    total = model.rowCount()

    assigned = 0
    not_assigned = 0
    ready_to_assign = 0
    ready_to_unassign = 0
    review = 0
    enrolled = 0

    manufacturer_counts = {}

    for r in range(total):
        if idx_assignment is not None:
            v = cell_str(model, r, idx_assignment)
            if v == "Assigned":
                assigned += 1
            elif v == "NotAssigned":
                not_assigned += 1

        if idx_recommendation is not None:
            v = cell_str(model, r, idx_recommendation)
            if v == "ReadyToAssign":
                ready_to_assign += 1
            elif v == "ReadyToUnassign":
                ready_to_unassign += 1
            elif v == "Review":
                review += 1

        if idx_enrollment is not None:
            v = cell_str(model, r, idx_enrollment).lower()
            if v == "enrolled":
                enrolled += 1

        if idx_manufacturer is not None:
            m = cell_str(model, r, idx_manufacturer) or "Unknown"
            manufacturer_counts[m] = manufacturer_counts.get(m, 0) + 1

    cards = [
        {
            "title": "Autopilot Total",
            "value": total,
            "subtitle": "All Autopilot devices",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Enrolled",
            "value": enrolled,
            "subtitle": "Enrollment state = enrolled",
            "filter_spec": {"EnrollmentState": ["enrolled"]},
            "kind": "accent",
            "section": "Overview",
        },

        {
            "title": "Assigned",
            "value": assigned,
            "subtitle": "User assigned",
            "filter_spec": {"AssignmentStatus": ["Assigned"]},
            "kind": "good",
            "section": "Assignment Status",
        },
        {
            "title": "Not Assigned",
            "value": not_assigned,
            "subtitle": "No user assigned",
            "filter_spec": {"AssignmentStatus": ["NotAssigned"]},
            "kind": "warning",
            "section": "Assignment Status",
        },

        {
            "title": "Ready To Assign",
            "value": ready_to_assign,
            "subtitle": "Available for assignment",
            "filter_spec": {"RecommendedAction": ["ReadyToAssign"]},
            "kind": "good",
            "section": "Recommended Actions",
        },
        {
            "title": "Ready To Unassign",
            "value": ready_to_unassign,
            "subtitle": "Can likely be unassigned",
            "filter_spec": {"RecommendedAction": ["ReadyToUnassign"]},
            "kind": "warning",
            "section": "Recommended Actions",
        },
        {
            "title": "Review Required",
            "value": review,
            "subtitle": "Assigned and enrolled",
            "filter_spec": {"RecommendedAction": ["Review"]},
            "kind": "danger",
            "section": "Recommended Actions",
        },
    ]

    for manufacturer, count in sorted(
            manufacturer_counts.items(),
            key=lambda x: x[0].lower()
    )[:6]:
        cards.append({
            "title": manufacturer,
            "value": count,
            "subtitle": "Manufacturer",
            "filter_spec": {"Manufacturer": [manufacturer]},
            "kind": "info",
            "section": "Inventory",
        })
    return cards