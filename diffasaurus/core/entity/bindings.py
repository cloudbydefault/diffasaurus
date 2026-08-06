from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ResolvedAliasStatus = Literal["bound", "unbound", "ambiguous"]


@dataclass(frozen=True)
class ResolvedAlias:
    status: ResolvedAliasStatus
    immutable_id: str = ""
    candidates: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AliasObservation:
    kind: str
    normalized_value: str
    observed_at: datetime
    immutable_id: str
    source_family: str


class AliasBindingIndex:
    def __init__(self) -> None:
        self._observations: list[AliasObservation] = []

    def record(
        self,
        kind: str,
        normalized_value: str,
        observed_at: datetime,
        immutable_id: str,
        source_family: str,
    ) -> None:
        if not normalized_value or not immutable_id:
            return
        self._observations.append(
            AliasObservation(
                kind=kind,
                normalized_value=normalized_value,
                observed_at=observed_at,
                immutable_id=immutable_id,
                source_family=source_family,
            )
        )

    def resolve(self, kind: str, normalized_value: str, as_of: datetime) -> ResolvedAlias:
        candidates = [
            observation
            for observation in self._observations
            if observation.kind == kind
            and observation.normalized_value == normalized_value
            and observation.observed_at <= as_of
        ]
        if not candidates:
            return ResolvedAlias(status="unbound")
        newest_at = max(observation.observed_at for observation in candidates)
        at_newest = [
            observation for observation in candidates if observation.observed_at == newest_at
        ]
        immutable_ids = {observation.immutable_id for observation in at_newest}
        if len(immutable_ids) > 1:
            return ResolvedAlias(status="ambiguous", candidates=frozenset(immutable_ids))
        return ResolvedAlias(status="bound", immutable_id=at_newest[0].immutable_id)

    def values_for_immutable_id(self, immutable_id: str, as_of: datetime) -> set[str]:
        """Normalized alias values observed for an immutable id at or before as_of."""
        return {
            observation.normalized_value
            for observation in self._observations
            if observation.immutable_id == immutable_id and observation.observed_at <= as_of
        }
