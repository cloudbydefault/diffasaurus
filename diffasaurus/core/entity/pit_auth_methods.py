from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from diffasaurus.core.entity.types import EntityState, FamilyCoverage, SourcedProperty

if TYPE_CHECKING:
    from diffasaurus.core.entity.pit_presentation import ProvenanceObservation, SourceProvenance

AUTH_METHODS_FAMILY = "Entra_Users_AuthenticationMethods"

# Property-level authority within the auth family. MethodsRegistered is the direct
# Graph registration field; AuthenticationMethods is the legacy compatibility column
# populated from the same Graph source in the PowerShell export.
AUTH_METHODS_PROPERTY_AUTHORITY: tuple[str, ...] = (
    "MethodsRegistered",
    "AuthenticationMethods",
)

AuthMethodsCoverage = Literal["populated", "known_empty", "no_coverage", "unknown"]


@dataclass(frozen=True)
class AuthMethodsSource:
    property_name: str
    raw_value: str
    provenance: SourceProvenance
    parsed_methods: tuple[str, ...]


@dataclass(frozen=True)
class AuthMethodsConflictAlternate:
    property_name: str
    parsed_methods: tuple[str, ...]
    provenance: SourceProvenance


@dataclass(frozen=True)
class AuthMethodsConflict:
    authoritative_property: str
    authoritative_methods: tuple[str, ...]
    alternates: tuple[AuthMethodsConflictAlternate, ...]


@dataclass(frozen=True)
class ParsedAuthMethods:
    sources: tuple[AuthMethodsSource, ...]
    methods: tuple[str, ...]
    coverage: AuthMethodsCoverage
    has_conflict: bool
    conflict: AuthMethodsConflict | None


def parse_auth_method_tokens(raw_value: str) -> tuple[str, ...]:
    """Split on semicolon, trim, drop empties, dedupe case-insensitively."""
    if not raw_value:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for part in raw_value.split(";"):
        token = part.strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    return tuple(result)


def _parsed_method_set(methods: tuple[str, ...]) -> frozenset[str]:
    return frozenset(method.casefold() for method in methods)


def _property_authority_rank(property_name: str) -> int:
    if property_name in AUTH_METHODS_PROPERTY_AUTHORITY:
        return AUTH_METHODS_PROPERTY_AUTHORITY.index(property_name)
    return len(AUTH_METHODS_PROPERTY_AUTHORITY)


def _observation_for_property(
    prop: SourcedProperty,
    coverage: FamilyCoverage | None,
    requested_at: datetime,
) -> ProvenanceObservation:
    from diffasaurus.core.entity.pit_presentation import ProvenanceObservation

    return ProvenanceObservation(
        family=prop.family,
        observed_at=prop.observed_at,
        snapshot_at=coverage.snapshot_at if coverage else None,
        requested_at=requested_at,
        gap=coverage.gap if coverage else None,
    )


def _merge_method_lists(
    sources: tuple[AuthMethodsSource, ...],
) -> tuple[str, ...]:
    """Merge parsed tokens preserving first spelling and first-seen order."""
    seen: set[str] = set()
    merged: list[str] = []
    ordered_sources = sorted(sources, key=lambda item: _property_authority_rank(item.property_name))
    for source in ordered_sources:
        for method in source.parsed_methods:
            key = method.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(method)
    return tuple(merged)


def _build_conflict(
    sources: tuple[AuthMethodsSource, ...],
    authoritative: AuthMethodsSource,
) -> AuthMethodsConflict:
    alternates: list[AuthMethodsConflictAlternate] = []
    for source in sources:
        if source.property_name == authoritative.property_name:
            continue
        if _parsed_method_set(source.parsed_methods) == _parsed_method_set(authoritative.parsed_methods):
            continue
        alternates.append(
            AuthMethodsConflictAlternate(
                property_name=source.property_name,
                parsed_methods=source.parsed_methods,
                provenance=source.provenance,
            )
        )
    return AuthMethodsConflict(
        authoritative_property=authoritative.property_name,
        authoritative_methods=authoritative.parsed_methods,
        alternates=tuple(alternates),
    )


def _resolve_auth_methods(
    sources: tuple[AuthMethodsSource, ...],
) -> tuple[tuple[str, ...], bool, AuthMethodsConflict | None]:
    if not sources:
        return (), False, None

    ordered = sorted(sources, key=lambda item: _property_authority_rank(item.property_name))
    authoritative = ordered[0]
    auth_set = _parsed_method_set(authoritative.parsed_methods)

    has_conflict = False
    for source in ordered[1:]:
        if _parsed_method_set(source.parsed_methods) != auth_set:
            has_conflict = True
            break

    if has_conflict:
        conflict = _build_conflict(sources, authoritative)
        return tuple(authoritative.parsed_methods), True, conflict

    return _merge_method_lists(sources), False, None


def _auth_method_properties(
    state: EntityState,
) -> dict[str, SourcedProperty]:
    properties = state.scalar_properties_by_family.get(AUTH_METHODS_FAMILY, ())
    result: dict[str, SourcedProperty] = {}
    for prop in properties:
        if prop.name in AUTH_METHODS_PROPERTY_AUTHORITY:
            result[prop.name] = prop
    return result


def build_parsed_auth_methods(state: EntityState) -> ParsedAuthMethods | None:
    from diffasaurus.core.entity.pit_presentation import single_provenance

    if state.key.entity_type != "user":
        return None

    coverage_map = {item.family: item for item in state.coverage}
    coverage = coverage_map.get(AUTH_METHODS_FAMILY)

    if coverage is None or coverage.status == "no_snapshot":
        return ParsedAuthMethods(
            sources=(),
            methods=(),
            coverage="no_coverage",
            has_conflict=False,
            conflict=None,
        )

    if coverage.status == "entity_absent":
        return ParsedAuthMethods(
            sources=(),
            methods=(),
            coverage="unknown",
            has_conflict=False,
            conflict=None,
        )

    if coverage.status != "snapshot_used":
        return ParsedAuthMethods(
            sources=(),
            methods=(),
            coverage="unknown",
            has_conflict=False,
            conflict=None,
        )

    method_props = _auth_method_properties(state)
    sources: list[AuthMethodsSource] = []
    for property_name in AUTH_METHODS_PROPERTY_AUTHORITY:
        prop = method_props.get(property_name)
        if prop is None:
            continue
        raw_value = prop.value
        obs = _observation_for_property(prop, coverage, state.as_of)
        sources.append(
            AuthMethodsSource(
                property_name=property_name,
                raw_value=raw_value,
                provenance=single_provenance(obs),
                parsed_methods=parse_auth_method_tokens(raw_value),
            )
        )

    # snapshot_used but no method columns in state — treat as known empty if user row exists
    if not sources:
        return ParsedAuthMethods(
            sources=(),
            methods=(),
            coverage="known_empty",
            has_conflict=False,
            conflict=None,
        )

    if not any(source.raw_value.strip() for source in sources):
        return ParsedAuthMethods(
            sources=tuple(sources),
            methods=(),
            coverage="known_empty",
            has_conflict=False,
            conflict=None,
        )

    sources_with_tokens = tuple(source for source in sources if source.parsed_methods)
    if not sources_with_tokens:
        return ParsedAuthMethods(
            sources=tuple(sources),
            methods=(),
            coverage="known_empty",
            has_conflict=False,
            conflict=None,
        )

    methods, has_conflict, conflict = _resolve_auth_methods(sources_with_tokens)
    return ParsedAuthMethods(
        sources=tuple(sources),
        methods=methods,
        coverage="populated",
        has_conflict=has_conflict,
        conflict=conflict,
    )


def auth_methods_collection_provenance(parsed: ParsedAuthMethods) -> SourceProvenance:
    from diffasaurus.core.entity.pit_presentation import merge_provenance

    parts = [source.provenance for source in parsed.sources]
    if not parts:
        return merge_provenance()
    return merge_provenance(*parts)
