from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from diffasaurus.core.entity.adapters import DEVICE_MANAGED, ReportFamilyAdapter
from diffasaurus.core.entity.bindings import AliasBindingIndex, ResolvedAlias
from diffasaurus.core.entity.pit_enrichment import (
    MANAGED_DEVICES_FAMILY,
    ManagedDevicesCoverage,
    RelatedManagedDevice,
    UserDeviceLinkObservation,
    UserDeviceResolutionStatus,
    UserManagedDevicesEnrichment,
)
from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    FamilyCoverage,
    SourcedProperty,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _row_value(row: dict[str, str], column: str) -> str:
    if not column:
        return ""
    normalized = {header.lower(): header for header in row}
    actual = normalized.get(column.lower())
    if not actual:
        return ""
    return str(row.get(actual, "") or "").strip()


def normalize_device_id(value: str) -> str:
    return value.strip().casefold()


def is_semantically_valid_mail(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    return _EMAIL_RE.match(text) is not None


def device_dedup_key(row: dict[str, str], family: str = MANAGED_DEVICES_FAMILY) -> str:
    """Stable device grouping key. Never uses display name."""
    azure = _row_value(row, "AzureADDeviceId")
    if azure:
        return f"aad:{normalize_device_id(azure)}"
    managed = _row_value(row, "ManagedDeviceId")
    if managed:
        return f"md:{normalize_device_id(managed)}"
    serial = _row_value(row, "SerialNumber")
    if serial:
        return f"serial:{family}:{normalize_device_id(serial)}"
    # Deterministic fallback from available identity fields (not DeviceName alone).
    parts = [
        normalize_device_id(_row_value(row, column))
        for column in ("Manufacturer", "Model", "OperatingSystem", "OSVersion")
        if _row_value(row, column)
    ]
    if parts:
        return f"fallback:{family}:{'|'.join(parts)}"
    return f"fallback:{family}:empty"


def device_entity_key(row: dict[str, str], family: str = MANAGED_DEVICES_FAMILY) -> CanonicalEntityKey | None:
    del family  # adapter family is fixed for managed devices
    return DEVICE_MANAGED.build_key(row)


@dataclass(frozen=True)
class RawDeviceLinkSignals:
    user_id: str
    upn: str
    mail: str
    row: dict[str, str]


def extract_link_signals(row: dict[str, str]) -> RawDeviceLinkSignals:
    return RawDeviceLinkSignals(
        user_id=_row_value(row, "UserId"),
        upn=_row_value(row, "UserPrincipalName"),
        mail=_row_value(row, "EmailAddress"),
        row=row,
    )


def _resolve_alias_kind(
    alias_index: AliasBindingIndex,
    kind: str,
    value: str,
    observed_at: datetime,
) -> ResolvedAlias:
    return alias_index.resolve(kind, value.lower(), observed_at)


@dataclass(frozen=True)
class ResolvedLinkAttempt:
    link_kind: str
    normalized_link_value: str
    status: UserDeviceResolutionStatus
    resolved_user_immutable_id: str | None
    candidate_user_ids: frozenset[str]
    diagnostic: str


def resolve_row_link_attempts(
    row: dict[str, str],
    observed_at: datetime,
    alias_index: AliasBindingIndex,
) -> tuple[ResolvedLinkAttempt, ...]:
    """Resolve every available link signal for one managed-device row at snapshot time."""
    signals = extract_link_signals(row)
    attempts: list[ResolvedLinkAttempt] = []

    if signals.user_id:
        attempts.append(
            ResolvedLinkAttempt(
                link_kind="user_id",
                normalized_link_value=signals.user_id,
                status="resolved",
                resolved_user_immutable_id=signals.user_id,
                candidate_user_ids=frozenset({signals.user_id}),
                diagnostic="UserId exact match",
            )
        )

    if signals.upn:
        resolved = _resolve_alias_kind(alias_index, "upn", signals.upn, observed_at)
        if resolved.status == "bound":
            attempts.append(
                ResolvedLinkAttempt(
                    link_kind="upn",
                    normalized_link_value=signals.upn.lower(),
                    status="resolved",
                    resolved_user_immutable_id=resolved.immutable_id,
                    candidate_user_ids=frozenset({resolved.immutable_id}),
                    diagnostic="UPN bound via historical alias",
                )
            )
        elif resolved.status == "ambiguous":
            attempts.append(
                ResolvedLinkAttempt(
                    link_kind="upn",
                    normalized_link_value=signals.upn.lower(),
                    status="ambiguous",
                    resolved_user_immutable_id=None,
                    candidate_user_ids=frozenset(resolved.candidates),
                    diagnostic="UPN ambiguous at snapshot time",
                )
            )
        else:
            attempts.append(
                ResolvedLinkAttempt(
                    link_kind="upn",
                    normalized_link_value=signals.upn.lower(),
                    status="unbound",
                    resolved_user_immutable_id=None,
                    candidate_user_ids=frozenset(),
                    diagnostic="UPN unbound at snapshot time",
                )
            )

    mail = signals.mail
    if mail and is_semantically_valid_mail(mail):
        if signals.upn and mail.lower() == signals.upn.lower():
            pass
        else:
            resolved = _resolve_alias_kind(alias_index, "mail", mail, observed_at)
            if resolved.status == "bound":
                attempts.append(
                    ResolvedLinkAttempt(
                        link_kind="mail",
                        normalized_link_value=mail.lower(),
                        status="resolved",
                        resolved_user_immutable_id=resolved.immutable_id,
                        candidate_user_ids=frozenset({resolved.immutable_id}),
                        diagnostic="EmailAddress bound via historical mail alias",
                    )
                )
            elif resolved.status == "ambiguous":
                attempts.append(
                    ResolvedLinkAttempt(
                        link_kind="mail",
                        normalized_link_value=mail.lower(),
                        status="ambiguous",
                        resolved_user_immutable_id=None,
                        candidate_user_ids=frozenset(resolved.candidates),
                        diagnostic="EmailAddress ambiguous at snapshot time",
                    )
                )
            else:
                attempts.append(
                    ResolvedLinkAttempt(
                        link_kind="mail",
                        normalized_link_value=mail.lower(),
                        status="unbound",
                        resolved_user_immutable_id=None,
                        candidate_user_ids=frozenset(),
                        diagnostic="EmailAddress unbound at snapshot time",
                    )
                )

    return tuple(attempts)


def _merge_group_attempts(
    attempts_by_row: list[tuple[ResolvedLinkAttempt, ...]],
) -> ResolvedLinkAttempt | None:
    """Collapse per-row attempts for one device into a single observation outcome."""
    flat = [attempt for row_attempts in attempts_by_row for attempt in row_attempts]
    if not flat:
        return None

    user_ids = {
        attempt.resolved_user_immutable_id
        for attempt in flat
        if attempt.link_kind == "user_id" and attempt.resolved_user_immutable_id
    }
    if len(user_ids) > 1:
        return ResolvedLinkAttempt(
            link_kind="user_id",
            normalized_link_value="",
            status="conflicting",
            resolved_user_immutable_id=None,
            candidate_user_ids=frozenset(user_ids),
            diagnostic="Multiple immutable UserId values for the same device",
        )

    user_id = next(iter(user_ids), None)
    upn_resolved = [
        attempt
        for attempt in flat
        if attempt.link_kind == "upn" and attempt.status == "resolved"
    ]
    mail_resolved = [
        attempt
        for attempt in flat
        if attempt.link_kind == "mail" and attempt.status == "resolved"
    ]

    if user_id is not None:
        disagreeing = [
            attempt
            for attempt in upn_resolved + mail_resolved
            if attempt.resolved_user_immutable_id
            and attempt.resolved_user_immutable_id != user_id
        ]
        if disagreeing:
            candidates = {user_id}
            candidates.update(
                attempt.resolved_user_immutable_id
                for attempt in disagreeing
                if attempt.resolved_user_immutable_id
            )
            return ResolvedLinkAttempt(
                link_kind="composite",
                normalized_link_value=user_id,
                status="conflicting",
                resolved_user_immutable_id=None,
                candidate_user_ids=frozenset(candidates),
                diagnostic="UserId conflicts with UPN/mail resolution",
            )
        return ResolvedLinkAttempt(
            link_kind="user_id",
            normalized_link_value=user_id,
            status="resolved",
            resolved_user_immutable_id=user_id,
            candidate_user_ids=frozenset({user_id}),
            diagnostic="UserId exact match (immutable precedence)",
        )

    ambiguous = [attempt for attempt in flat if attempt.status == "ambiguous"]
    if ambiguous:
        candidates: set[str] = set()
        for attempt in ambiguous:
            candidates.update(attempt.candidate_user_ids)
        primary = ambiguous[0]
        return ResolvedLinkAttempt(
            link_kind=primary.link_kind,
            normalized_link_value=primary.normalized_link_value,
            status="ambiguous",
            resolved_user_immutable_id=None,
            candidate_user_ids=frozenset(candidates),
            diagnostic=primary.diagnostic,
        )

    resolved_ids = {
        attempt.resolved_user_immutable_id
        for attempt in upn_resolved + mail_resolved
        if attempt.resolved_user_immutable_id
    }
    if len(resolved_ids) > 1:
        return ResolvedLinkAttempt(
            link_kind="composite",
            normalized_link_value="",
            status="conflicting",
            resolved_user_immutable_id=None,
            candidate_user_ids=frozenset(resolved_ids),
            diagnostic="UPN and mail resolve to different users",
        )
    if len(resolved_ids) == 1:
        chosen = next(
            attempt
            for attempt in upn_resolved + mail_resolved
            if attempt.resolved_user_immutable_id in resolved_ids
        )
        return ResolvedLinkAttempt(
            link_kind=chosen.link_kind,
            normalized_link_value=chosen.normalized_link_value,
            status="resolved",
            resolved_user_immutable_id=chosen.resolved_user_immutable_id,
            candidate_user_ids=frozenset({chosen.resolved_user_immutable_id or ""}),
            diagnostic=chosen.diagnostic,
        )

    unbound = [attempt for attempt in flat if attempt.status == "unbound"]
    if unbound:
        primary = unbound[0]
        return ResolvedLinkAttempt(
            link_kind=primary.link_kind,
            normalized_link_value=primary.normalized_link_value,
            status="unbound",
            resolved_user_immutable_id=None,
            candidate_user_ids=frozenset(),
            diagnostic=primary.diagnostic,
        )

    return None


@dataclass(frozen=True)
class GroupedDeviceObservation:
    dedup_key: str
    device_key: CanonicalEntityKey | None
    outcome: ResolvedLinkAttempt
    rows: tuple[dict[str, str], ...]
    raw_link_data_json: str


def group_managed_device_rows(
    rows: list[dict[str, str]],
    observed_at: datetime,
    alias_index: AliasBindingIndex,
    *,
    family: str = MANAGED_DEVICES_FAMILY,
) -> tuple[GroupedDeviceObservation, ...]:
    """Group rows by stable device key, then resolve conflicts before projection write."""
    buckets: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = device_dedup_key(row, family)
        buckets.setdefault(key, []).append(row)

    grouped: list[GroupedDeviceObservation] = []
    for dedup_key, group_rows in sorted(buckets.items()):
        attempts_by_row = [
            resolve_row_link_attempts(row, observed_at, alias_index) for row in group_rows
        ]
        outcome = _merge_group_attempts(attempts_by_row)
        if outcome is None:
            # No user-link fields — inventory-only row; skip projection observation.
            continue
        device_key = device_entity_key(group_rows[0], family)
        raw_payload = {
            "row_count": len(group_rows),
            "signals": [
                {
                    "UserId": extract_link_signals(row).user_id,
                    "UserPrincipalName": extract_link_signals(row).upn,
                    "EmailAddress": extract_link_signals(row).mail,
                }
                for row in group_rows
            ],
        }
        grouped.append(
            GroupedDeviceObservation(
                dedup_key=dedup_key,
                device_key=device_key,
                outcome=outcome,
                rows=tuple(group_rows),
                raw_link_data_json=json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
            )
        )
    return tuple(grouped)


def build_observation_records(
    *,
    source_id: int,
    file_id: int,
    observed_at: datetime,
    grouped: tuple[GroupedDeviceObservation, ...],
    device_entity_id_for_key: Callable[[CanonicalEntityKey], int | None],
) -> tuple[UserDeviceLinkObservation, ...]:
    observations: list[UserDeviceLinkObservation] = []
    for item in grouped:
        if item.device_key is None:
            continue
        entity_id = device_entity_id_for_key(item.device_key)
        if entity_id is None:
            continue
        outcome = item.outcome
        observations.append(
            UserDeviceLinkObservation(
                source_id=source_id,
                file_id=file_id,
                observed_at=observed_at,
                device_entity_id=entity_id,
                device_dedup_key=item.dedup_key,
                link_kind=outcome.link_kind,
                normalized_link_value=outcome.normalized_link_value,
                resolution_status=outcome.status,
                resolved_user_immutable_id=outcome.resolved_user_immutable_id,
                candidate_user_ids=outcome.candidate_user_ids,
                diagnostic=outcome.diagnostic,
                raw_link_data_json=item.raw_link_data_json,
            )
        )
    return tuple(observations)


def observation_relevant_to_user(
    observation: UserDeviceLinkObservation,
    user_immutable_id: str,
    user_alias_values: set[str],
) -> bool:
    """Whether an unresolved observation could affect enrichment for this user."""
    if observation.resolution_status == "resolved":
        return observation.resolved_user_immutable_id == user_immutable_id
    if user_immutable_id in observation.candidate_user_ids:
        return True
    if observation.normalized_link_value and observation.normalized_link_value in user_alias_values:
        return True
    return False


def determine_managed_devices_coverage(
    *,
    snapshot_exists: bool,
    authoritative_inventory: bool,
    resolved_count: int,
    relevant_unresolved: tuple[UserDeviceLinkObservation, ...],
) -> ManagedDevicesCoverage:
    if not snapshot_exists:
        return "no_coverage"
    if resolved_count > 0:
        return "populated"
    blocking = tuple(
        item
        for item in relevant_unresolved
        if item.resolution_status in ("ambiguous", "conflicting", "unbound")
    )
    if blocking:
        return "ambiguous_association"
    if authoritative_inventory:
        return "known_zero"
    return "unknown"


def properties_from_managed_row(
    row: dict[str, str],
    observed_at: datetime,
    adapter: ReportFamilyAdapter = DEVICE_MANAGED,
) -> tuple[SourcedProperty, ...]:
    return tuple(
        SourcedProperty(
            family=adapter.family,
            name=name,
            value=value,
            observed_at=observed_at,
        )
        for name, value in adapter.card_properties(row, observed_at)
    )


def build_enrichment_from_observations(
    *,
    user_key: CanonicalEntityKey,
    target: datetime,
    snapshot_at: datetime | None,
    snapshot_file_id: int | None,
    source_relative_path: str,
    authoritative_inventory: bool,
    observations: tuple[UserDeviceLinkObservation, ...],
    devices: tuple[RelatedManagedDevice, ...],
    user_alias_values: set[str],
) -> UserManagedDevicesEnrichment:
    family = MANAGED_DEVICES_FAMILY
    if snapshot_at is None:
        coverage_item = FamilyCoverage(
            family=family,
            status="no_snapshot",
            requested_at=target,
            snapshot_at=None,
            gap=None,
            entity_present=False,
            source_relative_path="",
            source_report_family="",
        )
        return UserManagedDevicesEnrichment(
            devices=(),
            coverage="no_coverage",
            family_coverage=coverage_item,
            unresolved_observations=(),
            snapshot_at=None,
            snapshot_file_id=None,
            source_relative_path="",
        )

    relevant_unresolved = tuple(
        item
        for item in observations
        if item.resolution_status != "resolved"
        and observation_relevant_to_user(item, user_key.primary_id, user_alias_values)
    )
    coverage = determine_managed_devices_coverage(
        snapshot_exists=True,
        authoritative_inventory=authoritative_inventory,
        resolved_count=len(devices),
        relevant_unresolved=relevant_unresolved,
    )
    gap = target - snapshot_at
    from diffasaurus.core.report_history import report_family

    source_report_family = (
        report_family(source_relative_path) if source_relative_path else family
    )
    family_coverage = FamilyCoverage(
        family=family,
        status="snapshot_used",
        requested_at=target,
        snapshot_at=snapshot_at,
        gap=gap,
        entity_present=len(devices) > 0,
        source_relative_path=source_relative_path,
        source_report_family=source_report_family,
    )
    return UserManagedDevicesEnrichment(
        devices=devices,
        coverage=coverage,
        family_coverage=family_coverage,
        unresolved_observations=relevant_unresolved,
        snapshot_at=snapshot_at,
        snapshot_file_id=snapshot_file_id,
        source_relative_path=source_relative_path,
    )
