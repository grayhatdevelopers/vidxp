from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from functools import wraps
from typing import Any, Callable, Iterator, Mapping, cast

from pydantic import BaseModel, JsonValue, ValidationError

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    DependencyCheckCommand,
    DependencyCheckResult,
    DependencyUnavailableError,
    ErrorCategory,
    InvalidRequestError,
    IndexResult,
    IndexStatus,
    PrepareModelsCommand,
    PrepareModelsResult,
    ResourceNotFoundError,
    SearchCommand,
)
from vidxp.capabilities.actor.schemas import (
    ActorClusterSummary,
    ActorDetection,
    ActorRenderResult,
)
from vidxp.capabilities.actor.results import ActorClusterNotFoundError
from vidxp.capabilities.contracts import (
    CapabilityDependencyError,
    CapabilityContext,
    CapabilityRequestError,
    PreparationContext,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capabilities.schemas import SearchResult
from vidxp.core.contracts import (
    CancellationToken,
    IndexCancelledError,
    IndexConfig,
    IndexSchemaError,
)
from vidxp.core.indexing_common import ProgressCallback
from vidxp.index_state import (
    INDEX_STATUS_SCHEMA,
    IndexingInProgressError,
    IndexNotReadyError,
)
from vidxp.ports import IndexBackend, ModelRuntimePort, ResourceLimitError
from vidxp.model_contracts import ModelArtifactUnavailableError
from vidxp.repository_layout import RepositoryLayout
from vidxp.settings import VidXPSettings


def _validation_details(
    exc: ValidationError,
) -> list[dict[str, JsonValue]]:
    return [
        {
            "type": item["type"],
            "location": [str(part) for part in item["loc"]],
            "message": item["msg"],
        }
        for item in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    ]


def application_boundary(handler: Callable) -> Callable:
    """Translate expected domain failures once for every transport."""

    @wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return handler(*args, **kwargs)
        except ApplicationError:
            raise
        except ActorClusterNotFoundError as exc:
            raise ApplicationError(
                "actor_cluster_not_found",
                ErrorCategory.not_found,
                "The requested actor cluster was not found.",
            ) from exc
        except IndexingInProgressError as exc:
            raise ApplicationError(
                "indexing_in_progress",
                ErrorCategory.conflict,
                "An indexing operation is already in progress.",
                retryable=True,
            ) from exc
        except IndexNotReadyError as exc:
            raise ApplicationError(
                "index_not_ready",
                ErrorCategory.conflict,
                "The index is not ready.",
                retryable=True,
            ) from exc
        except IndexSchemaError as exc:
            raise ApplicationError(
                "index_schema_incompatible",
                ErrorCategory.conflict,
                "The index schema is incompatible with this version.",
            ) from exc
        except IndexCancelledError as exc:
            raise ApplicationError(
                "operation_cancelled",
                ErrorCategory.cancelled,
                "The operation was cancelled.",
            ) from exc
        except ResourceLimitError as exc:
            raise ApplicationError(
                "resource_limit",
                ErrorCategory.resource_limit,
                "The worker does not currently have enough host capacity.",
                retryable=True,
            ) from exc
        except ValidationError as exc:
            raise InvalidRequestError(
                errors=_validation_details(exc),
            ) from exc
        except CapabilityRequestError as exc:
            raise InvalidRequestError() from exc

    return wrapped


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
    ) -> None:
        self.settings = settings
        self.layout = layout
        self.registry = registry
        self.runtime = runtime
        self.index_backend = index_backend

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
        payload.update(
            {
                "repository_root": self.layout.root,
                "index_directory": self.index_directory,
            }
        )
        return IndexStatus.model_validate(payload)

    @application_boundary
    def active_config(self) -> tuple[IndexConfig, dict[str, Any]]:
        return self.index_backend.active_config(
            self.index_directory,
            device=self.device,
        )

    @application_boundary
    def create_index(
        self,
        command: CreateIndexCommand,
        *,
        progress_callback: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
    ) -> IndexResult:
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
        if not command.path.is_file():
            raise ResourceNotFoundError("media")
        self.layout.ensure_local_directories()
        config = IndexConfig.local(
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
                return IndexResult(
                    summary=self.index_backend.create(
                        command.path,
                        config=config,
                        progress=progress_callback,
                        cancellation=cancellation,
                        source_name=command.source_name,
                    )
                )

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
        progress_callback: ProgressCallback | None = None,
    ) -> PrepareModelsResult:
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
                    executor = self.registry.executor(name)
                    if executor.prepare is not None:
                        prepared.extend(
                            executor.prepare(
                                PreparationContext(
                                    runtime=self.runtime,
                                    settings=self.registry.get(name)
                                    .config_model.model_validate(options[name]),
                                ),
                                progress_callback,
                            )
                        )
        return PrepareModelsResult(
            prepared=tuple(prepared),
            modalities=selected,
            runtime=self.runtime.describe(),
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
            config, _ = self.active_config()
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
    def actor_clusters(self) -> tuple[ActorClusterSummary, ...]:
        result = self.execute("actor", "clusters", {})
        return tuple(result.clusters)

    @application_boundary
    def actor_detections(self, cluster_id: str) -> list[ActorDetection]:
        result = self.execute(
            "actor",
            "detections",
            {"cluster_id": cluster_id},
        )
        return list(result.detections)

    @application_boundary
    def render_actor(
        self,
        cluster_id: str,
        input_path: str | Path,
        output_path: str | Path,
    ) -> ActorRenderResult:
        return cast(
            ActorRenderResult,
            self.execute(
                "actor",
                "render",
                {
                    "cluster_id": cluster_id,
                    "input_path": input_path,
                    "output_path": output_path,
                },
            ),
        )

    @application_boundary
    def clear_index(self) -> bool:
        if not self.index_directory.exists():
            return False
        base_config = self._base_config()
        if self.index_backend.indexing_in_progress(base_config):
            raise IndexingInProgressError(
                f"Indexing is active for {self.index_directory}."
            )
        status = self.index_backend.status(self.index_directory)
        if status is not None and status.get("state") == "ready":
            try:
                config, _ = self.index_backend.active_config(
                    self.index_directory,
                    device=self.device,
                )
            except (IndexSchemaError, KeyError, TypeError, ValueError):
                config = base_config
        else:
            config = base_config
        try:
            self.index_backend.clear(config)
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                self.registry.index_names(),
                self.registry.install_hint(self.registry.index_names()),
            ) from exc

        return True

    def _base_config(self) -> IndexConfig:
        return IndexConfig.local(
            storage_directory=self.index_directory,
            enabled_modalities=self.registry.index_names(),
            collection_names=self.registry.collection_names(),
            device=self.device,
        )
