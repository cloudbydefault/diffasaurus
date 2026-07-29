from __future__ import annotations


def looks_like_memberships_report(headers: list[str]) -> bool:
    normalized = {str(header).strip().casefold() for header in headers}
    return {
        "userprincipalname",
        "groupid",
        "groupname",
        "membershiptype",
    }.issubset(normalized)


def build_memberships_stats(model, headers: list[str]) -> list[dict]:
    indexes = {
        str(header).strip().casefold(): index
        for index, header in enumerate(headers)
    }

    def values(name: str) -> list[str]:
        column = indexes.get(name.casefold())
        return model.column_values(column) if column is not None else []

    users = values("UserPrincipalName")
    groups = values("GroupId")
    user_types = [value.casefold() for value in values("UserType")]
    account_states = [value.casefold() for value in values("AccountEnabled")]
    membership_types = [value.casefold() for value in values("MembershipType")]
    group_types = [value.casefold() for value in values("GroupType")]
    total = model.rowCount()
    unique_users = len({value for value in users if value})
    unique_groups = len({value for value in groups if value})

    def card(
        title,
        value,
        subtitle,
        section,
        kind="neutral",
        filter_spec=None,
    ):
        return {
            "title": title,
            "value": value,
            "subtitle": subtitle,
            "section": section,
            "kind": kind,
            "filter_spec": filter_spec or {},
        }

    return [
        card("Memberships", total, "User-to-group links", "Overview", "info"),
        card(
            "Unique users",
            unique_users,
            "Distinct user principals",
            "Overview",
            "accent",
            {"__mode__": "distinct_nonblank", "column": "UserPrincipalName"},
        ),
        card(
            "Unique groups",
            unique_groups,
            "Distinct group IDs",
            "Overview",
            "accent",
            {"__mode__": "distinct_nonblank", "column": "GroupId"},
        ),
        card(
            "Members",
            user_types.count("member"),
            "Internal memberships",
            "User type",
            "good",
            {"UserType": ["Member"]},
        ),
        card(
            "Guests",
            user_types.count("guest"),
            "Guest memberships",
            "User type",
            "warning",
            {"UserType": ["Guest"]},
        ),
        card(
            "Enabled users",
            account_states.count("true"),
            "Active account links",
            "Account state",
            "good",
            {"AccountEnabled": ["True"]},
        ),
        card(
            "Disabled users",
            account_states.count("false"),
            "Disabled account links",
            "Account state",
            "danger",
            {"AccountEnabled": ["False"]},
        ),
        card(
            "Dynamic",
            membership_types.count("dynamic"),
            "Rule-based memberships",
            "Membership type",
            "info",
            {"MembershipType": ["Dynamic"]},
        ),
        card(
            "Assigned",
            sum(value in {"assigned", "static", "direct"} for value in membership_types),
            "Direct memberships",
            "Membership type",
            "good",
            {"MembershipType": ["Assigned", "Static", "Direct"]},
        ),
        card(
            "Security groups",
            group_types.count("security"),
            "Security group links",
            "Group type",
            "info",
            {"GroupType": ["Security"]},
        ),
        card(
            "Microsoft 365",
            sum("microsoft" in value or value == "unified" for value in group_types),
            "Collaboration group links",
            "Group type",
            "accent",
            {"GroupType": ["Microsoft365", "Microsoft 365", "Unified"]},
        ),
    ]
