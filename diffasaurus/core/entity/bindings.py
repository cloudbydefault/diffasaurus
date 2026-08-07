from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import DefaultDict, Literal

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
        self._by_alias: DefaultDict[tuple[str, str], list[AliasObservation]] = DefaultDict(list)
        self._by_immutable: DefaultDict[str, list[AliasObservation]] = DefaultDict(list)
        self._alias_times: DefaultDict[tuple[str, str], list[datetime]] = DefaultDict(list)
        self._immutable_times: DefaultDict[str, list[datetime]] = DefaultDict(list)

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
        observation = AliasObservation(
            kind=kind,
            normalized_value=normalized_value,
            observed_at=observed_at,
            immutable_id=immutable_id,
            source_family=source_family,
        )
        alias_key = (kind, normalized_value)
        self._by_alias[alias_key].append(observation)
        self._alias_times[alias_key].append(observed_at)
        self._by_immutable[immutable_id].append(observation)
        self._immutable_times[immutable_id].append(observed_at)

    def _ensure_sorted(
        self,
        times: list[datetime],
        observations: list[AliasObservation],
    ) -> None:
        if len(times) <= 1:
            return
        for index in range(len(times) - 1):
            if times[index] > times[index + 1]:
                pairs = sorted(zip(times, observations), key=lambda pair: pair[0])
                times[:] = [pair[0] for pair in pairs]
                observations[:] = [pair[1] for pair in pairs]
                return

    def resolve(self, kind: str, normalized_value: str, as_of: datetime) -> ResolvedAlias:
        alias_key = (kind, normalized_value)
        observations = self._by_alias.get(alias_key)
        if not observations:
            return ResolvedAlias(status="unbound")
        times = self._alias_times[alias_key]
        self._ensure_sorted(times, observations)
        end = bisect_right(times, as_of)
        if end == 0:
            return ResolvedAlias(status="unbound")
        candidates = observations[:end]
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
        observations = self._by_immutable.get(immutable_id)
        if not observations:
            return set()
        times = self._immutable_times[immutable_id]
        self._ensure_sorted(times, observations)
        end = bisect_right(times, as_of)
        if end == 0:
            return set()
        return {
            observation.normalized_value
            for observation in observations[:end]
        }
