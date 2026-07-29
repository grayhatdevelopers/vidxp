from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, cast

from pydantic import BaseModel

from vidxp.application_boundary import application_boundary
from vidxp.application_models import (
    ApplicationError,
    Artifact,
    ComponentReadiness,
    CreateIndexCommand,
    CreateActorOverlayCommand,
    CreateSnippetCommand,
    DependencyCheckCommand,
    DependencyCheckResult,
    DependencyUnavailableError,
    ErrorCategory,
    IndexResult,
    ImportMediaCommand,
    IndexSnapshotReference,
    MediaAsset,
    ModelUnavailableError,
    PrepareModelsCommand,
    PrepareModelsResult,
    RemoveIndexCommand,
    ResourceNotFoundError,
    RuntimeReadiness,
    SearchCommand,
)
from vidxp.capabilities.actor.schemas import (
    ActorClusterSummary,
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
from vidxp.ports import IndexBackend, ModelRuntimePort
from vidxp.model_contracts import ModelArtifactUnavailableError
from vidxp.repository_layout import RepositoryLayout
from vidxp.settings import VidXPSettings
from vidxp.control_plane import ControlPlaneApplication
from vidxp.artifact_service import (
    ArtifactService,
)
from vidxp.media_service import (
    MediaService,
)


class VidXPApplication(ControlPlaneApplication):
    """The transport-neutral command and query boundary."""

    def __init__(
        self,
        *,
        settings: VidXPSettings,
        layout: RepositoryLayout,
        registry: CapabilityRegistry,
        runtime: ModelRuntimePort,
        index_backend: IndexBackend,
        media: MediaService,
        artifacts: ArtifactService,
        index_status: Callable[[], dict[str, Any] | None],
        completed_upload_importer: Callable[[str], MediaAsset] | None = None,
    ) -> None:
        self.registry = registry
        self.runtime = runtime
        self.index_backend = index_backend
        self._completed_upload_importer = completed_upload_importer
        super().__init__(
            layout=layout,
            capabilities=CapabilityService(registry),
            media=media,
            artifacts=artifacts,
            index_status=index_status,
        )
        self.settings = settings

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
        except ModelArtifactUnavailableError as exc:
            raise ModelUnavailableError(exc.capability) from exc
        except (ModuleNotFoundError, CapabilityDependencyError) as exc:
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
    def _active_config(self) -> IndexConfig:
        return self.index_backend.active_config(
            self.index_directory,
            device=self.device,
        )

    @application_boundary
    def import_media(self, command: ImportMediaCommand) -> MediaAsset:
        self.layout.ensure_local_directories()
        return self.media.import_local(command)

    @application_boundary
    def import_completed_upload(self, upload_id: str) -> MediaAsset:
        if self._completed_upload_importer is None:
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "Remote resumable uploads are not configured.",
            )
        return self._completed_upload_importer(upload_id)

    @application_boundary
    def runtime_readiness(self) -> RuntimeReadiness:
        components = (
            *self.control_plane_readiness(),
            self._index_storage_readiness(),
        )
        dependencies = self.check_dependencies(
            DependencyCheckCommand(modalities=self.registry.names())
        )
        return RuntimeReadiness(
            ready=(
                all(component.ready for component in components)
                and dependencies.ok
            ),
            runtime=self.runtime.backends,
            components=components,
            dependencies=dependencies,
        )

    def _index_storage_readiness(self) -> ComponentReadiness:
        try:
            self.index_backend.status(self.index_directory)
        except Exception:
            return ComponentReadiness(
                name="index_storage",
                ready=False,
                message="The committed index storage failed integrity checks.",
            )
        return ComponentReadiness(
            name="index_storage",
            ready=True,
            message="The committed index storage passed integrity checks.",
        )

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
        media = self.media.require_record(command.media_id)
        content = self.media.content(command.media_id)
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
        checks = self.registry.dependency_checks(
            selected,
            include_runtime_checks=command.include_runtime_checks,
        )
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
        definition, contract, handler = self._operation(
            capability,
            operation,
        )
        request = contract.input_model.model_validate(payload)
        config = None
        if contract.requires_index:
            config = self._active_config()
            self._require_indexed_capability(capability, config)

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
            else:
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

    def _operation(self, capability: str, operation: str):
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
        return definition, contract, handler

    @staticmethod
    def _require_indexed_capability(
        capability: str,
        config: IndexConfig,
    ) -> None:
        if capability not in config.enabled_modalities:
            raise CapabilityRequestError(
                f"The {capability} capability is not present in this index."
            )

    def _invoke_operation(
        self,
        capability: str,
        operation: str,
        payload: BaseModel | Mapping[str, Any],
        *,
        context: CapabilityContext,
    ) -> BaseModel:
        _, contract, handler = self._operation(capability, operation)
        if contract.requires_index:
            if context.config is None or context.storage is None:
                raise RuntimeError(
                    "Indexed capability execution requires a pinned store."
                )
            self._require_indexed_capability(capability, context.config)
        request = contract.input_model.model_validate(payload)
        response = handler(context, request)
        return contract.output_model.model_validate(response)

    def _config_for_snapshot(
        self,
        reference: IndexSnapshotReference,
    ) -> IndexConfig:
        return self.index_backend.config_for_snapshot(
            self.index_directory,
            snapshot_id=reference.snapshot_id,
            snapshot_sha256=reference.snapshot_sha256,
            device=self.device,
        )

    @application_boundary
    def search(
        self,
        command: SearchCommand,
        *,
        snapshot: IndexSnapshotReference | None = None,
    ) -> SearchResult:
        if snapshot is None:
            return cast(
                SearchResult,
                self.execute(
                    command.modality,
                    "search",
                    {"query": command.query, "top_k": command.top_k},
                ),
            )
        config = self._config_for_snapshot(snapshot)
        self._require_indexed_capability(command.modality, config)
        with self._capability_dependencies((command.modality,)):
            with self.index_backend.open_store(config) as storage:
                with self.runtime.scheduler.inference():
                    return cast(
                        SearchResult,
                        self._invoke_operation(
                            command.modality,
                            "search",
                            {
                                "query": command.query,
                                "top_k": command.top_k,
                            },
                            context=CapabilityContext(
                                config=config,
                                runtime=self.runtime,
                                storage=storage,
                            ),
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
    def actor_cluster(self, cluster_id: str) -> ActorClusterSummary:
        return cast(
            ActorClusterSummary,
            self.execute(
                "actor",
                "cluster",
                {"cluster_id": cluster_id},
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
        snapshot: IndexSnapshotReference | None = None,
        media_id: str | None = None,
        generation_id: str | None = None,
        execution: ExecutionContext | None = None,
    ) -> Artifact:
        active_execution = execution_context(execution)
        if (media_id is None) != (generation_id is None):
            raise ValueError(
                "Pinned actor media and generation identities must be "
                "provided together."
            )
        active_execution.report(
            {
                "stage": "resolving_detections",
                "message": "Resolving actor detections.",
            }
        )
        config = (
            self._config_for_snapshot(snapshot)
            if snapshot is not None
            else self._active_config()
        )
        self._require_indexed_capability("actor", config)
        detections = []
        with self._capability_dependencies(("actor",)):
            with self.index_backend.open_store(config) as storage:
                context = CapabilityContext(
                    config=config,
                    runtime=self.runtime,
                    storage=storage,
                )
                with self.runtime.scheduler.inference():
                    cluster = cast(
                        ActorClusterSummary,
                        self._invoke_operation(
                            "actor",
                            "cluster",
                            {"cluster_id": command.cluster_id},
                            context=context,
                        ),
                    )
                    cursor = None
                    while True:
                        active_execution.checkpoint()
                        page = cast(
                            ActorDetectionsOutput,
                            self._invoke_operation(
                                "actor",
                                "detections",
                                {
                                    "cluster_id": command.cluster_id,
                                    "page_size": 100,
                                    "cursor": cursor,
                                },
                                context=context,
                            ),
                        )
                        detections.extend(page.detections)
                        cursor = page.next_cursor
                        if cursor is None:
                            break
        identities = {
            (detection.media_id, detection.generation_id)
            for detection in detections
        }
        expected_identity = (cluster.media_id, cluster.generation_id)
        if (
            identities != {expected_identity}
            or (
                media_id is not None
                and expected_identity != (media_id, generation_id)
            )
        ):
            raise ApplicationError(
                "actor_cluster_identity_invalid",
                ErrorCategory.conflict,
                "The actor cluster does not match the requested index identity.",
            )
        return self.artifacts.create_actor_overlay(
            media_id=cluster.media_id,
            generation_id=cluster.generation_id,
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
        return self.artifacts.create_snippet(
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
