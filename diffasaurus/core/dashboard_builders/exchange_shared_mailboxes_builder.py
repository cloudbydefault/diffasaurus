def looks_like_exchange_shared_mailboxes_report(headers: list[str]) -> bool:
    normalized = {str(h).strip().lower() for h in headers if h}

    required = {
        "displayname",
        "primarysmtpaddress",
        "hasfullaccessdelegates",
        "hassendasdelegates",
        "hassendonbehalfdelegates",
        "hasforwarding",
    }

    return required.issubset(normalized)

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
    return str(value).strip().lower() in {"true", "yes", "1"}


def is_nonblank(value: str) -> bool:
    raw = str(value or "").strip()
    return bool(raw) and not raw.lower().startswith("error")


def build_exchange_shared_mailboxes_stats(model, headers: list[str]) -> list[dict]:
    idx_hidden = find_header_index(headers, ["HiddenFromGAL", "HiddenFromAddressListsEnabled"])
    idx_full = find_header_index(headers, ["HasFullAccessDelegates"])
    idx_send_as = find_header_index(headers, ["HasSendAsDelegates"])
    idx_sob = find_header_index(headers, ["HasSendOnBehalfDelegates"])
    idx_forwarding = find_header_index(headers, ["ForwardingEnabled", "HasForwarding"])
    idx_litigation = find_header_index(headers, ["LitigationHoldEnabled"])
    idx_retention = find_header_index(headers, ["RetentionPolicy"])
    idx_no_delegates = find_header_index(headers, ["NoDelegatesConfigured"])

    total = model.rowCount()

    hidden = 0
    visible = 0
    with_full = 0
    without_full = 0
    with_send_as = 0
    with_sob = 0
    with_any_delegation = 0
    no_delegates = 0
    forwarding = 0
    litigation = 0
    retention = 0

    for r in range(total):
        hidden_val = cell_str(model, r, idx_hidden)
        full_val = cell_str(model, r, idx_full)
        send_as_val = cell_str(model, r, idx_send_as)
        sob_val = cell_str(model, r, idx_sob)
        forwarding_val = cell_str(model, r, idx_forwarding)
        litigation_val = cell_str(model, r, idx_litigation)
        retention_val = cell_str(model, r, idx_retention)
        no_delegates_val = cell_str(model, r, idx_no_delegates)

        has_full = is_true(full_val)
        has_send_as = is_true(send_as_val)
        has_sob = is_true(sob_val)

        if is_true(hidden_val):
            hidden += 1
        else:
            visible += 1

        if has_full:
            with_full += 1
        else:
            without_full += 1

        if has_send_as:
            with_send_as += 1

        if has_sob:
            with_sob += 1

        if has_full or has_send_as or has_sob:
            with_any_delegation += 1

        if is_true(no_delegates_val):
            no_delegates += 1

        if is_true(forwarding_val):
            forwarding += 1

        if is_true(litigation_val):
            litigation += 1

        if is_nonblank(retention_val):
            retention += 1

    return [
        {
            "title": "Shared Mailboxes",
            "value": total,
            "subtitle": "Total shared mailboxes",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },

        {
            "title": "With Any Delegation",
            "value": with_any_delegation,
            "subtitle": "Any mailbox delegation",
            "filter_spec": {},
            "kind": "good",
            "section": "Delegation",
        },
        {
            "title": "With Full Access",
            "value": with_full,
            "subtitle": "Full Access delegates",
            "filter_spec": {"HasFullAccessDelegates": ["True"]},
            "kind": "good",
            "section": "Delegation",
        },
        {
            "title": "With Send As",
            "value": with_send_as,
            "subtitle": "Send As delegates",
            "filter_spec": {"HasSendAsDelegates": ["True"]},
            "kind": "accent",
            "section": "Delegation",
        },
        {
            "title": "With Send on Behalf",
            "value": with_sob,
            "subtitle": "Send on behalf delegates",
            "filter_spec": {"HasSendOnBehalfDelegates": ["True"]},
            "kind": "accent",
            "section": "Delegation",
        },

        {
            "title": "No Delegates",
            "value": no_delegates,
            "subtitle": "No delegation configured",
            "filter_spec": {"NoDelegatesConfigured": ["True"]},
            "kind": "warning",
            "section": "Review Required",
        },
        {
            "title": "Forwarding Enabled",
            "value": forwarding,
            "subtitle": "Mailbox forwarding configured",
            "filter_spec": {"HasForwarding": ["True"]},
            "kind": "danger",
            "section": "Review Required",
        },

        {
            "title": "Visible in GAL",
            "value": visible,
            "subtitle": "Visible address list entries",
            "filter_spec": {"HiddenFromAddressListsEnabled": ["False"]},
            "kind": "good",
            "section": "Visibility",
        },
        {
            "title": "Hidden from GAL",
            "value": hidden,
            "subtitle": "Hidden address list entries",
            "filter_spec": {"HiddenFromAddressListsEnabled": ["True"]},
            "kind": "warning",
            "section": "Visibility",
        },

        {
            "title": "Litigation Hold",
            "value": litigation,
            "subtitle": "Litigation hold enabled",
            "filter_spec": {"LitigationHoldEnabled": ["True"]},
            "kind": "security",
            "section": "Compliance",
        },
        {
            "title": "With Retention Policy",
            "value": retention,
            "subtitle": "Retention policy assigned",
            "filter_spec": {
                "__mode__": "nonblank",
                "column": "RetentionPolicy"
            },
            "kind": "compliance",
            "section": "Compliance",
        },

        {
            "title": "Without Full Access",
            "value": without_full,
            "subtitle": "No Full Access delegates",
            "filter_spec": {"HasFullAccessDelegates": ["False"]},
            "kind": "warning",
            "section": "Review Required",
        },
    ]