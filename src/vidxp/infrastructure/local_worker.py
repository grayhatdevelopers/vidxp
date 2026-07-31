from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from secrets import token_hex
from threading import Lock
from time import monotonic, sleep

from filelock import FileLock, Timeout
from pydantic import ValidationError

from vidxp.core.manifest import write_json_atomic
from vidxp.infrastructure.local_files import durable_unlink
from vidxp.settings import VidXPSettings
from vidxp.workflow_runtime import (
    LOCAL_WORKER_BOOTSTRAP_ENV,
    LocalWorkerBootstrap,
    LocalWorkerReady,
    LocalWorkerStopRequest,
    local_worker_bootstrap,
    local_executor_id,
    workflow_application_version,
)


class LocalWorkerSupervisor:
    """Start one detached repository-scoped worker without owning job state."""

    _retry_delay_seconds = 5.0
    _startup_timeout_seconds = 15.0

    def __init__(self, settings: VidXPSettings) -> None:
        self.settings = settings
        self.layout = settings.layout
        self._startup_lock = Lock()
        self._retry_after = 0.0

    def ensure_running(self) -> None:
        with self._startup_lock:
            if monotonic() < self._retry_after:
                raise RuntimeError(
                    "The local background worker is in startup backoff."
                )
            try:
                self._ensure_running()
            except Exception:
                self._retry_after = (
                    monotonic() + self._retry_delay_seconds
                )
                raise
            self._retry_after = 0.0

    def _ensure_running(self) -> None:
        self.layout.ensure_local_directories()
        version = workflow_application_version()
        bootstrap = local_worker_bootstrap(self.settings)
        worker_lock_path = (
            self.layout.local_workflows / f"worker-{version}.lock"
        )
        ready_path = (
            self.layout.local_workflows / f"worker-{version}.ready"
        )
        stop_path = (
            self.layout.local_workflows / f"worker-{version}.stop"
        )
        start_lock = FileLock(
            self.layout.local_workflows / f"worker-{version}-start.lock"
        )
        with start_lock:
            self._remove_stale_bootstraps(version)
            worker_lock = FileLock(worker_lock_path)
            if self._wait_for_existing_worker(
                worker_lock,
                ready_path,
                fingerprint=bootstrap.fingerprint,
            ):
                return
            durable_unlink(ready_path, missing_ok=True)
            durable_unlink(stop_path, missing_ok=True)

            command = [
                sys.executable,
                "-m",
                "vidxp.workflow_worker",
                "--executor-id",
                local_executor_id(self.settings),
                "--role",
                "all",
                "--lock-file",
                str(worker_lock_path.resolve()),
                "--ready-file",
                str(ready_path.resolve()),
                "--stop-file",
                str(stop_path.resolve()),
                "--log-file",
                str(self._worker_log(version).resolve()),
            ]
            bootstrap_path = self._write_bootstrap(bootstrap)
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.upper().startswith(("VIDXP_", "DBOS_"))
            }
            environment[LOCAL_WORKER_BOOTSTRAP_ENV] = str(
                bootstrap_path.resolve()
            )
            options: dict = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
                "env": environment,
            }
            if sys.platform == "win32":
                options["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )
            else:
                options["start_new_session"] = True
            process = None
            try:
                process = subprocess.Popen(
                    command,
                    **options,
                )
                self._wait_for_worker(
                    process,
                    worker_lock,
                    ready_path,
                    fingerprint=bootstrap.fingerprint,
                    timeout_seconds=self._startup_timeout_seconds,
                )
            except Exception:
                if process is not None:
                    self._terminate(process)
                raise
            finally:
                durable_unlink(bootstrap_path, missing_ok=True)

    def _worker_log(self, version: str) -> Path:
        return self.layout.local_workflows / f"worker-{version}.log"

    def _write_bootstrap(
        self,
        bootstrap: LocalWorkerBootstrap,
    ) -> Path:
        path = (
            self.layout.local_workflows
            / (
                f".worker-{workflow_application_version()}-"
                f"bootstrap-{token_hex(16)}.json"
            )
        )
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                destination.write(bootstrap.model_dump_json())
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            durable_unlink(path, missing_ok=True)
            raise
        return path

    def _remove_stale_bootstraps(self, version: str) -> None:
        pattern = f".worker-{version}-bootstrap-*.json"
        for path in self.layout.local_workflows.glob(pattern):
            if path.is_file():
                durable_unlink(path)

    def health(self) -> None:
        version = workflow_application_version()
        worker_lock = FileLock(
            self.layout.local_workflows / f"worker-{version}.lock"
        )
        ready_path = (
            self.layout.local_workflows / f"worker-{version}.ready"
        )
        fingerprint = local_worker_bootstrap(self.settings).fingerprint
        if not self._wait_for_existing_worker(
            worker_lock,
            ready_path,
            fingerprint=fingerprint,
            timeout_seconds=0.1,
        ):
            raise RuntimeError("The local background worker is not running.")

    def stop(self) -> bool:
        version = workflow_application_version()
        worker_lock = FileLock(
            self.layout.local_workflows / f"worker-{version}.lock"
        )
        ready_path = (
            self.layout.local_workflows / f"worker-{version}.ready"
        )
        stop_path = (
            self.layout.local_workflows / f"worker-{version}.stop"
        )
        try:
            worker_lock.acquire(timeout=0)
        except Timeout:
            ready = self._load_ready(ready_path)
            if ready.application_version != version:
                raise RuntimeError(
                    "The running local worker has an invalid version identity."
                )
            write_json_atomic(
                stop_path,
                LocalWorkerStopRequest(
                    pid=ready.pid,
                    application_version=version,
                ).model_dump(mode="json"),
            )
            deadline = monotonic() + 35
            while monotonic() < deadline:
                try:
                    worker_lock.acquire(timeout=0)
                except Timeout:
                    sleep(0.05)
                    continue
                else:
                    worker_lock.release()
                    durable_unlink(ready_path, missing_ok=True)
                    durable_unlink(stop_path, missing_ok=True)
                    return True
            raise RuntimeError(
                "The local background worker did not stop in time."
            )
        else:
            worker_lock.release()
            durable_unlink(ready_path, missing_ok=True)
            durable_unlink(stop_path, missing_ok=True)
            return False

    @staticmethod
    def _wait_for_existing_worker(
        worker_lock: FileLock,
        ready_path: Path,
        *,
        fingerprint: str,
        timeout_seconds: float = _startup_timeout_seconds,
    ) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            try:
                worker_lock.acquire(timeout=0)
            except Timeout:
                if ready_path.is_file():
                    LocalWorkerSupervisor._validate_ready(
                        ready_path,
                        fingerprint,
                    )
                    return True
                sleep(0.05)
                continue
            else:
                worker_lock.release()
                return False
        raise RuntimeError(
            "The existing local background worker did not become ready."
        )

    @staticmethod
    def _wait_for_worker(
        process: subprocess.Popen,
        worker_lock: FileLock,
        ready_path: Path,
        *,
        fingerprint: str,
        timeout_seconds: float = _startup_timeout_seconds,
    ) -> None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "The local background worker exited during startup."
                )
            try:
                worker_lock.acquire(timeout=0)
            except Timeout:
                if ready_path.is_file():
                    LocalWorkerSupervisor._validate_ready(
                        ready_path,
                        fingerprint,
                    )
                    return
            else:
                worker_lock.release()
            sleep(0.05)
        raise RuntimeError(
            "The local background worker did not become ready in time."
        )

    @staticmethod
    def _validate_ready(ready_path: Path, fingerprint: str) -> None:
        ready = LocalWorkerSupervisor._load_ready(ready_path)
        if ready.fingerprint != fingerprint:
            raise RuntimeError(
                "The running local worker uses different execution settings "
                f"(PID {ready.pid}); run `vidxp jobs stop-worker` before "
                "applying the new configuration."
            )

    @staticmethod
    def _load_ready(ready_path: Path) -> LocalWorkerReady:
        try:
            return LocalWorkerReady.model_validate_json(
                ready_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise RuntimeError(
                "The local background worker published invalid readiness."
            ) from exc

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
