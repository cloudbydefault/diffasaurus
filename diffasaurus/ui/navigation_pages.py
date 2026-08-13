"""Named main-window page indices for Diffasaurus navigation."""

from __future__ import annotations

PAGE_RECENT_CHANGES = 0
PAGE_ENTITY_HISTORY = 1
PAGE_POINT_IN_TIME = 2
PAGE_DIG_SITE = 3
PAGE_RUN_HEALTH = 4
PAGE_FOSSIL_LIBRARY = 5
PAGE_COMPARE = 6
PAGE_SNAPSHOT_EXPLORER = 7
PAGE_CONFIGURATION_POLICIES = 8

PAGE_COUNT = 9

PAGE_TITLES: tuple[tuple[str, str], ...] = (
    (
        "Recent changes",
        "See what changed across every supported report since your last collections.",
    ),
    (
        "Entity history",
        "Trace one user, device, or shared mailbox across every snapshot that knows about it.",
    ),
    (
        "Point-in-Time",
        "Reconstruct what was known about an entity at a selected date.",
    ),
    ("The dig site", "Unearthing your Microsoft 365 history, one CSV fossil at a time."),
    ("Scheduled run health", "See which weekday collections produced evidence—and which outputs are missing."),
    ("Fossil library", "Browse the CSV snapshots buried in your tenant timeline."),
    ("Compare snapshots", "Explain exactly what appeared, disappeared, or changed."),
    (
        "Snapshot explorer",
        "Inspect raw tenant data, combine filters, and open report-aware dashboards.",
    ),
    (
        "Configuration policies",
        "Explore Intune policy settings, assignments, coverage, and historical changes.",
    ),
)
