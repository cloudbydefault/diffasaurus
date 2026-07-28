def looks_like_authentication_methods_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    # New hybrid report
    hybrid_required = {
        "authenticationmethods",
        "ismfaregistered",
        "ismfacapable",
        "ispasswordlesscapable",
        "isssprregistered",
    }

    # Old detailed report compatibility
    legacy_required = {
        "authenticationmethods",
        "prefsstatus",
        "methodsstatus",
    }

    return hybrid_required.issubset(normalized) or legacy_required.issubset(normalized)


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


def is_true(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def is_false(value: str) -> bool:
    return str(value).strip().lower() in {"false", "0", "no", "n"}


def build_authentication_methods_stats(model, headers: list[str]) -> list[dict]:
    idx_upn = find_header_index(headers, ["UPN", "UserPrincipalName"])
    idx_enabled = find_header_index(headers, ["AccountEnabled"])
    idx_methods = find_header_index(headers, ["AuthenticationMethods", "MethodsRegistered"])
    idx_user_type = find_header_index(headers, ["UserType"])
    idx_sys_pref = find_header_index(headers, ["IsSystemPreferredAuthenticationMethodEnabled"])

    idx_is_admin = find_header_index(headers, ["IsAdmin"])
    idx_mfa_registered = find_header_index(headers, ["IsMfaRegistered"])
    idx_mfa_capable = find_header_index(headers, ["IsMfaCapable"])
    idx_passwordless = find_header_index(headers, ["IsPasswordlessCapable"])
    idx_sspr_registered = find_header_index(headers, ["IsSsprRegistered"])
    idx_sspr_enabled = find_header_index(headers, ["IsSsprEnabled"])
    idx_sspr_capable = find_header_index(headers, ["IsSsprCapable"])

    total_rows = model.rowCount()

    users = set()
    enabled = 0
    disabled = 0
    guests = 0
    members = 0

    with_methods = 0
    without_methods = 0
    with_authenticator = 0
    with_phone = 0
    with_fido2 = 0
    with_tap = 0
    system_pref_enabled = 0

    admins = 0
    admins_without_mfa = 0

    mfa_registered = 0
    mfa_missing = 0
    mfa_capable = 0

    passwordless_capable = 0
    passwordless_missing = 0

    sspr_registered = 0
    sspr_missing = 0
    sspr_enabled = 0
    sspr_capable = 0

    for r in range(total_rows):
        upn = cell_str(model, r, idx_upn)
        acc_enabled = cell_str(model, r, idx_enabled)
        methods = cell_str(model, r, idx_methods)
        user_type = cell_str(model, r, idx_user_type).lower()
        sys_pref = cell_str(model, r, idx_sys_pref)

        is_admin_value = cell_str(model, r, idx_is_admin)
        mfa_registered_value = cell_str(model, r, idx_mfa_registered)
        mfa_capable_value = cell_str(model, r, idx_mfa_capable)
        passwordless_value = cell_str(model, r, idx_passwordless)
        sspr_registered_value = cell_str(model, r, idx_sspr_registered)
        sspr_enabled_value = cell_str(model, r, idx_sspr_enabled)
        sspr_capable_value = cell_str(model, r, idx_sspr_capable)

        if upn:
            users.add(upn.lower())

        if is_true(acc_enabled):
            enabled += 1
        elif is_false(acc_enabled):
            disabled += 1

        if user_type == "guest":
            guests += 1
        elif user_type == "member":
            members += 1

        if methods:
            with_methods += 1
            ml = methods.lower()

            if "microsoft authenticator" in ml:
                with_authenticator += 1

            if "phone" in ml or "sms" in ml or "voice" in ml:
                with_phone += 1

            if "fido2" in ml or "passkey" in ml:
                with_fido2 += 1

            if "temporary access pass" in ml or "tap" in ml:
                with_tap += 1
        else:
            without_methods += 1

        if is_true(sys_pref):
            system_pref_enabled += 1

        if is_true(is_admin_value):
            admins += 1
            if is_false(mfa_registered_value):
                admins_without_mfa += 1

        if is_true(mfa_registered_value):
            mfa_registered += 1
        elif is_false(mfa_registered_value):
            mfa_missing += 1

        if is_true(mfa_capable_value):
            mfa_capable += 1

        if is_true(passwordless_value):
            passwordless_capable += 1
        elif is_false(passwordless_value):
            passwordless_missing += 1

        if is_true(sspr_registered_value):
            sspr_registered += 1
        elif is_false(sspr_registered_value):
            sspr_missing += 1

        if is_true(sspr_enabled_value):
            sspr_enabled += 1

        if is_true(sspr_capable_value):
            sspr_capable += 1

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
            "title": "Distinct UPNs",
            "value": len(users),
            "subtitle": "Unique users",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Enabled",
            "value": enabled,
            "subtitle": "Enabled accounts",
            "filter_spec": {"AccountEnabled": ["True"]},
            "kind": "good",
            "section": "Overview",
        },
        {
            "title": "Disabled",
            "value": disabled,
            "subtitle": "Disabled accounts",
            "filter_spec": {"AccountEnabled": ["False"]},
            "kind": "danger",
            "section": "Overview",
        },
        {
            "title": "Members",
            "value": members,
            "subtitle": "Internal users",
            "filter_spec": {"UserType": ["Member"]},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Guests",
            "value": guests,
            "subtitle": "Guest users",
            "filter_spec": {"UserType": ["Guest"]},
            "kind": "warning",
            "section": "Overview",
        },
        {
            "title": "MFA Registered",
            "value": mfa_registered,
            "subtitle": "Users registered for MFA",
            "filter_spec": {"IsMfaRegistered": ["True"]},
            "kind": "good",
            "section": "Healthy State",
        },
        {
            "title": "MFA Missing",
            "value": mfa_missing,
            "subtitle": "Users not registered for MFA",
            "filter_spec": {"IsMfaRegistered": ["False"]},
            "kind": "danger",
            "section": "Risks",
        },
        {
            "title": "MFA Capable",
            "value": mfa_capable,
            "subtitle": "Users capable of MFA",
            "filter_spec": {"IsMfaCapable": ["True"]},
            "kind": "good",
            "section": "Healthy State",
        },
        {
            "title": "Passwordless Ready",
            "value": passwordless_capable,
            "subtitle": "Passwordless capable users",
            "filter_spec": {"IsPasswordlessCapable": ["True"]},
            "kind": "accent",
            "section": "Healthy State",
        },
        {
            "title": "Passwordless Missing",
            "value": passwordless_missing,
            "subtitle": "Not passwordless capable",
            "filter_spec": {"IsPasswordlessCapable": ["False"]},
            "kind": "warning",
            "section": "Risks",
        },
        {
            "title": "SSPR Registered",
            "value": sspr_registered,
            "subtitle": "Self-service reset registered",
            "filter_spec": {"IsSsprRegistered": ["True"]},
            "kind": "good",
            "section": "Healthy State",
        },
        {
            "title": "SSPR Missing",
            "value": sspr_missing,
            "subtitle": "Not registered for SSPR",
            "filter_spec": {"IsSsprRegistered": ["False"]},
            "kind": "warning",
            "section": "Risks",
        },
        {
            "title": "Admin Accounts",
            "value": admins,
            "subtitle": "Privileged users",
            "filter_spec": {"IsAdmin": ["True"]},
            "kind": "accent",
            "section": "Overview",
        },
        {
            "title": "Admins Without MFA",
            "value": admins_without_mfa,
            "subtitle": "Privileged risk",
            "filter_spec": {
                "__mode__": "and",
                "conditions": [
                    {"column": "IsAdmin", "values": ["True"]},
                    {"column": "IsMfaRegistered", "values": ["False"]},
                ],
            },
            "kind": "danger",
            "section": "Risks",
        },
        {
            "title": "With Methods",
            "value": with_methods,
            "subtitle": "Authentication methods present",
            "filter_spec": {
                "__mode__": "nonblank",
                "column": "AuthenticationMethods",
            },
            "kind": "good",
            "section": "Healthy State",
        },
        {
            "title": "Without Methods",
            "value": without_methods,
            "subtitle": "No methods listed",
            "filter_spec": {
                "__mode__": "blank",
                "column": "AuthenticationMethods",
            },
            "kind": "danger",
            "section": "Risks",
        },
        {
            "title": "Authenticator",
            "value": with_authenticator,
            "subtitle": "Microsoft Authenticator",
            "filter_spec": {
                "__mode__": "contains",
                "column": "AuthenticationMethods",
                "value": "Microsoft Authenticator",
            },
            "kind": "good",
            "section": "Methods",
        },
        {
            "title": "Phone",
            "value": with_phone,
            "subtitle": "Phone / SMS / Voice",
            "filter_spec": {
                "__mode__": "contains_any",
                "column": "AuthenticationMethods",
                "values": ["Phone", "SMS", "Voice"],
            },
            "kind": "warning",
            "section": "Methods",
        },
        {
            "title": "FIDO2",
            "value": with_fido2,
            "subtitle": "Passkey / FIDO2",
            "filter_spec": {
                "__mode__": "contains_any",
                "column": "AuthenticationMethods",
                "values": ["FIDO2", "Passkey"],
            },
            "kind": "accent",
            "section": "Methods",
        },
        {
            "title": "Temporary Access Pass",
            "value": with_tap,
            "subtitle": "TAP present",
            "filter_spec": {
                "__mode__": "contains_any",
                "column": "AuthenticationMethods",
                "values": ["Temporary Access Pass", "TAP"],
            },
            "kind": "accent",
            "section": "Methods",
        },
        {
            "title": "System Preferred Enabled",
            "value": system_pref_enabled,
            "subtitle": "System preferred on",
            "filter_spec": {"IsSystemPreferredAuthenticationMethodEnabled": ["True"]},
            "kind": "good",
            "section": "Methods",
        },
    ]