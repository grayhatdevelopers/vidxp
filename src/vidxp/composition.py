from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from vidxp.application import VidXPApplication
from vidxp.application_models import ApplicationError, ErrorCategory
from vidxp.capabilities.registry import create_capability_registry
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
from vidxp.artifact_service import ArtifactService
from vidxp.media_service import MediaService
from vidxp.runtime import ModelRuntime, RuntimeBackendUnavailableError
from vidxp.repositories import (
    RepositoryConfig,
    RepositoryConfigError,
    RepositoryRegistry,
    resolve_repository,
)
from vidxp.settings import VidXPSettings


@dataclass(frozen=True)
class LocalApplicationContext:
    application: VidXPApplication
    repositories: RepositoryRegistry
    repository: RepositoryConfig


def settings_for_repository(repository: RepositoryConfig) -> VidXPSettings:
    values = {"repository_root": repository.index_directory}
    if repository.device is not None:
        values["runtime_backend"] = repository.device
    return VidXPSettings(**values)


def create_application(
    settings: VidXPSettings | None = None,
) -> VidXPApplication:
    active_settings = settings or VidXPSettings()
    registry = create_capability_registry(
        external=active_settings.external_capabilities,
        allowlist=active_settings.capability_allowlist,
        platform_runtime_checks=LOCAL_INDEX_RUNTIME_CHECKS,
    )
    try:
        runtime = ModelRuntime(
            active_settings,
            allowed_specs=registry.model_specs(),
        )
    except RuntimeBackendUnavailableError as exc:
        raise ApplicationError(
            "runtime_unavailable",
            ErrorCategory.unavailable,
            "The requested runtime backend is unavailable.",
        ) from exc
    backend = LocalIndexBackend(registry, runtime, active_settings.layout)
    active_settings.layout.ensure_local_directories()
    catalog = LocalCatalog(active_settings.layout.catalog)
    media = MediaService(
        settings=active_settings,
        catalog=catalog,
        store=LocalMediaStore(
            active_settings.layout.media,
            max_bytes=active_settings.max_local_import_bytes,
        ),
        probe=FFprobeMediaProbe(active_settings.ffprobe_executable),
    )
    artifacts = ArtifactService(
        catalog=catalog,
        store=LocalArtifactStore(active_settings.layout.artifacts),
        media=media,
        probe=FFprobeMediaProbe(active_settings.ffprobe_executable),
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
        registry=registry,
        runtime=runtime,
        index_backend=backend,
        media_service=media,
        artifact_service=artifacts,
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
        application=create_application(settings),
        repositories=repositories,
        repository=repository,
    )
