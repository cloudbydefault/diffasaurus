from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SyncPhase = Literal[
    "discovering",
    "checking",
    "repairing_projections",
    "building_user_device_links",
    "indexing",
    "resolving_identities",
    "recomputing_entities",
    "checkpointing",
    "publishing",
    "finalizing",
    "complete",
    "completed_with_errors",
    "failed",
]


@dataclass(frozen=True)
class SyncProgressEvent:
    phase: SyncPhase
    generation: int = 0
    task_id: str = "entity_sync"
    discovered: int = 0
    total: int = 0
    parsed: int = 0
    reused: int = 0
    failed: int = 0
    unresolved: int = 0
    elapsed_ms: int = 0
    eta_ms: int | None = None
    label: str = ""


@dataclass(frozen=True)
class SyncCompleteEvent:
    generation: int
    status: Literal["complete", "completed_with_errors", "failed", "interrupted"]
    task_id: str = "entity_sync"
    discovered: int = 0
    parsed: int = 0
    reused: int = 0
    failed: int = 0
    unresolved: int = 0
    elapsed_ms: int = 0
    message: str = ""
