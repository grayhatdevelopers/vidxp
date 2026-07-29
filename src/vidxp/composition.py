from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from pydantic import ValidationError

from vidxp.application import VidXPApplication
from vidxp.application_models import ApplicationError, ErrorCategory
from vidxp.artifact_service import ArtifactQueryService, ArtifactService
from vidxp.authentication import Authenticator, create_authenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.capability_service import CapabilityService
from vidxp.capabilities.registry import (
    CapabilityRegistry,
    create_capability_registry,
)
from vidxp.control_plane import ControlPlaneApplication
from vidxp.infrastructure.local_index import (
    LOCAL_INDEX_RUNTIME_CHECKS,
    LocalIndexBackend,
)
from vidxp.infrastructure.local_artifacts import (
    FFmpegSnippetRenderer,
    LocalActorRenderer,
    LocalArtifactStore,
)
from vidxp.infrastructure.local_catalog import LocalCatalog
from vidxp.infrastructure.local_media import FFprobeMediaProbe, LocalMediaStore
from vidxp.infrastructure.local_snapshots import LocalSnapshotRepository
from vidxp.infrastructure.dbos_jobs import DBOSJobBackend
from vidxp.infrastructure.local_worker import LocalWorkerSupervisor
from vidxp.job_service import JobService
from vidxp.media_service import MediaService
from vidxp.readiness_service import ReadinessService
from vidxp.read_job_planner import LocalReadJobPlanner
from vidxp.runtime import ModelRuntime, RuntimeBackendUnavailableError
from vidxp.repositories import (
    RepositoryConfig,
    RepositoryConfigError,
    RepositoryRegistry,
    resolve_repository,
)
from vidxp.settings import ApplicationMode, VidXPSettings
from vidxp.workflow_runtime import (
    workflow_application_version,
    workflow_database_url,
)


class LocalApplicationContext:
    """Lazy local composition so lightweight CLI commands stay lightweight."""

    def __init__(
        self,
        *,
        repositories: RepositoryRegistry,
        repository: RepositoryConfig,
        settings: VidXPSettings | None = None,
        application: VidXPApplication | None = None,
        jobs: JobService | None = None,
    ) -> None:
        if settings is None and (application is None or jobs is None):
            raise ValueError(
                "Lazy local composition requires settings or composed services."
            )
        self.repositories = repositories
        self.repository = repository
        self._settings = settings
        if application is not None:
            self.__dict__["application"] = application
        if jobs is not None:
            self.__dict__["jobs"] = jobs

    @cached_property
    def application(self) -> VidXPApplication:
        assert self._settings is not None
        return create_application(self._settings)

    @property
    def settings(self) -> VidXPSettings:
        if self._settings is not None:
            return self._settings
        return settings_for_repository(self.repository)

    @cached_property
    def jobs(self) -> JobService:
        assert self._settings is not None
        return create_job_service(self._settings)

    def close(self) -> None:
        jobs = self.__dict__.get("jobs")
        if jobs is not None:
            jobs.close()


@dataclass(frozen=True)
class HttpApplicationContext:
    application: ControlPlaneApplication
    jobs: JobService
    readiness: ReadinessService
    authenticator: Authenticator
    authorization: AuthorizationPolicy
    settings: VidXPSettings

    def close(self) -> None:
        self.jobs.close()


@dataclass(frozen=True)
class _ControlPlaneComponents:
    registry: CapabilityRegistry
    catalog: LocalCatalog
    media: MediaService
    artifact_store: LocalArtifactStore
    probe: FFprobeMediaProbe


def _create_control_plane_components(
    settings: VidXPSettings,
) -> _ControlPlaneComponents:
    settings.layout.ensure_local_directories()
    registry = create_capability_registry(
        external=settings.external_capabilities,
        allowlist=settings.capability_allowlist,
        platform_runtime_checks=LOCAL_INDEX_RUNTIME_CHECKS,
    )
    catalog = LocalCatalog(settings.layout.catalog)
    probe = FFprobeMediaProbe(settings.ffprobe_executable)
    return _ControlPlaneComponents(
        registry=registry,
        catalog=catalog,
        media=MediaService(
            settings=settings,
            catalog=catalog,
            store=LocalMediaStore(
                settings.layout.media,
                max_bytes=settings.max_local_import_bytes,
            ),
            probe=probe,
        ),
        artifact_store=LocalArtifactStore(settings.layout.artifacts),
        probe=probe,
    )


def settings_for_repository(repository: RepositoryConfig) -> VidXPSettings:
    values = {"repository_root": repository.index_directory}
    if repository.device is not None:
        values["runtime_backend"] = repository.device
    return VidXPSettings(**values)


def create_application(
    settings: VidXPSettings | None = None,
) -> VidXPApplication:
    active_settings = settings or VidXPSettings()
    components = _create_control_plane_components(active_settings)
    try:
        runtime = ModelRuntime(
            active_settings,
            allowed_specs=components.registry.model_specs(),
        )
    except RuntimeBackendUnavailableError as exc:
        raise ApplicationError(
            "runtime_unavailable",
            ErrorCategory.unavailable,
            "The requested runtime backend is unavailable.",
        ) from exc
    backend = LocalIndexBackend(
        components.registry,
        runtime,
        active_settings.layout,
    )
    artifacts = ArtifactService(
        catalog=components.catalog,
        store=components.artifact_store,
        media=components.media,
        probe=components.probe,
        actor_renderer=LocalActorRenderer(),
        snippet_renderer=FFmpegSnippetRenderer(
            active_settings.ffmpeg_executable
        ),
        max_snippet_duration_seconds=(
            active_settings.max_snippet_duration_seconds
        ),
    )
    return VidXPApplication(
        settings=active_settings,
        layout=active_settings.layout,
        registry=components.registry,
        runtime=runtime,
        index_backend=backend,
        media=components.media,
        artifacts=artifacts,
        index_status=backend.repository.status,
    )


def create_job_service(settings: VidXPSettings) -> JobService:
    settings.layout.ensure_local_directories()
    before_access = None
    health_check = None
    stop_executor = None
    if settings.mode != ApplicationMode.server:
        supervisor = LocalWorkerSupervisor(settings)
        before_access = supervisor.ensure_running
        health_check = supervisor.health
        stop_executor = supervisor.stop
    return JobService(
        settings=settings,
        backend=DBOSJobBackend(
            system_database_url=workflow_database_url(settings),
            application_version=workflow_application_version(),
            before_access=before_access,
            health_check=health_check,
            stop_executor=stop_executor,
        ),
        read_planner=LocalReadJobPlanner(layout=settings.layout),
    )


def create_http_application(
    settings: VidXPSettings | None = None,
) -> HttpApplicationContext:
    active_settings = settings or VidXPSettings()
    active_settings.validate_http_server()
    components = _create_control_plane_components(active_settings)
    application = ControlPlaneApplication(
        layout=active_settings.layout,
        capabilities=CapabilityService(components.registry),
        media=components.media,
        artifacts=ArtifactQueryService(
            catalog=components.catalog,
            store=components.artifact_store,
        ),
        index_status=LocalSnapshotRepository(
            active_settings.layout.indexes
        ).status,
    )
    jobs = create_job_service(active_settings)
    authenticator = create_authenticator(active_settings)
    return HttpApplicationContext(
        application=application,
        jobs=jobs,
        readiness=ReadinessService(
            application=application,
            jobs=jobs,
            authenticator=authenticator,
        ),
        authenticator=authenticator,
        authorization=AuthorizationPolicy(),
        settings=active_settings,
    )


def create_local_application(
    *,
    registry_path: str | Path | None = None,
    repository_name: str | None = None,
    index_directory: str | Path | None = None,
    device: str | None = None,
) -> LocalApplicationContext:
    try:
        repositories, repository = resolve_repository(
            registry_path=registry_path,
            name=repository_name,
            index_directory=index_directory,
            device=device,
        )
        settings = settings_for_repository(repository)
    except (RepositoryConfigError, ValidationError) as exc:
        raise ApplicationError(
            "configuration_invalid",
            ErrorCategory.validation,
            "The repository or runtime configuration is invalid.",
        ) from exc
    return LocalApplicationContext(
        repositories=repositories,
        repository=repository,
        settings=settings,
    )
