from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from dataclasses import asdict

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from diffasaurus.core.entity.index_lock import (
    check_lock_holder,
    lock_unavailable_message,
)
from diffasaurus.core.entity.index_paths import (
    entity_index_path,
    normalize_reports_path,
    source_key,
)
from diffasaurus.core.entity.index_worker_launch import (
    worker_dispatch_flag,
    worker_program,
    worker_script_argument,
)

logger = logging.getLogger(__name__)


class EntityIndexController(QObject):
    progress = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._generation = 0
        self._sync_state = "idle"
        self._queued = False
        self._reports_dir: Path | None = None
        self._repository = None
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._complete_received = False
        self._failure_emitted = False
        self._last_db_path: Path | None = None
        self._last_source_key: str = ""

    @property
    def repository(self):
        return self._repository

    @property
    def sync_state(self) -> str:
        return self._sync_state

    @property
    def generation(self) -> int:
        return self._generation

    def open_existing(self, reports_dir: Path):
        from diffasaurus.core.entity.index_projection import (
            ensure_search_projections,
            user_device_links_need_build_at_path,
        )
        from diffasaurus.core.entity.index_repository import EntityIndexRepository

        normalized = normalize_reports_path(reports_dir)
        self._reports_dir = normalized
        db_path = entity_index_path(normalized)
        key = source_key(normalized)
        logger.info(
            "Entity index open_existing: reports_dir=%s source_key=%s db_path=%s exists=%s",
            normalized,
            key,
            db_path,
            db_path.is_file(),
        )
        repair_stats = None
        needs_user_device_links = False
        if db_path.is_file():
            repair_stats = ensure_search_projections(
                normalized,
                db_path=db_path,
                generation=self._generation,
                progress=lambda event: self.progress.emit(
                    {"type": "progress", **asdict(event), "task_id": "entity_sync"}
                ),
            )
            if repair_stats is not None:
                logger.info(
                    "Entity search projection repair on open: entities=%d aliases_before=%d "
                    "aliases_after=%d fts_rows_rebuilt=%d duration_ms=%d "
                    "alias_projection_version=%d search_projection_version=%d",
                    repair_stats.entities_processed,
                    repair_stats.aliases_before,
                    repair_stats.aliases_after,
                    repair_stats.fts_rows_rebuilt,
                    repair_stats.duration_ms,
                    repair_stats.alias_projection_version,
                    repair_stats.search_projection_version,
                )
            # User-device link projection is worker-only — never rebuild CSVs here.
            needs_user_device_links = user_device_links_need_build_at_path(db_path)
        if self._repository is not None:
            self._repository.close()
        self._repository = EntityIndexRepository.open(normalized, db_path=db_path)
        if repair_stats is not None and self._repository is not None:
            self._repository.invalidate_caches()
        self._last_db_path = db_path
        self._last_source_key = key
        if self._repository is not None:
            logger.info("Entity index repository opened for source_key=%s", key)
        else:
            logger.info("Entity index repository unavailable for source_key=%s", key)
        if needs_user_device_links and self._sync_state != "running":
            logger.info(
                "Queueing entity index sync for user-device link projection repair "
                "(source_key=%s)",
                key,
            )
            self.start_sync(normalized)
        return self._repository
    def close_repository(self) -> None:
        if self._repository is not None:
            self._repository.close()
            self._repository = None

    def start_sync(self, reports_dir: Path, *, force: bool = False, cold: bool = False) -> None:
        normalized = normalize_reports_path(reports_dir)
        self._reports_dir = normalized
        db_path = entity_index_path(normalized)
        key = source_key(normalized)
        needs_cold = cold or not db_path.is_file()

        if self._sync_state == "running":
            if force:
                logger.info(
                    "Cancelling in-flight entity index sync (generation=%d) for forced restart",
                    self._generation,
                )
                self.cancel()
            else:
                logger.info(
                    "Entity index sync already running (generation=%d); request queued",
                    self._generation,
                )
                self._queued = True
                return

        holder = check_lock_holder(db_path)
        if holder is not None:
            message = lock_unavailable_message(db_path, holder)
            logger.warning("Entity index sync blocked by active lock: %s", message)
            self.failed.emit(message)
            return

        self._generation += 1
        generation = self._generation
        self._sync_state = "running"
        self._complete_received = False
        self._failure_emitted = False
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._last_db_path = db_path
        self._last_source_key = key

        arguments: list[str] = []
        script = worker_script_argument()
        if script is not None:
            arguments.append(script)
        arguments.extend(
            [
                worker_dispatch_flag(),
                "--reports-dir",
                str(normalized),
                "--source-key",
                key,
                "--generation",
                str(generation),
                "--task-id",
                "entity_sync",
                "--db-path",
                str(db_path),
            ]
        )
        if needs_cold:
            arguments.append("--cold")

        program = worker_program()
        logger.info(
            "Starting entity index worker generation=%d cold=%s reports_dir=%s "
            "source_key=%s db_path=%s exists=%s",
            generation,
            needs_cold,
            normalized,
            key,
            db_path,
            db_path.is_file(),
        )
        logger.info("Entity index worker command: %s %s", program, " ".join(arguments))

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.setWorkingDirectory(str(normalized))
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.errorOccurred.connect(
            lambda error, gen=generation: self._on_process_error(gen, error)
        )
        self._process.finished.connect(
            lambda exit_code, exit_status, gen=generation: self._on_process_finished(
                gen,
                exit_code,
                exit_status,
            )
        )
        self._process.start(program, arguments)
        if self._process.state() == QProcess.ProcessState.Starting:
            logger.info("Entity index worker process starting")
        elif not self._process.waitForStarted(3000):
            message = self._process.errorString() or "Entity index worker failed to start"
            logger.error("Entity index worker failed to start: %s", message)
            self._sync_state = "idle"
            self._process = None
            self._emit_failure(message)
            return
        logger.info(
            "Entity index worker started pid=%s state=%s",
            self._process.processId(),
            self._process.state(),
        )

    def cancel(self) -> None:
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self._generation += 1
        self._queued = False
        if self._process is None:
            return
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(timeout_ms):
                self._process.kill()
                self._process.waitForFinished(500)
        self._process = None
        self._sync_state = "idle"

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._stdout_buffer += data
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Entity index worker malformed stdout line: %s", line)
                continue
            if payload.get("type") == "progress":
                self.progress.emit(payload)
            elif payload.get("type") == "complete":
                self._complete_received = True
                if payload.get("status") == "failed":
                    self._emit_failure(self._format_failure_message(payload))
                else:
                    self.finished.emit(payload)

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        data = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        if data.strip():
            logger.info("Entity index worker stderr: %s", data.strip())
        self._stderr_buffer += data

    def _format_failure_message(self, payload: dict | None = None) -> str:
        parts: list[str] = []
        if payload is not None:
            message = str(payload.get("message", "")).strip()
            if message:
                parts.append(message)
        stderr = self._stderr_buffer.strip()
        if stderr:
            tail = "\n".join(stderr.splitlines()[-8:])
            parts.append(f"Worker diagnostics:\n{tail}")
        if not parts:
            return "Entity index synchronization failed."
        return "\n\n".join(parts)

    def _emit_failure(self, message: str) -> None:
        self._failure_emitted = True
        self._sync_state = "idle"
        self.progress.emit(
            {
                "type": "progress",
                "phase": "failed",
                "generation": self._generation,
                "label": message,
            }
        )
        self.failed.emit(message)

    def _on_process_error(self, generation: int, error: QProcess.ProcessError) -> None:
        if generation != self._generation:
            return
        message = self._process.errorString() if self._process is not None else ""
        detail = message or f"Entity index worker process error: {error.name}"
        logger.error(
            "Entity index worker error generation=%d: %s (%s)",
            generation,
            detail,
            error.name,
        )
        self._process = None
        self._emit_failure(detail)

    def _on_process_finished(
        self,
        generation: int,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        trailing = self._stdout_buffer.strip()
        if trailing:
            logger.warning("Entity index worker trailing stdout: %s", trailing)
            self._stdout_buffer = ""
        self._sync_state = "idle"
        self._process = None
        if generation != self._generation:
            logger.info(
                "Ignoring entity index worker completion for stale generation=%d (current=%d)",
                generation,
                self._generation,
            )
            return
        if self._failure_emitted:
            return

        logger.info(
            "Entity index worker finished generation=%d exit_code=%d exit_status=%s complete=%s",
            generation,
            exit_code,
            exit_status.name,
            self._complete_received,
        )

        if exit_status == QProcess.ExitStatus.CrashExit:
            self._emit_failure(self._format_failure_message())
            return
        if not self._complete_received:
            detail = self._format_failure_message()
            if exit_code != 0:
                detail = f"Entity index worker exited with code {exit_code}.\n\n{detail}"
            else:
                detail = (
                    "Entity index worker finished without a completion event.\n\n" + detail
                )
            self._emit_failure(detail)
            return
        if exit_code != 0:
            self._emit_failure(
                f"Entity index worker exited with code {exit_code}.\n\n"
                f"{self._format_failure_message()}"
            )
            return

        if self._reports_dir is not None:
            repository = self.open_existing(self._reports_dir)
            if repository is None and self._last_db_path is not None:
                logger.error(
                    "Entity index worker completed but database is missing: %s",
                    self._last_db_path,
                )
                self._emit_failure(
                    f"Entity index build completed but database was not published at "
                    f"{self._last_db_path}"
                )
                return
            if self._last_db_path is not None and self._last_db_path.is_file():
                logger.info(
                    "Entity index publication verified: %s size=%s bytes",
                    self._last_db_path,
                    self._last_db_path.stat().st_size,
                )

        if self._queued:
            self._queued = False
            logger.info("Starting queued entity index sync")
            self.start_sync(self._reports_dir, force=True)
