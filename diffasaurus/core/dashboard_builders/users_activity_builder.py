from datetime import datetime, timezone

def looks_like_users_activity_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    required = {
        "displayname",
        "upn",
        "usertype",
        "accountenabled",
        "createddatetime",
        "lastinteractivesignindatetime",
        "lastnoninteractivesignindatetime",
        "lastsuccessfulsignindatetime",
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


def parse_dt(value: str):
    if value is None:
        return None

    value = str(value).strip()
    if not value or value.lower() in {"nan", "none"}:
        return None

    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def build_users_activity_stats(model, headers: list[str]) -> list[dict]:
    idx_created = find_header_index(headers, ["CreatedDateTime"])
    idx_last_successful = find_header_index(headers, ["LastSuccessfulSignInDateTime"])

    total_rows = model.rowCount()

    never_successful = 0

    created_30 = 0
    created_60 = 0
    created_90 = 0
    created_180 = 0
    created_over_365 = 0
    created_over_730 = 0

    success_30 = 0
    success_60 = 0
    success_90 = 0
    success_180 = 0
    success_over_365 = 0
    success_over_730 = 0

    inactive_30 = 0
    inactive_60 = 0
    inactive_90 = 0
    inactive_180 = 0

    now = datetime.now(timezone.utc)

    for r in range(total_rows):
        created_val = cell_str(model, r, idx_created) if idx_created is not None else ""
        success_val = cell_str(model, r, idx_last_successful) if idx_last_successful is not None else ""

        created_dt = parse_dt(created_val)
        if created_dt:
            created_age = (now - created_dt).days

            if created_age <= 30:
                created_30 += 1
            if created_age <= 60:
                created_60 += 1
            if created_age <= 90:
                created_90 += 1
            if created_age <= 180:
                created_180 += 1
            if created_age > 365:
                created_over_365 += 1
            if created_age > 730:
                created_over_730 += 1

        success_dt = parse_dt(success_val)
        if success_dt:
            success_age = (now - success_dt).days

            if success_age <= 30:
                success_30 += 1
            if success_age <= 60:
                success_60 += 1
            if success_age <= 90:
                success_90 += 1
            if success_age <= 180:
                success_180 += 1
            if success_age > 365:
                success_over_365 += 1
            if success_age > 730:
                success_over_730 += 1

            if success_age > 30:
                inactive_30 += 1
            if success_age > 60:
                inactive_60 += 1
            if success_age > 90:
                inactive_90 += 1
            if success_age > 180:
                inactive_180 += 1
        else:
            never_successful += 1
            inactive_30 += 1
            inactive_60 += 1
            inactive_90 += 1
            inactive_180 += 1

    return [
        {
            "title": "Users",
            "value": total_rows,
            "subtitle": "Report rows",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "Created ≤30d",
            "value": created_30,
            "subtitle": "New accounts",
            "custom_filter": {
                "column": "CreatedDateTime",
                "mode": "days_lte",
                "days": 30,
            },
            "kind": "good",
            "section": "Account Age",
        },
        {
            "title": "Created ≤60d",
            "value": created_60,
            "subtitle": "Recent accounts",
            "custom_filter": {
                "column": "CreatedDateTime",
                "mode": "days_lte",
                "days": 60,
            },
            "kind": "warning",
            "section": "Account Age",
        },
        {
            "title": "Created ≤90d",
            "value": created_90,
            "subtitle": "Last 90 days",
            "custom_filter": {
                "column": "CreatedDateTime",
                "mode": "days_lte",
                "days": 90,
            },
            "kind": "warning",
            "section": "Account Age",
        },
        {
            "title": "Created ≤180d",
            "value": created_180,
            "subtitle": "Last 180 days",
            "custom_filter": {
                "column": "CreatedDateTime",
                "mode": "days_lte",
                "days": 180,
            },
            "kind": "accent",
            "section": "Account Age",
        },
        {
            "title": "Created >365d",
            "value": created_over_365,
            "subtitle": "Older than 1 year",
            "custom_filter": {
                "column": "CreatedDateTime",
                "mode": "days_gt",
                "days": 365,
            },
            "kind": "neutral",
            "section": "Account Age",
        },
        {
            "title": "Created >730d",
            "value": created_over_730,
            "subtitle": "Older than 2 years",
            "custom_filter": {
                "column": "CreatedDateTime",
                "mode": "days_gt",
                "days": 730,
            },
            "kind": "neutral",
            "section": "Account Age",
        },

        {
            "title": "Successful ≤30d",
            "value": success_30,
            "subtitle": "Active in 30d",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_lte",
                "days": 30,
            },
            "kind": "good",
            "section": "Recent Activity",
        },
        {
            "title": "Successful ≤60d",
            "value": success_60,
            "subtitle": "Active in 60d",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_lte",
                "days": 60,
            },
            "kind": "good",
            "section": "Recent Activity",
        },
        {
            "title": "Successful ≤90d",
            "value": success_90,
            "subtitle": "Active in 90d",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_lte",
                "days": 90,
            },
            "kind": "warning",
            "section": "Recent Activity",
        },
        {
            "title": "Successful ≤180d",
            "value": success_180,
            "subtitle": "Active in 180d",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_lte",
                "days": 180,
            },
            "kind": "accent",
            "section": "Recent Activity",
        },

        {
            "title": "Inactive >30d",
            "value": inactive_30,
            "subtitle": "No recent successful sign-in",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_gt_or_blank",
                "days": 30,
            },
            "kind": "warning",
            "section": "Inactive Accounts",
        },
        {
            "title": "Inactive >60d",
            "value": inactive_60,
            "subtitle": "Inactive over 60 days",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_gt_or_blank",
                "days": 60,
            },
            "kind": "warning",
            "section": "Inactive Accounts",
        },
        {
            "title": "Inactive >90d",
            "value": inactive_90,
            "subtitle": "Inactive over 90 days",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_gt_or_blank",
                "days": 90,
            },
            "kind": "danger",
            "section": "Inactive Accounts",
        },
        {
            "title": "Inactive >180d",
            "value": inactive_180,
            "subtitle": "Inactive over 180 days",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_gt_or_blank",
                "days": 180,
            },
            "kind": "danger",
            "section": "Inactive Accounts",
        },

        {
            "title": "Never Successful",
            "value": never_successful,
            "subtitle": "No successful sign-in",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "blank",
            },
            "kind": "danger",
            "section": "Risks",
        },
        {
            "title": "Successful >365d",
            "value": success_over_365,
            "subtitle": "Stale over 1 year",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_gt",
                "days": 365,
            },
            "kind": "danger",
            "section": "Risks",
        },
        {
            "title": "Successful >730d",
            "value": success_over_730,
            "subtitle": "Stale over 2 years",
            "custom_filter": {
                "column": "LastSuccessfulSignInDateTime",
                "mode": "days_gt",
                "days": 730,
            },
            "kind": "danger",
            "section": "Risks",
        },
    ]