from diffasaurus.core.dashboard_builders.identity_builder import (
    looks_like_identity_report,
    build_identity_stats,
)
from diffasaurus.core.dashboard_builders.groups_builder import (
    looks_like_groups_report,
    build_groups_stats,
)
from diffasaurus.core.dashboard_builders.autopilot_devices_builder import (
    looks_like_autopilot_devices_report,
    build_autopilot_devices_stats,
)
from diffasaurus.core.dashboard_builders.intune_ios_devices_builder import (
    looks_like_intune_ios_devices_report,
    build_intune_ios_devices_stats,
)
from diffasaurus.core.dashboard_builders.devices_builder import (
    looks_like_devices_report,
    build_devices_stats,
)
from diffasaurus.core.dashboard_builders.access_packages_builder import (
    looks_like_access_packages_report,
    build_access_packages_stats,
)
from diffasaurus.core.dashboard_builders.role_assignments_builder import (
    looks_like_role_assignments_report,
    build_role_assignments_stats,
)

from diffasaurus.core.dashboard_builders.authentication_methods_builder import (
    looks_like_authentication_methods_report,
    build_authentication_methods_stats,
)

from diffasaurus.core.dashboard_builders.users_activity_builder import (
    looks_like_users_activity_report,
    build_users_activity_stats,
)

from diffasaurus.core.dashboard_builders.intune_apps_builder import (
    looks_like_intune_apps_report,
    build_intune_apps_stats,
)

from diffasaurus.core.dashboard_builders.exchange_shared_mailboxes_builder import (
    looks_like_exchange_shared_mailboxes_report,
    build_exchange_shared_mailboxes_stats,
)
from diffasaurus.core.dashboard_builders.generic_builder import build_generic_stats
from diffasaurus.core.dashboard_builders.memberships_builder import (
    build_memberships_stats,
    looks_like_memberships_report,
)

DASHBOARD_BUILDERS = [
    {
        "title": "Group Memberships Dashboard",
        "detector": looks_like_memberships_report,
        "builder": build_memberships_stats,
    },
    {
        "title": "Role Assignments Dashboard",
        "detector": looks_like_role_assignments_report,
        "builder": build_role_assignments_stats,
    },
    {
        "title": "Authentication Methods Dashboard",
        "detector": looks_like_authentication_methods_report,
        "builder": build_authentication_methods_stats,
    },
    {
        "title": "Users Activity Dashboard",
        "detector": looks_like_users_activity_report,
        "builder": build_users_activity_stats,
    },
    {
        "title": "Identity Dashboard",
        "detector": looks_like_identity_report,
        "builder": build_identity_stats,
    },
    {
        "title": "Groups Dashboard",
        "detector": looks_like_groups_report,
        "builder": build_groups_stats,
    },
    {
        "title": "Autopilot Devices Dashboard",
        "detector": looks_like_autopilot_devices_report,
        "builder": build_autopilot_devices_stats,
    },
    {
        "title": "Intune iOS Devices",
        "detector": looks_like_intune_ios_devices_report,
        "builder": build_intune_ios_devices_stats,
    },
    {
        "title": "Devices Dashboard",
        "detector": looks_like_devices_report,
        "builder": build_devices_stats,
    },
    {
        "title": "Access Packages Dashboard",
        "detector": looks_like_access_packages_report,
        "builder": build_access_packages_stats,
    },
    {
        "title": "Intune Apps Dashboard",
        "detector": looks_like_intune_apps_report,
        "builder": build_intune_apps_stats,
    },
    {
        "title": "Exchange Shared Mailboxes Dashboard",
        "detector": looks_like_exchange_shared_mailboxes_report,
        "builder": build_exchange_shared_mailboxes_stats,
    },
]

def get_dashboard_definition(model, headers):
    for item in DASHBOARD_BUILDERS:
        result = item["detector"](headers)

        if result:
            return item["title"], item["builder"](model, headers)

    return "Snapshot Dashboard", build_generic_stats(model, headers)
