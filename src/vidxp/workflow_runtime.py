from __future__ import annotations

import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vidxp import __version__
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.manifest import dependency_versions, implementation_digest
from vidxp.settings import (
    ApplicationMode,
    LocalExecutionSettings,
    VidXPSettings,
)


LOCAL_WORKER_BOOTSTRAP_ENV = "VIDXP_LOCAL_WORKER_BOOTSTRAP_FILE"
BUNDLED_POSTGRES_DATABASE_URL = (
    "postgresql+psycopg://vidxp@postgres:5432/vidxp"
)


class LocalWorkerBootstrap(BaseModel):
    """One-use local worker configuration stored outside process arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    settings: LocalExecutionSettings
    database_url: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class LocalWorkerReady(BaseModel):
    """Non-secret identity published by a ready local worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pid: int = Field(gt=0)
    application_version: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class LocalWorkerStopRequest(BaseModel):
    """Repository-local request for the supervised worker to shut down."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pid: int = Field(gt=0)
    application_version: str = Field(min_length=1)


def workflow_database_url(settings: VidXPSettings) -> str:
    if settings.mode == ApplicationMode.server:
        return BUNDLED_POSTGRES_DATABASE_URL
    database = settings.layout.workflow_database.resolve()
    return f"sqlite:///{database.as_posix()}"


@lru_cache(maxsize=1)
def workflow_application_version() -> str:
    """Identify the exact workflow implementation, not only the release."""

    return f"{__version__}+{implementation_digest()[:16]}"


@lru_cache(maxsize=16)
def _local_execution_provenance(
    external_capabilities: bool,
    capability_allowlist: tuple[str, ...],
) -> dict:
    registry = create_capability_registry(
        external=external_capabilities,
        allowlist=capability_allowlist,
    )
    return {
        "executable": str(Path(sys.executable).resolve()),
        "implementation_sha256": implementation_digest(),
        "python": sys.version,
        "dependencies": dependency_versions(registry),
    }


def local_worker_bootstrap(settings: VidXPSettings) -> LocalWorkerBootstrap:
    local_settings = LocalExecutionSettings.from_settings(settings)
    active_database_url = workflow_database_url(settings)
    identity = {
        "application_version": workflow_application_version(),
        "database_url": active_database_url,
        "execution": _local_execution_provenance(
            settings.external_capabilities,
            settings.capability_allowlist,
        ),
        "settings": local_settings.model_dump(mode="json"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return LocalWorkerBootstrap(
        settings=local_settings,
        database_url=active_database_url,
        fingerprint=fingerprint,
    )


def local_executor_id(settings: VidXPSettings) -> str:
    if settings.mode == ApplicationMode.server:
        raise ValueError("A local executor ID cannot be used in server mode.")
    return f"{workflow_application_version()}-local-0"


def server_executor_id(*, role: str) -> str:
    if role not in {"cpu", "gpu"}:
        raise ValueError("The worker role is invalid.")
    return f"{workflow_application_version()}-{role}-0"


def sqlite_database_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url.removeprefix(prefix))
