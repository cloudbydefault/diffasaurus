from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ProgressUpdate:
    task_id: str
    generation: int
    current: int
    total: int
    label: str


class ProgressCoordinator:
    def __init__(self) -> None:
        self._generations: dict[str, int] = {}
        self._active_tasks: set[str] = set()
        self._foreground_task_id: str | None = None
        self._global_handler: Callable[[int, int, str], None] | None = None
        self._entity_handler: Callable[[str], None] | None = None

    def set_global_handler(
        self,
        handler: Callable[[int, int, str], None] | None,
    ) -> None:
        self._global_handler = handler

    def set_entity_handler(self, handler: Callable[[str], None] | None) -> None:
        self._entity_handler = handler

    def start_task(self, task_id: str, generation: int, *, foreground: bool = False) -> None:
        self._generations[task_id] = generation
        self._active_tasks.add(task_id)
        if foreground or self._foreground_task_id is None:
            self._foreground_task_id = task_id

    def report_progress(
        self,
        task_id: str,
        generation: int,
        current: int,
        total: int,
        label: str,
    ) -> None:
        if self._generations.get(task_id) != generation:
            return
        if task_id not in self._active_tasks:
            return
        if task_id == self._foreground_task_id and self._global_handler is not None:
            self._global_handler(current, total, label)
        if task_id == "entity_sync" and self._entity_handler is not None:
            self._entity_handler(label)

    def report_entity_detail(self, detail: str) -> None:
        if self._entity_handler is not None and "entity_sync" in self._active_tasks:
            self._entity_handler(detail)

    def finish_task(self, task_id: str, generation: int) -> None:
        if self._generations.get(task_id) != generation:
            return
        self._active_tasks.discard(task_id)
        if self._foreground_task_id == task_id:
            self._foreground_task_id = next(iter(self._active_tasks), None)
            if self._foreground_task_id is None and self._global_handler is not None:
                self._global_handler(0, 1, "")

    def bump_generation(self, task_id: str) -> int:
        generation = self._generations.get(task_id, 0) + 1
        self._generations[task_id] = generation
        self._active_tasks.discard(task_id)
        if self._foreground_task_id == task_id:
            self._foreground_task_id = next(iter(self._active_tasks), None)
        return generation
