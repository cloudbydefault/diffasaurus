def looks_like_intune_ios_devices_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    ios_specific = {
        "udid",
        "imei",
        "iccid",
        "issupervised",
        "activationlockbypasscode",
        "hasactivationbypasscode",
    }

    required = {
        "devicename",
        "operatingsystem",
        "compliancestate",
    }

    android_markers = {
        "androidsecuritypatchlevel",
        "rooted",
        "partnerreportedthreatstate",
        "deviceregistrationstate",
    }

    if len(normalized.intersection(android_markers)) >= 1:
        return False

    return (
        required.issubset(normalized)
        and len(normalized.intersection(ios_specific)) >= 2
    )

def find_header_index(headers: list[str], candidates: list[str]) -> int | None:
    normalized_headers = [(str(h).strip().lower(), i) for i, h in enumerate(headers)]
    normalized_candidates = {c.strip().lower() for c in candidates}

    for h, i in normalized_headers:
        if h in normalized_candidates:
            return i

    return None


def cell_str(model, row: int, col: int | None) -> str:
    if col is None:
        return ""

    idx = model.index(row, col)
    val = model.data(idx)

    return "" if val is None else str(val).strip()


def build_intune_ios_devices_stats(model, headers: list[str]) -> list[dict]:
    idx_os = find_header_index(headers, ["OperatingSystem"])
    idx_compliance = find_header_index(headers, ["ComplianceState"])
    idx_owner = find_header_index(headers, ["OwnerType"])
    idx_supervised = find_header_index(headers, ["IsSupervised"])
    idx_encrypted = find_header_index(headers, ["IsEncrypted"])
    idx_jailbroken = find_header_index(headers, ["JailBroken"])
    idx_activation = find_header_index(headers, ["HasActivationBypassCode"])
    idx_activity = find_header_index(headers, ["DeviceActivityStatus"])
    idx_model = find_header_index(headers, ["Model"])
    idx_user = find_header_index(headers, ["UserPrincipalName"])

    total_rows = model.rowCount()

    ios = 0
    ipados = 0
    compliant = 0
    non_compliant = 0
    supervised = 0
    not_supervised = 0
    encrypted = 0
    not_encrypted = 0
    jailbroken = 0
    activation_code = 0
    corporate = 0
    personal = 0
    inactive_90 = 0
    never_synced = 0

    models = set()
    users = set()

    for r in range(total_rows):
        os_value = cell_str(model, r, idx_os).lower()
        compliance = cell_str(model, r, idx_compliance).lower()
        owner = cell_str(model, r, idx_owner).lower()
        supervised_value = cell_str(model, r, idx_supervised).lower()
        encrypted_value = cell_str(model, r, idx_encrypted).lower()
        jailbroken_value = cell_str(model, r, idx_jailbroken).lower()
        activation_value = cell_str(model, r, idx_activation).lower()
        activity = cell_str(model, r, idx_activity).lower()
        model_value = cell_str(model, r, idx_model)
        user_value = cell_str(model, r, idx_user)

        if os_value == "ios":
            ios += 1
        elif os_value == "ipados":
            ipados += 1

        if compliance == "compliant":
            compliant += 1
        elif compliance and compliance != "compliant":
            non_compliant += 1

        if supervised_value == "true":
            supervised += 1
        elif supervised_value == "false":
            not_supervised += 1

        if encrypted_value == "true":
            encrypted += 1
        elif encrypted_value == "false":
            not_encrypted += 1

        if jailbroken_value == "true":
            jailbroken += 1

        if activation_value == "yes":
            activation_code += 1

        if owner == "company":
            corporate += 1
        elif owner == "personal":
            personal += 1

        if activity == "inactive >90d":
            inactive_90 += 1
        elif activity == "never synced":
            never_synced += 1

        if model_value:
            models.add(model_value)

        if user_value:
            users.add(user_value)

    return [
        {
            "title": "iOS Devices",
            "value": total_rows,
            "subtitle": "Total iPhone / iPad rows",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Users",
            "value": len(users),
            "subtitle": "Distinct assigned users",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "UserPrincipalName",
            },
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "iPhone",
            "value": ios,
            "subtitle": "OperatingSystem = iOS",
            "filter_spec": {"OperatingSystem": ["iOS"]},
            "kind": "info",
            "section": "Platform",
        },
        {
            "title": "iPad",
            "value": ipados,
            "subtitle": "OperatingSystem = iPadOS",
            "filter_spec": {"OperatingSystem": ["iPadOS"]},
            "kind": "accent",
            "section": "Platform",
        },

        {
            "title": "Compliant",
            "value": compliant,
            "subtitle": "Compliant devices",
            "filter_spec": {"ComplianceState": ["compliant"]},
            "kind": "good",
            "section": "Compliance",
        },
        {
            "title": "Non-compliant",
            "value": non_compliant,
            "subtitle": "Review compliance state",
            "filter_spec": {
                "ComplianceState": [
                    "noncompliant",
                    "error",
                    "conflict",
                    "unknown",
                    "inGracePeriod",
                    "configManager",
                ]
            },
            "kind": "danger",
            "section": "Compliance",
        },

        {
            "title": "Supervised",
            "value": supervised,
            "subtitle": "Apple supervised devices",
            "filter_spec": {"IsSupervised": ["True"]},
            "kind": "good",
            "section": "Security",
        },
        {
            "title": "Not supervised",
            "value": not_supervised,
            "subtitle": "Supervision missing",
            "filter_spec": {"IsSupervised": ["False"]},
            "kind": "warning",
            "section": "Security",
        },
        {
            "title": "Encrypted",
            "value": encrypted,
            "subtitle": "Encrypted devices",
            "filter_spec": {"IsEncrypted": ["True"]},
            "kind": "good",
            "section": "Security",
        },
        {
            "title": "Not encrypted",
            "value": not_encrypted,
            "subtitle": "Encryption missing",
            "filter_spec": {"IsEncrypted": ["False"]},
            "kind": "danger",
            "section": "Security",
        },
        {
            "title": "Jailbroken",
            "value": jailbroken,
            "subtitle": "Jailbreak detected",
            "filter_spec": {"JailBroken": ["True"]},
            "kind": "danger",
            "section": "Security",
        },
        {
            "title": "Activation Lock Codes",
            "value": activation_code,
            "subtitle": "Bypass code available",
            "filter_spec": {"HasActivationBypassCode": ["Yes"]},
            "kind": "accent",
            "section": "Security",
        },

        {
            "title": "Corporate",
            "value": corporate,
            "subtitle": "Corporate ownership",
            "filter_spec": {"OwnerType": ["company", "Company"]},
            "kind": "info",
            "section": "Ownership",
        },
        {
            "title": "Personal",
            "value": personal,
            "subtitle": "Personal ownership",
            "filter_spec": {"OwnerType": ["personal", "Personal"]},
            "kind": "warning",
            "section": "Ownership",
        },

        {
            "title": "Inactive >90d",
            "value": inactive_90,
            "subtitle": "No sync for over 90 days",
            "filter_spec": {"DeviceActivityStatus": ["Inactive >90d"]},
            "kind": "danger",
            "section": "Activity",
        },
        {
            "title": "Never synced",
            "value": never_synced,
            "subtitle": "No sync timestamp",
            "filter_spec": {"DeviceActivityStatus": ["Never synced"]},
            "kind": "warning",
            "section": "Activity",
        },

        {
            "title": "Models",
            "value": len(models),
            "subtitle": "Distinct models",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "Model",
            },
            "kind": "info",
            "section": "Inventory",
        },
    ]