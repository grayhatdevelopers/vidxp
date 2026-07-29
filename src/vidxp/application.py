from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, cast

from pydantic import BaseModel

from vidxp.application_boundary import application_boundary
from vidxp.application_models import (
    ApplicationError,
    Artifact,
    CapabilityInfo,
    ComponentReadiness,
    CreateIndexCommand,
    CreateActorOverlayCommand,
    CreateSnippetCommand,
    DependencyCheckCommand,
    DependencyCheckResult,
    DependencyUnavailableError,
    ErrorCategory,
    InvalidRequestError,
    IndexResult,
    IndexStatus,
    ImportMediaCommand,
    ListMediaCommand,
    MediaAsset,
    MediaPage,
    PrepareModelsCommand,
    PrepareModelsResult,
    RemoveIndexCommand,
    ResourceNotFoundError,
    RuntimeReadiness,
    SearchCommand,
)
from vidxp.capabilities.actor.schemas import (
    ActorClustersOutput,
    ActorDetectionsOutput,
)
from vidxp.capabilities.contracts import (
    CapabilityDependencyError,
    CapabilityContext,
    CapabilityRequestError,
    PreparationContext,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capability_service import CapabilityService
from vidxp.capabilities.schemas import SearchResult
from vidxp.core.contracts import (
    IndexConfig,
)
from vidxp.execution import ExecutionContext, execution_context
from vidxp.index_state import (
    INDEX_STATUS_SCHEMA,
)
from vidxp.ports import IndexBackend, ModelRuntimePort
from vidxp.ports import LocalFileResource
from vidxp.model_contracts import ModelArtifactUnavailableError
from vidxp.repository_layout import RepositoryLayout
from vidxp.settings import VidXPSettings
from vidxp.artifact_service import (
    ArtifactService,
)
from vidxp.core.media import (
    QuarantinedMedia,
)
from vidxp.media_service import (
    MediaService,
)


class VidXPApplication:
    """The transport-neutral command and query boundary."""

    def __init__(
        self,
        *,
        settings: VidXPSettings,
        layout: RepositoryLayout,
        registry: CapabilityRegistry,
        runtime: ModelRuntimePort,
        index_backend: IndexBackend,
        media_service: MediaService,
        artifact_service: ArtifactService,
    ) -> None:
        self.settings = settings
        self.layout = layout
        self.registry = registry
        self.capabilities = CapabilityService(registry)
        self.runtime = runtime
        self.index_backend = index_backend
        self.media_service = media_service
        self.artifact_service = artifact_service

    @contextmanager
    def _capability_dependencies(
        self,
        capabilities: tuple[str, ...],
        *,
        missing_resource: str | None = None,
    ) -> Iterator[None]:
        try:
            yield
        except FileNotFoundError as exc:
            if missing_resource is not None:
                raise ResourceNotFoundError(missing_resource) from exc
            raise DependencyUnavailableError(
                capabilities,
                self.registry.install_hint(capabilities),
            ) from exc
        except (
            ModuleNotFoundError,
            CapabilityDependencyError,
            ModelArtifactUnavailableError,
        ) as exc:
            raise DependencyUnavailableError(
                capabilities,
                self.registry.install_hint(capabilities),
            ) from exc

    @property
    def index_directory(self) -> Path:
        return self.layout.local_index

    @property
    def device(self) -> str:
        return self.runtime.backends.torch_device

    @application_boundary
    def index_status(self) -> IndexStatus:
        stored = self.index_backend.status(self.index_directory)
        payload = (
            dict(stored)
            if stored is not None
            else {
                "schema_version": INDEX_STATUS_SCHEMA,
                "state": "missing",
                "stage": "status",
                "message": "No local video index was found.",
            }
        )
        return IndexStatus.model_validate(payload)

    @application_boundary
    def _active_config(self) -> tuple[IndexConfig, dict[str, Any]]:
        return self.index_backend.active_config(
            self.index_directory,
            device=self.device,
        )

    @application_boundary
    def import_media(self, command: ImportMediaCommand) -> MediaAsset:
        self.layout.ensure_local_directories()
        return self.media_service.import_local(command)

    @application_boundary
    def import_uploaded_media(
        self,
        *,
        staged_path: Path,
        original_filename: str,
        declared_mime_type: str | None,
    ) -> MediaAsset:
        self.layout.ensure_local_directories()
        return self.media_service.import_quarantined(
            QuarantinedMedia(
                path=staged_path,
                original_filename=original_filename,
                declared_mime_type=declared_mime_type,
            )
        )

    @application_boundary
    def list_capabilities(self) -> tuple[CapabilityInfo, ...]:
        return self.capabilities.list()

    @application_boundary
    def get_capability(self, name: str) -> CapabilityInfo:
        try:
            return self.capabilities.get(name)
        except CapabilityRequestError as exc:
            raise ResourceNotFoundError("capability") from exc

    @application_boundary
    def control_plane_readiness(self) -> tuple[ComponentReadiness, ...]:
        components: list[ComponentReadiness] = []
        try:
            self.media_service.list(ListMediaCommand(page_size=1))
        except Exception:
            components.append(
                ComponentReadiness(
                    name="catalog",
                    ready=False,
                    message="The media catalog is unavailable.",
                )
            )
        else:
            components.append(
                ComponentReadiness(
                    name="catalog",
                    ready=True,
                    message="The media catalog is available.",
                )
            )
        try:
            self.index_status()
        except ApplicationError:
            components.append(
                ComponentReadiness(
                    name="index",
                    ready=False,
                    message="The index catalog is unavailable.",
                )
            )
        else:
            components.append(
                ComponentReadiness(
                    name="index",
                    ready=True,
                    message="The index catalog is available.",
                )
            )
        return tuple(components)

    @application_boundary
    def runtime_readiness(self) -> RuntimeReadiness:
        components = self.control_plane_readiness()
        dependencies = self.check_dependencies(
            DependencyCheckCommand(modalities=self.registry.names())
        )
        return RuntimeReadiness(
            ready=all(component.ready for component in components),
            runtime=self.runtime.backends,
            components=components,
            dependencies=dependencies,
        )

    @application_boundary
    def get_media(self, media_id: str) -> MediaAsset:
        return self.media_service.get(media_id)

    @application_boundary
    def list_media(
        self,
        command: ListMediaCommand,
    ) -> MediaPage:
        try:
            return self.media_service.list(command)
        except ValueError as exc:
            raise InvalidRequestError() from exc

    @application_boundary
    def open_media_content(self, media_id: str) -> LocalFileResource:
        return self.media_service.content(media_id)

    @application_boundary
    def get_artifact(self, artifact_id: str) -> Artifact:
        return self.artifact_service.get(artifact_id)

    @application_boundary
    def open_artifact_content(self, artifact_id: str) -> LocalFileResource:
        return self.artifact_service.content(artifact_id)

    @application_boundary
    def create_index(
        self,
        command: CreateIndexCommand,
        *,
        execution: ExecutionContext | None = None,
    ) -> IndexResult:
        active_execution = execution_context(execution)
        selected = self.registry.validate_names(command.modalities)
        non_indexable = [
            name
            for name in selected
            if self.registry.get(name).collection_name is None
        ]
        if non_indexable:
            raise CapabilityRequestError(
                "One or more selected capabilities do not support indexing."
            )
        media = self.media_service.require_record(command.media_id)
        content = self.media_service.content(command.media_id)
        self.layout.ensure_local_directories()
        config = IndexConfig.local(
            video_id=command.media_id,
            enabled_modalities=selected,
            frame_stride=command.frame_stride,
            storage_directory=self.index_directory,
            collection_names=self.registry.collection_names(selected),
            capability_options=self.registry.validate_options(
                selected,
                command.capability_options,
            ),
            device=self.device,
        )
        with self.runtime.scheduler.indexing():
            with self._capability_dependencies(selected):
                result = self.index_backend.create(
                    content.path,
                    config=config,
                    progress=active_execution.report,
                    cancellation=active_execution.cancellation,
                    operation_id=active_execution.operation_id,
                    source_name=media.original_filename,
                    source_checksum=media.sha256,
                )
                return IndexResult.model_validate(result)

    @application_boundary
    def indexing_in_progress(self) -> bool:
        return self.index_backend.indexing_in_progress(
            self._base_config()
        )

    @application_boundary
    def check_dependencies(
        self,
        command: DependencyCheckCommand,
    ) -> DependencyCheckResult:
        selected = self.registry.validate_names(command.modalities)
        checks = self.registry.dependency_checks(selected)
        return DependencyCheckResult(
            ok=all(check.ok for check in checks),
            modalities=selected,
            checks=checks,
        )

    @application_boundary
    def prepare_models(
        self,
        command: PrepareModelsCommand,
        *,
        execution: ExecutionContext | None = None,
    ) -> PrepareModelsResult:
        active_execution = execution_context(execution)
        selected = self.registry.validate_names(command.modalities)
        options = self.registry.validate_options(
            selected,
            command.capability_options,
        )
        checks = self.registry.dependency_checks(selected)
        failures = [check for check in checks if not check.ok]
        if failures:
            raise DependencyUnavailableError(
                selected,
                self.registry.install_hint(selected),
            )

        prepared: list[str] = []
        with self.runtime.scheduler.inference():
            with self._capability_dependencies(selected):
                for name in selected:
                    active_execution.checkpoint()
                    executor = self.registry.executor(name)
                    if executor.prepare is not None:
                        prepared.extend(
                            executor.prepare(
                                PreparationContext(
                                    runtime=self.runtime,
                                    settings=self.registry.get(name)
                                    .config_model.model_validate(options[name]),
                                ),
                                active_execution.report,
                            )
                        )
                    active_execution.checkpoint()
        return PrepareModelsResult(
            prepared=tuple(prepared),
            modalities=selected,
            runtime=self.runtime.backends,
        )

    @application_boundary
    def execute(
        self,
        capability: str,
        operation: str,
        payload: BaseModel | Mapping[str, Any],
    ) -> BaseModel:
        definition = self.registry.get(capability)
        try:
            contract = definition.operations[operation]
            handler = self.registry.executor(capability).operations[operation]
        except KeyError as exc:
            available = ", ".join(definition.operations) or "none"
            raise CapabilityRequestError(
                f"Capability {capability!r} has no operation {operation!r}; "
                f"available operations: {available}."
            ) from exc

        config = None
        if contract.requires_index:
            config, _ = self._active_config()
            if capability not in config.enabled_modalities:
                raise CapabilityRequestError(
                    f"The {capability} capability is not present in this index."
                )
        request = contract.input_model.model_validate(payload)
        with self._capability_dependencies((capability,)):
            if config is None:
                with self.runtime.scheduler.inference():
                    response = handler(
                        CapabilityContext(
                            config=None,
                            runtime=self.runtime,
                        ),
                        request,
                    )
                return contract.output_model.model_validate(response)
            with self.index_backend.open_store(config) as storage:
                with self.runtime.scheduler.inference():
                    response = handler(
                        CapabilityContext(
                            config=config,
                            runtime=self.runtime,
                            storage=storage,
                        ),
                        request,
                    )
                return contract.output_model.model_validate(response)

    @application_boundary
    def search(self, command: SearchCommand) -> SearchResult:
        return cast(
            SearchResult,
            self.execute(
                command.modality,
                "search",
                {"query": command.query, "top_k": command.top_k},
            ),
        )

    @application_boundary
    def actor_clusters(
        self,
        *,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> ActorClustersOutput:
        return cast(
            ActorClustersOutput,
            self.execute(
                "actor",
                "clusters",
                {"page_size": page_size, "cursor": cursor},
            ),
        )

    @application_boundary
    def actor_detections(
        self,
        cluster_id: str,
        *,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> ActorDetectionsOutput:
        return cast(
            ActorDetectionsOutput,
            self.execute(
                "actor",
                "detections",
                {
                    "cluster_id": cluster_id,
                    "page_size": page_size,
                    "cursor": cursor,
                },
            ),
        )

    @application_boundary
    def render_actor(
        self,
        command: CreateActorOverlayCommand,
        *,
        execution: ExecutionContext | None = None,
    ) -> Artifact:
        active_execution = execution_context(execution)
        active_execution.report(
            {
                "stage": "resolving_detections",
                "message": "Resolving actor detections.",
            }
        )
        detections = []
        cursor = None
        while True:
            active_execution.checkpoint()
            page = self.actor_detections(
                command.cluster_id,
                page_size=100,
                cursor=cursor,
            )
            detections.extend(page.detections)
            cursor = page.next_cursor
            if cursor is None:
                break
        identities = {
            (detection.media_id, detection.generation_id)
            for detection in detections
        }
        if identities != {(command.media_id, command.generation_id)}:
            raise ApplicationError(
                "actor_cluster_identity_invalid",
                ErrorCategory.conflict,
                "The actor cluster does not match the requested index identity.",
            )
        return self.artifact_service.create_actor_overlay(
            media_id=command.media_id,
            generation_id=command.generation_id,
            cluster_id=command.cluster_id,
            detections=[
                detection.model_dump(mode="python")
                for detection in detections
            ],
            profile=command.profile,
            job_id=active_execution.job_id,
            execution=active_execution,
        )

    @application_boundary
    def create_snippet(
        self,
        command: CreateSnippetCommand,
        *,
        execution: ExecutionContext | None = None,
    ) -> Artifact:
        active_execution = execution_context(execution)
        return self.artifact_service.create_snippet(
            command,
            job_id=active_execution.job_id,
            execution=active_execution,
        )

    @application_boundary
    def clear_index(self) -> bool:
        base_config = self._base_config()
        try:
            return self.index_backend.clear(base_config)
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                self.registry.index_names(),
                self.registry.install_hint(self.registry.index_names()),
            ) from exc

    @application_boundary
    def remove_from_index(self, command: RemoveIndexCommand) -> bool:
        return self.index_backend.remove(
            self._base_config(),
            command.media_id,
        )

    def _base_config(self) -> IndexConfig:
        return IndexConfig.local(
            storage_directory=self.index_directory,
            enabled_modalities=self.registry.index_names(),
            collection_names=self.registry.collection_names(),
            device=self.device,
        )
