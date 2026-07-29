from __future__ import annotations

import argparse
import logging
import os
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event
from typing import Callable, Sequence

from dbos import DBOS, DBOSConfig
from filelock import FileLock

from vidxp.application_models import JobQueue
from vidxp.composition import create_application
from vidxp.core.manifest import write_json_atomic
from vidxp.infrastructure.dbos_workflows import VidXPWorkerWorkflows
from vidxp.infrastructure.local_files import durable_unlink
from vidxp.settings import VidXPSettings
from vidxp.workflow_contracts import APPLICATION_NAME, QUEUE_NAMES
from vidxp.workflow_runtime import (
    LOCAL_WORKER_BOOTSTRAP_ENV,
    LocalWorkerBootstrap,
    LocalWorkerReady,
    LocalWorkerStopRequest,
    local_worker_bootstrap,
    server_executor_id,
    workflow_application_version,
    workflow_database_url,
)

LOGGER = logging.getLogger(__name__)
_MAXIMUM_LOG_BYTES = 5 * 1024 * 1024


def _arguments(values: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VidXP DBOS workflows.")
    parser.add_argument("--executor-id")
    parser.add_argument("--role", choices=("all", "cpu", "gpu"), default="cpu")
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--log-file", type=Path)
    return parser.parse_args(values)


def _configure_logging(path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=_MAXIMUM_LOG_BYTES,
        backupCount=2,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[handler],
        force=True,
    )


def _local_bootstrap() -> LocalWorkerBootstrap | None:
    raw_path = os.environ.pop(LOCAL_WORKER_BOOTSTRAP_ENV, None)
    if raw_path is None:
        return None
    path = Path(raw_path)
    try:
        return LocalWorkerBootstrap.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    finally:
        durable_unlink(path, missing_ok=True)


def run_worker(
    *,
    settings: VidXPSettings,
    database_url: str,
    executor_id: str,
    role: str,
    ready: Callable[[], None] | None = None,
    stop_event: Event | None = None,
    stop_file: Path | None = None,
) -> None:
    config: DBOSConfig = {
        "name": APPLICATION_NAME,
        "system_database_url": database_url,
        "application_version": workflow_application_version(),
        "executor_id": executor_id,
        "max_executor_threads": 1 if role == "all" else None,
        "use_listen_notify": not database_url.startswith("sqlite:///"),
        "dbos_system_schema": (
            None if database_url.startswith("sqlite:///") else "dbos"
        ),
    }
    DBOS(config=config)
    try:
        VidXPWorkerWorkflows(create_application(settings))
        roles = ("cpu", "gpu") if role == "all" else (role,)
        queue_names = [
            QUEUE_NAMES[queue_role]
            for queue_role in (JobQueue(value) for value in roles)
        ]
        DBOS.listen_queues(queue_names)
        active_stop_event = stop_event or Event()
        DBOS.launch()
        for queue_name in queue_names:
            DBOS.register_queue(
                queue_name,
                worker_concurrency=1,
            )
        if ready is not None:
            ready()
        while not active_stop_event.wait(0.25):
            if stop_file is None or not stop_file.is_file():
                continue
            try:
                request = LocalWorkerStopRequest.model_validate_json(
                    stop_file.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if (
                request.pid == os.getpid()
                and request.application_version
                == workflow_application_version()
            ):
                break
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=30)


def main(arguments: Sequence[str] | None = None) -> None:
    options = _arguments(arguments)
    _configure_logging(options.log_file)
    bootstrap = _local_bootstrap()
    settings = (
        bootstrap.settings.application_settings()
        if bootstrap is not None
        else VidXPSettings()
    )
    if bootstrap is not None:
        expected_bootstrap = local_worker_bootstrap(settings)
        if expected_bootstrap.fingerprint != bootstrap.fingerprint:
            raise ValueError(
                "The local worker bootstrap identity is invalid."
            )
    database_url = (
        bootstrap.database_url
        if bootstrap is not None
        else workflow_database_url(settings)
    )
    role = options.role
    executor_id = options.executor_id
    if executor_id is None:
        if role == "all":
            raise ValueError("The all-queue worker requires an executor ID.")
        executor_id = server_executor_id(role=role)

    if options.lock_file is None:
        stop_event = Event()
        previous_handlers = _install_shutdown_handlers(stop_event)
        try:
            run_worker(
                settings=settings,
                database_url=database_url,
                executor_id=executor_id,
                role=role,
                stop_event=stop_event,
            )
        finally:
            _restore_shutdown_handlers(previous_handlers)
        return

    lock = FileLock(options.lock_file)
    ready_file = options.ready_file

    def mark_ready() -> None:
        if ready_file is None:
            return
        if bootstrap is None:
            raise RuntimeError(
                "A supervised local worker requires bootstrap identity."
            )
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            ready_file,
            LocalWorkerReady(
                pid=os.getpid(),
                application_version=workflow_application_version(),
                fingerprint=bootstrap.fingerprint,
            ).model_dump(mode="json"),
        )

    stop_event = Event()
    previous_handlers = _install_shutdown_handlers(stop_event)
    try:
        with lock:
            try:
                run_worker(
                    settings=settings,
                    database_url=database_url,
                    executor_id=executor_id,
                    role=role,
                    ready=mark_ready,
                    stop_event=stop_event,
                    stop_file=options.stop_file,
                )
            finally:
                if ready_file is not None:
                    durable_unlink(ready_file, missing_ok=True)
                if options.stop_file is not None:
                    durable_unlink(options.stop_file, missing_ok=True)
    finally:
        _restore_shutdown_handlers(previous_handlers)


def _install_shutdown_handlers(
    stop_event: Event,
) -> dict[signal.Signals, signal.Handlers]:
    previous_handlers = {}

    def request_shutdown(
        _signal_number: int,
        _frame: object,
    ) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        shutdown_signal = getattr(signal, signal_name, None)
        if shutdown_signal is None:
            continue
        previous_handlers[shutdown_signal] = signal.signal(
            shutdown_signal,
            request_shutdown,
        )
    return previous_handlers


def _restore_shutdown_handlers(
    previous_handlers: dict[signal.Signals, signal.Handlers],
) -> None:
    for shutdown_signal, previous_handler in previous_handlers.items():
        signal.signal(shutdown_signal, previous_handler)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOGGER.exception("The VidXP workflow worker exited unexpectedly.")
        raise
