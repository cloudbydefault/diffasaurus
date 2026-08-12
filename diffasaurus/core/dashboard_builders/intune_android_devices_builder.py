def looks_like_intune_android_devices_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    android_specific = {
        "androidsecuritypatchlevel",
        "rooted",
        "partnerreportedthreatstate",
        "deviceregistrationstate",
    }
    ios_specific = {
        "udid",
        "issupervised",
        "activationlockbypasscode",
        "hasactivationbypasscode",
    }
    managed_specific = {
        "manageddeviceid",
        "userid",
        "jailbroken",
    }

    required = {
        "devicename",
        "operatingsystem",
        "compliancestate",
        "intunedeviceid",
    }

    if not required.issubset(normalized):
        return False
    if len(normalized.intersection(android_specific)) < 2:
        return False
    if len(normalized.intersection(ios_specific)) >= 2:
        return False
    if normalized.intersection(managed_specific):
        return False
    return True


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


def _is_true(value: str) -> bool:
    return value.strip().lower() == "true"


def _is_nonblank(value: str) -> bool:
    return bool(value.strip())


def build_intune_android_devices_stats(model, headers: list[str]) -> list[dict]:
    idx_compliance = find_header_index(headers, ["ComplianceState"])
    idx_owner = find_header_index(headers, ["OwnerType"])
    idx_encrypted = find_header_index(headers, ["IsEncrypted"])
    idx_rooted = find_header_index(headers, ["Rooted"])
    idx_threat = find_header_index(headers, ["PartnerReportedThreatState"])
    idx_patch = find_header_index(headers, ["AndroidSecurityPatchLevel"])
    idx_activity = find_header_index(headers, ["DeviceActivityStatus"])
    idx_manufacturer = find_header_index(headers, ["Manufacturer"])
    idx_model = find_header_index(headers, ["Model"])
    idx_user = find_header_index(headers, ["UserPrincipalName"])
    idx_enrollment_type = find_header_index(headers, ["DeviceEnrollmentType"])
    idx_enrollment_profile = find_header_index(headers, ["EnrollmentProfileName"])
    idx_imei = find_header_index(headers, ["IMEI"])
    idx_phone = find_header_index(headers, ["PhoneNumber"])
    idx_iccid = find_header_index(headers, ["ICCID"])
    idx_carrier = find_header_index(headers, ["SubscriberCarrier"])

    total_rows = model.rowCount()

    compliant = 0
    non_compliant = 0
    encrypted = 0
    not_encrypted = 0
    rooted = 0
    threat_reported = 0
    patch_known = 0
    corporate = 0
    personal = 0
    active_30 = 0
    stale_31_90 = 0
    inactive_90 = 0
    never_synced = 0
    imei_available = 0
    phone_available = 0
    iccid_available = 0

    manufacturers = set()
    models = set()
    users = set()
    enrollment_types = set()
    enrollment_profiles = set()
    carriers = set()

    for r in range(total_rows):
        compliance = cell_str(model, r, idx_compliance).lower()
        owner = cell_str(model, r, idx_owner).lower()
        encrypted_value = cell_str(model, r, idx_encrypted)
        rooted_value = cell_str(model, r, idx_rooted)
        threat_value = cell_str(model, r, idx_threat)
        patch_value = cell_str(model, r, idx_patch)
        activity = cell_str(model, r, idx_activity).lower()
        manufacturer = cell_str(model, r, idx_manufacturer)
        model_value = cell_str(model, r, idx_model)
        user_value = cell_str(model, r, idx_user)
        enrollment_type = cell_str(model, r, idx_enrollment_type)
        enrollment_profile = cell_str(model, r, idx_enrollment_profile)
        imei_value = cell_str(model, r, idx_imei)
        phone_value = cell_str(model, r, idx_phone)
        iccid_value = cell_str(model, r, idx_iccid)
        carrier_value = cell_str(model, r, idx_carrier)

        if compliance == "compliant":
            compliant += 1
        elif compliance and compliance != "compliant":
            non_compliant += 1

        if _is_true(encrypted_value):
            encrypted += 1
        elif encrypted_value.strip().lower() == "false":
            not_encrypted += 1

        if _is_true(rooted_value):
            rooted += 1

        if _is_nonblank(threat_value):
            threat_reported += 1

        if _is_nonblank(patch_value):
            patch_known += 1

        if owner == "company":
            corporate += 1
        elif owner == "personal":
            personal += 1

        if activity == "active <=30d":
            active_30 += 1
        elif activity == "stale 31-90d":
            stale_31_90 += 1
        elif activity == "inactive >90d":
            inactive_90 += 1
        elif activity == "never synced":
            never_synced += 1

        if _is_nonblank(imei_value):
            imei_available += 1
        if _is_nonblank(phone_value):
            phone_available += 1
        if _is_nonblank(iccid_value):
            iccid_available += 1

        if manufacturer:
            manufacturers.add(manufacturer)
        if model_value:
            models.add(model_value)
        if user_value:
            users.add(user_value)
        if enrollment_type:
            enrollment_types.add(enrollment_type)
        if enrollment_profile:
            enrollment_profiles.add(enrollment_profile)
        if carrier_value:
            carriers.add(carrier_value)

    return [
        {
            "title": "Android Devices",
            "value": total_rows,
            "subtitle": "Total Android rows",
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
            "title": "Manufacturers",
            "value": len(manufacturers),
            "subtitle": "Distinct manufacturers",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "Manufacturer",
            },
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Models",
            "value": len(models),
            "subtitle": "Distinct models",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "Model",
            },
            "kind": "accent",
            "section": "Overview",
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
            "title": "Rooted",
            "value": rooted,
            "subtitle": "Root detected",
            "filter_spec": {"Rooted": ["True"]},
            "kind": "danger",
            "section": "Security",
        },
        {
            "title": "Threat state reported",
            "value": threat_reported,
            "subtitle": "Nonblank partner threat state",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "PartnerReportedThreatState",
            },
            "kind": "warning",
            "section": "Security",
        },
        {
            "title": "Security patch known",
            "value": patch_known,
            "subtitle": "Android patch level recorded",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "AndroidSecurityPatchLevel",
            },
            "kind": "info",
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
            "title": "Active <=30d",
            "value": active_30,
            "subtitle": "Synced within 30 days",
            "filter_spec": {"DeviceActivityStatus": ["Active <=30d"]},
            "kind": "good",
            "section": "Activity",
        },
        {
            "title": "Stale 31-90d",
            "value": stale_31_90,
            "subtitle": "No sync for 31-90 days",
            "filter_spec": {"DeviceActivityStatus": ["Stale 31-90d"]},
            "kind": "warning",
            "section": "Activity",
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
            "title": "Enrollment types",
            "value": len(enrollment_types),
            "subtitle": "Distinct enrollment types",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "DeviceEnrollmentType",
            },
            "kind": "info",
            "section": "Enrollment",
        },
        {
            "title": "Enrollment profiles",
            "value": len(enrollment_profiles),
            "subtitle": "Distinct enrollment profiles",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "EnrollmentProfileName",
            },
            "kind": "accent",
            "section": "Enrollment",
        },
        {
            "title": "IMEI available",
            "value": imei_available,
            "subtitle": "Devices with IMEI recorded",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "IMEI",
            },
            "kind": "info",
            "section": "Cellular / inventory",
        },
        {
            "title": "Phone number available",
            "value": phone_available,
            "subtitle": "Devices with phone number recorded",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "PhoneNumber",
            },
            "kind": "info",
            "section": "Cellular / inventory",
        },
        {
            "title": "ICCID available",
            "value": iccid_available,
            "subtitle": "Devices with ICCID recorded",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "ICCID",
            },
            "kind": "info",
            "section": "Cellular / inventory",
        },
        {
            "title": "Carriers",
            "value": len(carriers),
            "subtitle": "Distinct carriers",
            "filter_spec": {
                "__mode__": "distinct_nonblank",
                "column": "SubscriberCarrier",
            },
            "kind": "accent",
            "section": "Cellular / inventory",
        },
    ]
