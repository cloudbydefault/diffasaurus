from __future__ import annotations

# Raw report-family names from filenames that map to a canonical entity adapter family.
ENTITY_FAMILY_ALIASES: dict[str, str] = {
    "Entra_Users_AuthenticationMethods_Hybrid": "Entra_Users_AuthenticationMethods",
}


def canonical_entity_family(raw_family: str) -> str:
    return ENTITY_FAMILY_ALIASES.get(raw_family, raw_family)


def entity_family_names_for_adapter(canonical_family: str) -> tuple[str, ...]:
    names: list[str] = [canonical_family]
    for alias, target in ENTITY_FAMILY_ALIASES.items():
        if target == canonical_family and alias not in names:
            names.append(alias)
    return tuple(names)


def snapshots_for_adapter(
    families: dict[str, list],
    canonical_family: str,
) -> list:
    merged: list = []
    for name in entity_family_names_for_adapter(canonical_family):
        merged.extend(families.get(name, []))
    merged.sort(key=lambda item: item.captured_at)
    return merged
