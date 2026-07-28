from __future__ import annotations

import argparse
import os
from threading import Event
from pathlib import Path
from typing import Callable, Sequence

from dbos import DBOS, DBOSConfig
from filelock import FileLock

from vidxp.application_models import JobQueue
from vidxp.composition import create_application
from vidxp.infrastructure.dbos_workflows import VidXPWorkerWorkflows
from vidxp.settings import VidXPSettings
from vidxp.workflow_contracts import APPLICATION_NAME, QUEUE_NAMES
from vidxp.workflow_runtime import (
    LOCAL_WORKER_SETTINGS_ENV,
    server_executor_id,
    workflow_application_version,
    workflow_database_url,
)


def _arguments(values: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VidXP DBOS workflows.")
    parser.add_argument("--database-url")
    parser.add_argument("--executor-id")
    parser.add_argument("--role", choices=("all", "cpu", "gpu"), default="cpu")
    parser.add_argument("--ordinal", type=int, default=0)
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--ready-file", type=Path)
    return parser.parse_args(values)


def _resolved_database_url(
    settings: VidXPSettings,
    override: str | None,
) -> str:
    if override is None:
        return workflow_database_url(settings)
    return workflow_database_url(
        settings.model_copy(update={"workflow_database_url": override})
    )


def run_worker(
    *,
    settings: VidXPSettings,
    database_url: str,
    executor_id: str,
    role: str,
    ready: Callable[[], None] | None = None,
) -> None:
    config: DBOSConfig = {
        "name": APPLICATION_NAME,
        "system_database_url": database_url,
        "application_version": workflow_application_version(),
        "executor_id": executor_id,
        "max_executor_threads": 1 if role == "all" else None,
        "use_listen_notify": not database_url.startswith("sqlite:///"),
    }
    DBOS(config=config)

    VidXPWorkerWorkflows(create_application(settings))
    roles = ("cpu", "gpu") if role == "all" else (role,)
    queue_names = [
        QUEUE_NAMES[queue_role]
        for queue_role in (JobQueue(value) for value in roles)
    ]
    DBOS.listen_queues(queue_names)
    DBOS.launch()
    for queue_name in queue_names:
        DBOS.register_queue(
            queue_name,
            worker_concurrency=1,
        )
    if ready is not None:
        ready()
    Event().wait()


def main(arguments: Sequence[str] | None = None) -> None:
    options = _arguments(arguments)
    local_settings = os.environ.pop(LOCAL_WORKER_SETTINGS_ENV, None)
    settings = (
        VidXPSettings.model_validate_json(local_settings)
        if local_settings is not None
        else VidXPSettings()
    )
    database_url = _resolved_database_url(settings, options.database_url)
    role = options.role
    executor_id = options.executor_id
    if executor_id is None:
        if role == "all":
            raise ValueError("The all-queue worker requires an executor ID.")
        executor_id = server_executor_id(
            role=role,
            ordinal=options.ordinal,
        )

    if options.lock_file is None:
        run_worker(
            settings=settings,
            database_url=database_url,
            executor_id=executor_id,
            role=role,
        )
        return

    lock = FileLock(options.lock_file)
    ready_file = options.ready_file

    def mark_ready() -> None:
        if ready_file is None:
            return
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = ready_file.with_suffix(".tmp")
        temporary.write_text(str(os.getpid()), encoding="utf-8")
        temporary.replace(ready_file)

    with lock:
        try:
            run_worker(
                settings=settings,
                database_url=database_url,
                executor_id=executor_id,
                role=role,
                ready=mark_ready,
            )
        finally:
            if ready_file is not None:
                ready_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
