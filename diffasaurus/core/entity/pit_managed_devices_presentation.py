from __future__ import annotations

from datetime import datetime

from diffasaurus.core.entity.pit_enrichment import (
    AUTOPILOT_FAMILY,
    EnrichedManagedDevice,
    MANAGED_DEVICES_FAMILY,
    ManagedDevicesCoverage,
    RelatedAutopilotState,
    UserManagedDevicesEnrichment,
    UserPointInTimeEnrichment,
)
from diffasaurus.core.entity.pit_presentation import (
    AutopilotPresentationModel,
    AutopilotSourceAudit,
    CardField,
    DeviceFieldGroup,
    ManagedDeviceCardModel,
    ManagedDeviceSourceAudit,
    ManagedDevicesSectionModel,
    ManagedDevicesSourceAudit,
    single_provenance,
    SourceProvenance,
)
from diffasaurus.core.entity.types import SourcedProperty

_MANAGEMENT_PROPERTY_NAMES: tuple[str, ...] = (
    "DeviceName",
    "OperatingSystem",
    "OSVersion",
    "ComplianceState",
    "OwnerType",
    "ManagementAgent",
    "EnrolledDateTime",
    "LastSyncDateTime",
    "DeviceActivityStatus",
    "DaysSinceLastSync",
    "JailBroken",
)

_HARDWARE_PROPERTY_NAMES: tuple[str, ...] = (
    "Manufacturer",
    "Model",
    "SerialNumber",
    "AzureADDeviceId",
    "ManagedDeviceId",
    "PhoneNumber",
)

_AUTOPILOT_PROPERTY_NAMES: tuple[str, ...] = (
    "EnrollmentState",
    "AssignmentStatus",
    "GroupTag",
    "PurchaseOrderIdentifier",
    "LastContactedDateTime",
    "AutopilotObjectId",
    "Manufacturer",
    "Model",
    "SystemFamily",
    "SkuNumber",
    "RecommendedAction",
    "ResourceName",
)

_AUTOPILOT_LABELS: dict[str, str] = {
    "EnrollmentState": "Enrollment state",
    "AssignmentStatus": "Assignment status",
    "GroupTag": "Group tag",
    "PurchaseOrderIdentifier": "Purchase order",
    "LastContactedDateTime": "Last contacted",
    "AutopilotObjectId": "Autopilot object ID",
    "Manufacturer": "Manufacturer",
    "Model": "Model",
    "SystemFamily": "System family",
    "SkuNumber": "SKU number",
    "RecommendedAction": "Recommended action",
    "ResourceName": "Resource name",
}

_MANAGEMENT_LABELS: dict[str, str] = {
    "DeviceName": "Device name",
    "OperatingSystem": "Operating system",
    "OSVersion": "OS version",
    "ComplianceState": "Compliance state",
    "OwnerType": "Owner type",
    "ManagementAgent": "Management agent",
    "EnrolledDateTime": "Enrolled",
    "LastSyncDateTime": "Last sync",
    "DeviceActivityStatus": "Activity status",
    "DaysSinceLastSync": "Days since last sync",
    "JailBroken": "Jail broken",
    "Manufacturer": "Manufacturer",
    "Model": "Model",
    "SerialNumber": "Serial number",
    "AzureADDeviceId": "Azure AD device ID",
    "ManagedDeviceId": "Managed device ID",
    "PhoneNumber": "Phone number",
}


def _property_value(properties: tuple[SourcedProperty, ...], name: str) -> str:
    for prop in properties:
        if prop.name == name:
            return prop.value.strip()
    return ""


def _meaningful(value: str) -> bool:
    text = value.strip().casefold()
    return text and text not in {"unknown", "n/a", "na", "none", "null", "-"}


def _field_from_property(
    prop: SourcedProperty,
    *,
    normalized_key: str,
    label: str,
    provenance: SourceProvenance,
) -> CardField:
    return CardField(
        normalized_key=normalized_key,
        label=label,
        display_value=prop.value.strip(),
        provenance=provenance,
        conflict=None,
    )


def _build_field_groups(
    properties: tuple[SourcedProperty, ...],
    provenance: SourceProvenance,
    names: tuple[str, ...],
    group_id: str,
    title: str,
) -> DeviceFieldGroup | None:
    fields: list[CardField] = []
    prop_by_name = {p.name: p for p in properties}
    for name in names:
        prop = prop_by_name.get(name)
        if prop is None or not _meaningful(prop.value):
            continue
        fields.append(
            _field_from_property(
                prop,
                normalized_key=f"{group_id}:{name}",
                label=_MANAGEMENT_LABELS.get(name, name),
                provenance=provenance,
            )
        )
    if not fields:
        return None
    return DeviceFieldGroup(group_id=group_id, title=title, fields=tuple(fields))


def _os_summary(properties: tuple[SourcedProperty, ...]) -> str:
    os_name = _property_value(properties, "OperatingSystem")
    os_version = _property_value(properties, "OSVersion")
    if os_name and os_version:
        return f"{os_name} {os_version}"
    return os_name or os_version or ""


def _build_autopilot_presentation(
    enriched: EnrichedManagedDevice,
) -> AutopilotPresentationModel | None:
    autopilot = enriched.autopilot
    status = autopilot.status

    if status == "not_applicable":
        return None

    if status == "matched":
        provenance = autopilot.provenance or single_provenance()
        fields: list[CardField] = []
        for name in _AUTOPILOT_PROPERTY_NAMES:
            value = _property_value(autopilot.properties, name)
            if not _meaningful(value):
                continue
            prop = next((p for p in autopilot.properties if p.name == name), None)
            if prop is None:
                continue
            fields.append(
                _field_from_property(
                    prop,
                    normalized_key=f"autopilot:{name}",
                    label=_AUTOPILOT_LABELS.get(name, name),
                    provenance=provenance,
                )
            )
        matching_keys = tuple(
            km.key_kind for km in autopilot.key_matches if km.resolution_status == "unique"
        )
        return AutopilotPresentationModel(
            status=status,
            display_label="Autopilot",
            fields=tuple(fields),
            provenance=provenance,
            diagnostic=autopilot.conflict_diagnostic,
            matching_keys=matching_keys,
            show_warning=False,
            warning_message="",
        )

    warning = ""
    show_warning = False
    if status == "no_match_with_coverage":
        show_warning = True
        warning = "No matching Autopilot record in the historical snapshot."
    elif status == "no_coverage":
        show_warning = True
        warning = "No Autopilot snapshot available at this date."
    elif status == "ambiguous":
        show_warning = True
        warning = "Autopilot association is ambiguous."

    provenance = autopilot.provenance
    matching_keys = tuple(km.key_kind for km in autopilot.key_matches if km.normalized_value)
    return AutopilotPresentationModel(
        status=status,
        display_label="Autopilot",
        fields=(),
        provenance=provenance,
        diagnostic=autopilot.conflict_diagnostic,
        matching_keys=matching_keys,
        show_warning=show_warning,
        warning_message=warning,
    )


def _build_device_card(enriched: EnrichedManagedDevice) -> ManagedDeviceCardModel:
    device = enriched.device
    properties = device.properties
    provenance = device.provenance

    device_name = _property_value(properties, "DeviceName")
    primary = device_name or _property_value(properties, "SerialNumber") or device.dedup_key

    os_summary = _os_summary(properties)
    compliance = _property_value(properties, "ComplianceState")
    owner = _property_value(properties, "OwnerType")
    secondary_parts = [part for part in (os_summary, compliance, owner) if part]
    secondary = " · ".join(secondary_parts)

    manufacturer = _property_value(properties, "Manufacturer")
    model_name = _property_value(properties, "Model")
    tertiary = " · ".join(part for part in (manufacturer, model_name) if part)

    groups: list[DeviceFieldGroup] = []
    management = _build_field_groups(
        properties,
        provenance,
        _MANAGEMENT_PROPERTY_NAMES,
        "management",
        "Management and compliance",
    )
    if management is not None:
        groups.append(management)
    hardware = _build_field_groups(
        properties,
        provenance,
        _HARDWARE_PROPERTY_NAMES,
        "hardware",
        "Hardware and identity",
    )
    if hardware is not None:
        groups.append(hardware)

    filter_parts = [
        primary,
        os_summary,
        compliance,
        owner,
        manufacturer,
        model_name,
        _property_value(properties, "SerialNumber"),
    ]
    filter_blob = " ".join(part.casefold() for part in filter_parts if part)

    return ManagedDeviceCardModel(
        stable_key=device.dedup_key,
        primary_label=primary,
        secondary_label=secondary,
        tertiary_label=tertiary,
        operating_system=_property_value(properties, "OperatingSystem"),
        compliance_label=compliance,
        ownership_label=owner,
        management_groups=tuple(groups),
        provenance=provenance,
        autopilot=_build_autopilot_presentation(enriched),
        filter_blob=filter_blob,
    )


def _coverage_message(
    coverage: ManagedDevicesCoverage,
    unresolved_count: int,
) -> tuple[str, str | None]:
    if coverage == "populated":
        warning = None
        if unresolved_count:
            warning = (
                f"{unresolved_count} device association"
                f"{'s' if unresolved_count != 1 else ''} could not be resolved."
            )
        return "", warning
    if coverage == "known_zero":
        return "The historical snapshot confirmed no associated managed devices.", None
    if coverage == "no_coverage":
        return "No managed-device snapshot available.", None
    if coverage == "ambiguous_association":
        return "Managed-device associations are ambiguous for this snapshot.", None
    if coverage == "unknown":
        return "Managed-device association unavailable for this snapshot.", None
    return "", None


def _enriched_devices(enrichment: UserManagedDevicesEnrichment) -> tuple[EnrichedManagedDevice, ...]:
    if enrichment.enriched_devices:
        return enrichment.enriched_devices
    return tuple(
        EnrichedManagedDevice(
            device=device,
            autopilot=RelatedAutopilotState(
                status="no_coverage",
                properties=(),
                provenance=None,
                key_matches=(),
                matched_row_index=None,
                conflict_diagnostic="",
            ),
        )
        for device in enrichment.devices
    )


def build_managed_devices_section(
    enrichment: UserManagedDevicesEnrichment | None,
    *,
    enrichment_error: str | None = None,
) -> ManagedDevicesSectionModel | None:
    if enrichment_error:
        return ManagedDevicesSectionModel(
            coverage="enrichment_error",
            device_count=0,
            devices=(),
            message="Managed-device enrichment unavailable.",
            warning_message=None,
            enrichment_error=enrichment_error,
            unresolved_count=0,
            snapshot_at=None,
            source_relative_path="",
            family_coverage_status=None,
        )

    if enrichment is None:
        return None

    unresolved_count = sum(
        1
        for obs in enrichment.unresolved_observations
        if obs.resolution_status != "resolved"
    )
    message, warning = _coverage_message(enrichment.coverage, unresolved_count)

    devices = tuple(
        sorted(
            (_build_device_card(item) for item in _enriched_devices(enrichment)),
            key=lambda card: (card.primary_label.casefold(), card.stable_key),
        )
    )

    family_status = None
    if enrichment.family_coverage is not None:
        family_status = enrichment.family_coverage.status

    return ManagedDevicesSectionModel(
        coverage=enrichment.coverage,
        device_count=len(enrichment.devices),
        devices=devices,
        message=message,
        warning_message=warning,
        enrichment_error=None,
        unresolved_count=unresolved_count,
        snapshot_at=enrichment.snapshot_at,
        source_relative_path=enrichment.source_relative_path,
        family_coverage_status=family_status,
    )


def build_managed_devices_source_audit(
    enrichment: UserManagedDevicesEnrichment | None,
    *,
    enrichment_error: str | None = None,
) -> ManagedDevicesSourceAudit | None:
    if enrichment is None and not enrichment_error:
        return None

    if enrichment is None:
        return ManagedDevicesSourceAudit(
            coverage_status="enrichment_error",
            snapshot_at=None,
            source_relative_path="",
            source_family=MANAGED_DEVICES_FAMILY,
            resolved_device_count=0,
            unresolved_observation_count=0,
            enrichment_error=enrichment_error,
            autopilot_snapshot_at=None,
            autopilot_source_relative_path="",
            autopilot_coverage_status=None,
            devices=(),
        )

    device_audits: list[ManagedDeviceSourceAudit] = []
    enriched_by_dedup = {item.device.dedup_key: item for item in _enriched_devices(enrichment)}

    for device in enrichment.devices:
        enriched = enriched_by_dedup[device.dedup_key]
        ap = enriched.autopilot
        autopilot_audit = AutopilotSourceAudit(
            status=ap.status,
            snapshot_at=(
                ap.provenance.observations[0].snapshot_at
                if ap.provenance and ap.provenance.observations
                else enrichment.autopilot_family_coverage.snapshot_at
                if enrichment.autopilot_family_coverage
                else None
            ),
            source_relative_path=(
                enrichment.autopilot_family_coverage.source_relative_path
                if enrichment.autopilot_family_coverage
                else ""
            ),
            matching_keys=tuple(km.key_kind for km in ap.key_matches if km.normalized_value),
            normalized_values={
                km.key_kind: km.normalized_value for km in ap.key_matches if km.normalized_value
            },
            diagnostic=ap.conflict_diagnostic,
            candidate_counts={
                km.key_kind: len(km.candidate_row_indices) for km in ap.key_matches
            },
            properties=tuple(ap.properties),
            provenance_observations=(
                ap.provenance.observations if ap.provenance else ()
            ),
        )

        device_audits.append(
            ManagedDeviceSourceAudit(
                stable_key=device.dedup_key,
                link_kind=device.link_kind,
                resolution_status=device.resolution_status,
                normalized_link_value=device.normalized_link_value,
                diagnostic=device.diagnostic,
                candidate_user_ids=tuple(sorted(device.candidate_user_ids)),
                managed_provenance_observations=device.provenance.observations,
                properties=tuple(device.properties),
                autopilot=autopilot_audit,
            )
        )

    ap_cov = enrichment.autopilot_family_coverage
    return ManagedDevicesSourceAudit(
        coverage_status=enrichment.coverage,
        snapshot_at=enrichment.snapshot_at,
        source_relative_path=enrichment.source_relative_path,
        source_family=MANAGED_DEVICES_FAMILY,
        resolved_device_count=len(enrichment.devices),
        unresolved_observation_count=len(enrichment.unresolved_observations),
        enrichment_error=enrichment_error,
        autopilot_snapshot_at=ap_cov.snapshot_at if ap_cov else None,
        autopilot_source_relative_path=ap_cov.source_relative_path if ap_cov else "",
        autopilot_coverage_status=ap_cov.status if ap_cov else None,
        devices=tuple(device_audits),
    )


def build_managed_devices_from_point_in_time_enrichment(
    point_in_time: UserPointInTimeEnrichment | None,
    *,
    enrichment_error: str | None = None,
) -> tuple[ManagedDevicesSectionModel | None, ManagedDevicesSourceAudit | None]:
    enrichment = point_in_time.managed_devices if point_in_time else None
    section = build_managed_devices_section(enrichment, enrichment_error=enrichment_error)
    audit = build_managed_devices_source_audit(enrichment, enrichment_error=enrichment_error)
    return section, audit
