from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

from filelock import FileLock, Timeout

from vidxp.settings import VidXPSettings
from vidxp.workflow_runtime import (
    LOCAL_WORKER_SETTINGS_ENV,
    local_executor_id,
    workflow_database_url,
    workflow_application_version,
)


class LocalWorkerSupervisor:
    """Start one detached repository-scoped worker without owning job state."""

    def __init__(self, settings: VidXPSettings) -> None:
        self.settings = settings
        self.layout = settings.layout

    def ensure_running(self) -> None:
        self.layout.ensure_local_directories()
        version = workflow_application_version()
        worker_lock_path = (
            self.layout.local_workflows / f"worker-{version}.lock"
        )
        ready_path = (
            self.layout.local_workflows / f"worker-{version}.ready"
        )
        start_lock = FileLock(
            self.layout.local_workflows / f"worker-{version}-start.lock"
        )
        with start_lock:
            worker_lock = FileLock(worker_lock_path)
            if self._wait_for_existing_worker(worker_lock, ready_path):
                return
            ready_path.unlink(missing_ok=True)

            command = [
                sys.executable,
                "-m",
                "vidxp.workflow_worker",
                "--database-url",
                workflow_database_url(self.settings),
                "--executor-id",
                local_executor_id(self.settings),
                "--role",
                "all",
                "--lock-file",
                str(worker_lock_path.resolve()),
                "--ready-file",
                str(ready_path.resolve()),
            ]
            environment = os.environ.copy()
            environment[LOCAL_WORKER_SETTINGS_ENV] = (
                self.settings.model_dump_json()
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
                    | subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NO_WINDOW
                )
            else:
                options["start_new_session"] = True
            process = subprocess.Popen(command, **options)
            self._wait_for_worker(process, worker_lock, ready_path)

    @staticmethod
    def _wait_for_existing_worker(
        worker_lock: FileLock,
        ready_path: Path,
        *,
        timeout_seconds: float = 5,
    ) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            try:
                worker_lock.acquire(timeout=0)
            except Timeout:
                if ready_path.is_file():
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
        timeout_seconds: float = 5,
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
                    return
            else:
                worker_lock.release()
            sleep(0.05)
        raise RuntimeError(
            "The local background worker did not become ready in time."
        )
