from __future__ import annotations

PIT_COLORS = {
    "surface2": "#152331",
    "text": "#f2f7fb",
    "muted": "#8295a8",
    "teal": "#8bd450",
    "green": "#4fd1a5",
    "red": "#fb7185",
    "amber": "#f5b942",
}

COLLECTION_PREVIEW_COUNT = 8
COLLECTION_FILTER_THRESHOLD = 12


def format_gap(gap) -> str:
    if gap is None:
        return "—"
    total_seconds = int(gap.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


def format_collection_row_label(primary_label: str, secondary_label: str) -> str:
    if secondary_label:
        return f"{primary_label} — {secondary_label}"
    return primary_label


def format_provenance_tooltip(observations) -> str:
    lines: list[str] = []
    for obs in observations:
        observed = obs.observed_at.strftime("%d %b %Y · %H:%M") if obs.observed_at else "—"
        snapshot = obs.snapshot_at.strftime("%d %b %Y · %H:%M") if obs.snapshot_at else "—"
        gap = format_gap(obs.gap)
        lines.append(f"{obs.family}\n  Observed: {observed}\n  Snapshot: {snapshot}\n  Gap: {gap}")
    return "\n\n".join(lines)
