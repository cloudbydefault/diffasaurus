from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from diffasaurus.core.entity.types import (
    CanonicalEntityKey,
    FamilyCoverage,
    SourcedProperty,
)

if TYPE_CHECKING:
    from diffasaurus.core.entity.pit_presentation import SourceProvenance

ManagedDevicesCoverage = Literal[
    "populated",
    "known_zero",
    "no_coverage",
    "ambiguous_association",
    "unknown",
]

UserDeviceResolutionStatus = Literal[
    "resolved",
    "ambiguous",
    "unbound",
    "conflicting",
]

MANAGED_DEVICES_FAMILY = "Intune_ManagedDevices_Compliance"
AUTOPILOT_FAMILY = "Intune_Devices_Autopilot"

AutopilotMatchStatus = Literal[
    "matched",
    "no_match_with_coverage",
    "no_coverage",
    "ambiguous",
    "not_applicable",
]


@dataclass(frozen=True)
class AutopilotKeyMatch:
    key_kind: str
    raw_value: str
    normalized_value: str
    resolution_status: Literal["unique", "ambiguous", "absent", "invalid"]
    matched_row_index: int | None
    candidate_row_indices: frozenset[int]


@dataclass(frozen=True)
class RelatedAutopilotState:
    status: AutopilotMatchStatus
    properties: tuple[SourcedProperty, ...]
    provenance: SourceProvenance | None
    key_matches: tuple[AutopilotKeyMatch, ...]
    matched_row_index: int | None
    conflict_diagnostic: str


@dataclass(frozen=True)
class UserDeviceLinkObservation:
    """One auditable projection row after snapshot grouping."""

    source_id: int
    file_id: int
    observed_at: datetime
    device_entity_id: int
    device_dedup_key: str
    link_kind: str
    normalized_link_value: str
    resolution_status: UserDeviceResolutionStatus
    resolved_user_immutable_id: str | None
    candidate_user_ids: frozenset[str]
    diagnostic: str
    raw_link_data_json: str = ""


@dataclass(frozen=True)
class RelatedManagedDevice:
    device_key: CanonicalEntityKey
    dedup_key: str
    properties: tuple[SourcedProperty, ...]
    provenance: SourceProvenance
    link_kind: str
    normalized_link_value: str
    resolution_status: UserDeviceResolutionStatus
    resolved_user_immutable_id: str | None
    candidate_user_ids: frozenset[str]
    diagnostic: str


@dataclass(frozen=True)
class EnrichedManagedDevice:
    device: RelatedManagedDevice
    autopilot: RelatedAutopilotState


@dataclass(frozen=True)
class UserManagedDevicesEnrichment:
    devices: tuple[RelatedManagedDevice, ...]
    coverage: ManagedDevicesCoverage
    family_coverage: FamilyCoverage | None
    unresolved_observations: tuple[UserDeviceLinkObservation, ...]
    snapshot_at: datetime | None
    snapshot_file_id: int | None
    source_relative_path: str = ""
    enriched_devices: tuple[EnrichedManagedDevice, ...] = ()
    autopilot_family_coverage: FamilyCoverage | None = None


@dataclass(frozen=True)
class UserPointInTimeEnrichment:
    """Combined Point-in-Time enrichment including managed devices and Autopilot."""

    managed_devices: UserManagedDevicesEnrichment
