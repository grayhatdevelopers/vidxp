from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from vidxp import __version__
from vidxp.settings import ApplicationMode, VidXPSettings


LOCAL_WORKER_SETTINGS_ENV = "VIDXP_LOCAL_WORKER_SETTINGS_JSON"


def workflow_database_url(settings: VidXPSettings) -> str:
    if settings.workflow_database_url is not None:
        database_url = settings.workflow_database_url
        if (
            settings.mode == ApplicationMode.server
            and not urlsplit(database_url).scheme.startswith("postgresql")
        ):
            raise ValueError(
                "Server mode requires a PostgreSQL workflow database URL."
            )
        return database_url
    if settings.mode == ApplicationMode.server:
        raise ValueError(
            "Server mode requires VIDXP_WORKFLOW_DATABASE_URL."
        )
    database = settings.layout.workflow_database.resolve()
    return f"sqlite:///{database.as_posix()}"


def workflow_application_version() -> str:
    return __version__


def local_executor_id(settings: VidXPSettings) -> str:
    if settings.mode == ApplicationMode.server:
        raise ValueError("A local executor ID cannot be used in server mode.")
    return f"{workflow_application_version()}-local-0"


def server_executor_id(*, role: str, ordinal: int) -> str:
    if role not in {"cpu", "gpu"} or ordinal < 0:
        raise ValueError("The worker role or ordinal is invalid.")
    return f"{workflow_application_version()}-{role}-{ordinal}"


def sqlite_database_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url.removeprefix(prefix))
