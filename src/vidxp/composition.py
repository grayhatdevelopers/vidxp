from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from vidxp.application import VidXPApplication
from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    ErrorCategory,
)
from vidxp.artifact_service import ArtifactQueryService, ArtifactService
from vidxp.authentication import Authenticator, create_authenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.capability_service import CapabilityService
from vidxp.capabilities.registry import (
    CapabilityRegistry,
    create_capability_registry,
)
from vidxp.control_plane import ControlPlaneApplication
from vidxp.core.storage import BUNDLED_CHROMA_SERVER_URL
from vidxp.dependencies import active_requirements, packaged_requirements
from vidxp.infrastructure.local_index import (
    LOCAL_INDEX_RUNTIME_CHECKS,
    SERVER_INDEX_RUNTIME_CHECKS,
    LocalIndexBackend,
    LocalIndexReader,
)
from vidxp.infrastructure.local_artifacts import (
    FFmpegSnippetRenderer,
    FFmpegFrameRenderer,
    LocalActorRenderer,
    LocalArtifactStore,
)
from vidxp.infrastructure.local_catalog import LocalCatalog
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.infrastructure.sql_snapshots import SQLSnapshotRepository
from vidxp.infrastructure.local_media import FFprobeMediaProbe, LocalMediaStore
from vidxp.infrastructure.local_snapshots import LocalSnapshotRepository
from vidxp.infrastructure.dbos_jobs import DBOSJobBackend
from vidxp.infrastructure.local_worker import LocalWorkerSupervisor
from vidxp.job_service import JobService
from vidxp.media_service import MediaService
from vidxp.network_share import (
    is_loopback_host,
    load_or_create_native_artifact_secret,
    load_or_create_native_upload_secret,
)
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
from vidxp.upload_service import RemoteUploadService
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
        return create_job_service(
            self._settings,
            index_preflight=self.application.preflight_index,
        )

    def close(self) -> None:
        jobs = self.__dict__.get("jobs")
        if jobs is not None:
            jobs.close()


@dataclass(frozen=True, kw_only=True)
class ControlPlaneContext:
    application: ControlPlaneApplication
    jobs: JobService
    authorization: AuthorizationPolicy
    settings: VidXPSettings
    catalog: SQLCatalog | None = None
    uploads: RemoteUploadService | None = None
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def start(self, *, eager_jobs: bool = True) -> None:
        if self._closed:
            raise RuntimeError("A closed control-plane context cannot be restarted.")
        if eager_jobs:
            self.jobs.start()
        coordinator = (
            getattr(self.uploads, "coordinator", None)
            if self.uploads is not None
            else None
        )
        if coordinator is not None:
            coordinator.start(self.uploads.reconcile)

    def stop(self) -> None:
        coordinator = (
            getattr(self.uploads, "coordinator", None)
            if self.uploads is not None
            else None
        )
        if coordinator is not None:
            coordinator.stop()

    def close(self) -> None:
        if self._closed:
            return
        self.stop()
        try:
            if self.settings.mode != ApplicationMode.server:
                self.jobs.stop_worker()
        finally:
            try:
                self.jobs.close()
            finally:
                if self.catalog is not None:
                    self.catalog.close()
        object.__setattr__(self, "_closed", True)


@dataclass(frozen=True, kw_only=True)
class HttpApplicationContext(ControlPlaneContext):
    readiness: ReadinessService
    authenticator: Authenticator


@dataclass(frozen=True)
class UploadHookContext:
    jobs: JobService
    authenticator: Authenticator
    authorization: AuthorizationPolicy
    settings: VidXPSettings
    catalog: SQLCatalog
    uploads: RemoteUploadService
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("A closed upload-hook context cannot be restarted.")
        self.jobs.start()

    def stop(self) -> None:
        """The hook owns no background ingestion lifecycle."""

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.jobs.close()
        finally:
            self.catalog.close()
        object.__setattr__(self, "_closed", True)


@dataclass(frozen=True)
class _ControlPlaneComponents:
    registry: CapabilityRegistry
    catalog: SQLCatalog
    media: MediaService
    artifact_store: LocalArtifactStore
    probe: FFprobeMediaProbe
    snapshots: LocalSnapshotRepository


def _server_chroma_url(settings: VidXPSettings) -> str | None:
    if settings.mode != ApplicationMode.server:
        return None
    return BUNDLED_CHROMA_SERVER_URL


def _capability_registry(settings: VidXPSettings) -> CapabilityRegistry:
    server_mode = settings.mode == ApplicationMode.server
    return create_capability_registry(
        external=settings.external_capabilities,
        allowlist=settings.capability_allowlist,
        platform_runtime_checks=(
            SERVER_INDEX_RUNTIME_CHECKS if server_mode else LOCAL_INDEX_RUNTIME_CHECKS
        ),
        storage_requirements=(
            active_requirements(
                packaged_requirements(
                    "vidxp",
                    "requirements/server-storage.txt",
                )
            )
            if server_mode
            else None
        ),
    )


def _create_control_plane_components(
    settings: VidXPSettings,
) -> _ControlPlaneComponents:
    settings.layout.ensure_local_directories()
    registry = _capability_registry(settings)
    catalog = (
        SQLCatalog(
            workflow_database_url(settings),
            initialize=False,
        )
        if settings.mode == ApplicationMode.server
        else LocalCatalog(settings.layout.catalog)
    )
    probe = FFprobeMediaProbe(settings.ffprobe_executable)
    snapshots = (
        SQLSnapshotRepository(
            settings.layout.indexes,
            engine=catalog.engine,
        )
        if settings.mode == ApplicationMode.server
        else LocalSnapshotRepository(settings.layout.indexes)
    )
    return _ControlPlaneComponents(
        registry=registry,
        catalog=catalog,
        media=MediaService(
            settings=settings,
            catalog=catalog,
            store=LocalMediaStore(
                settings.layout.media,
                max_bytes=(
                    settings.upload_max_bytes
                    if settings.mode == ApplicationMode.server
                    else settings.max_local_import_bytes
                ),
            ),
            probe=probe,
        ),
        artifact_store=LocalArtifactStore(settings.layout.artifacts),
        probe=probe,
        snapshots=snapshots,
    )


def settings_for_repository(
    repository: RepositoryConfig,
    *,
    data_directory: str | Path | None = None,
) -> VidXPSettings:
    values = {
        "mode": ApplicationMode.local,
        "repository_root": repository.index_directory,
    }
    if data_directory is not None:
        values["data_dir"] = Path(data_directory)
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
        chroma_server_url=_server_chroma_url(active_settings),
        snapshot_repository=components.snapshots,
    )
    artifacts = ArtifactService(
        catalog=components.catalog,
        store=components.artifact_store,
        media=components.media,
        probe=components.probe,
        actor_renderer=LocalActorRenderer(),
        snippet_renderer=FFmpegSnippetRenderer(active_settings.ffmpeg_executable),
        frame_renderer=FFmpegFrameRenderer(active_settings.ffmpeg_executable),
        max_snippet_duration_seconds=(active_settings.max_snippet_duration_seconds),
    )
    upload_service = RemoteUploadService(
        settings=active_settings,
        catalog=components.catalog,
        media=components.media,
        default_index_modalities=components.registry.index_names(),
    )
    query_model = None
    if (
        active_settings.slm_base_url is not None
        and active_settings.slm_model is not None
    ):
        from vidxp.infrastructure.ollama_query import OllamaQueryModel

        query_model = OllamaQueryModel(
            base_url=active_settings.slm_base_url,
            model_name=active_settings.slm_model,
            timeout_seconds=active_settings.slm_timeout_seconds,
            output_retries=active_settings.slm_output_retries,
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
        active_snapshot=components.snapshots.read_active,
        completed_upload_importer=(upload_service.import_completed),
        query_model=query_model,
    )


def create_job_service(
    settings: VidXPSettings,
    *,
    catalog: SQLCatalog | None = None,
    snapshots: LocalSnapshotRepository | None = None,
    registry: CapabilityRegistry | None = None,
    index_preflight: Callable[[CreateIndexCommand], None] | None = None,
    include_read_planner: bool = True,
) -> JobService:
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
        index_preflight=index_preflight,
        backend=DBOSJobBackend(
            system_database_url=(
                None
                if settings.mode == ApplicationMode.server and catalog is not None
                else workflow_database_url(settings)
            ),
            system_database_engine=(
                catalog.engine
                if settings.mode == ApplicationMode.server and catalog is not None
                else None
            ),
            application_version=workflow_application_version(),
            before_access=before_access,
            health_check=health_check,
            stop_executor=stop_executor,
        ),
        read_planner=(
            LocalReadJobPlanner(
                layout=settings.layout,
                registry=registry or _capability_registry(settings),
                index=LocalIndexReader(
                    settings.layout,
                    chroma_server_url=_server_chroma_url(settings),
                    snapshot_repository=snapshots,
                ),
            )
            if include_read_planner
            else None
        ),
    )


def create_control_plane_application(
    settings: VidXPSettings | None = None,
) -> ControlPlaneContext:
    active_settings = settings or VidXPSettings()
    components = _create_control_plane_components(active_settings)
    application = ControlPlaneApplication(
        layout=active_settings.layout,
        capabilities=CapabilityService(components.registry),
        media=components.media,
        artifacts=ArtifactQueryService(
            catalog=components.catalog,
            store=components.artifact_store,
        ),
        index_status=components.snapshots.status,
        model_cache=active_settings.model_cache,
        active_snapshot=components.snapshots.read_active,
    )
    jobs = create_job_service(
        active_settings,
        catalog=components.catalog,
        snapshots=components.snapshots,
        registry=components.registry,
        index_preflight=application.preflight_index,
    )
    uploads = RemoteUploadService(
        settings=active_settings,
        catalog=components.catalog,
        media=components.media,
        jobs=jobs,
        default_index_modalities=components.registry.index_names(),
    )
    return ControlPlaneContext(
        application=application,
        jobs=jobs,
        authorization=AuthorizationPolicy(),
        settings=active_settings,
        catalog=components.catalog,
        uploads=uploads,
    )


def create_http_application(
    settings: VidXPSettings | None = None,
) -> HttpApplicationContext:
    active_settings = settings or VidXPSettings()
    if (
        active_settings.mode == ApplicationMode.local
        and is_loopback_host(active_settings.http_bind_host)
    ):
        host = active_settings.http_bind_host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1" if host == "0.0.0.0" else "[::1]"
        elif ":" in host and not host.startswith("["):
            host = f"[{host}]"
        payload = active_settings.model_dump(mode="python")
        native_surfaces: dict[str, object] = {}
        if (
            active_settings.upload_handoff_public_url is None
            and active_settings.upload_handoff_secret is None
        ):
            native_surfaces.update({
                "upload_handoff_public_url": (
                    f"http://{host}:{active_settings.http_port}/upload-handoff"
                ),
                "upload_handoff_secret": load_or_create_native_upload_secret(
                    active_settings.data_dir
                ),
            })
        if (
            active_settings.artifact_download_public_url is None
            and active_settings.artifact_download_secret is None
        ):
            native_surfaces.update({
                "artifact_download_public_url": (
                    f"http://{host}:{active_settings.http_port}/artifact-download"
                ),
                "artifact_download_secret": load_or_create_native_artifact_secret(
                    active_settings.data_dir
                ),
            })
        if native_surfaces:
            payload.update(native_surfaces)
            active_settings = VidXPSettings.model_validate(payload)
    active_settings.validate_http_server()
    control = create_control_plane_application(active_settings)
    authenticator = create_authenticator(active_settings)
    return HttpApplicationContext(
        application=control.application,
        jobs=control.jobs,
        authorization=control.authorization,
        settings=control.settings,
        catalog=control.catalog,
        uploads=control.uploads,
        readiness=ReadinessService(
            application=control.application,
            jobs=control.jobs,
            authenticator=authenticator,
        ),
        authenticator=authenticator,
    )


def create_upload_hook_context(
    settings: VidXPSettings | None = None,
) -> UploadHookContext:
    active_settings = settings or VidXPSettings()
    active_settings.validate_http_server()
    if active_settings.mode != ApplicationMode.server:
        raise ValueError("The tusd hook service requires server mode.")
    catalog = SQLCatalog(
        workflow_database_url(active_settings),
        initialize=False,
    )
    try:
        jobs = create_job_service(
            active_settings,
            catalog=catalog,
            include_read_planner=False,
        )
        uploads = RemoteUploadService(
            settings=active_settings,
            catalog=catalog,
            media=None,
            jobs=jobs,
        )
        return UploadHookContext(
            jobs=jobs,
            authenticator=create_authenticator(active_settings),
            authorization=AuthorizationPolicy(),
            settings=active_settings,
            catalog=catalog,
            uploads=uploads,
        )
    except Exception:
        catalog.close()
        raise


def create_local_application(
    *,
    registry_path: str | Path | None = None,
    repository_name: str | None = None,
    index_directory: str | Path | None = None,
    data_directory: str | Path | None = None,
    device: str | None = None,
) -> LocalApplicationContext:
    try:
        repositories, repository = resolve_repository(
            registry_path=registry_path,
            name=repository_name,
            index_directory=index_directory,
            data_directory=data_directory,
            device=device,
        )
        settings = settings_for_repository(
            repository,
            data_directory=data_directory,
        )
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
