from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

from pydantic import BaseModel

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    DependencyCheckCommand,
    DependencyCheckResult,
    DependencyUnavailableError,
    ErrorCategory,
    IndexResult,
    IndexStatus,
    PrepareModelsCommand,
    PrepareModelsResult,
    SearchCommand,
)
from vidxp.capabilities.actor.schemas import (
    ActorClusterSummary,
    ActorDetection,
    ActorRenderResult,
)
from vidxp.capabilities.contracts import (
    CapabilityContext,
    PreparationContext,
    capability_install_hint,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capabilities.schemas import SearchResult
from vidxp.core.contracts import CancellationToken, IndexConfig, IndexSchemaError
from vidxp.core.indexing_common import ProgressCallback
from vidxp.core.manifest import (
    CHECKPOINT_DIRECTORY,
    COMPLETION_FILE,
    FAILURES_FILE,
    MANIFEST_FILE,
    TIMINGS_FILE,
)
from vidxp.index_state import (
    INDEX_STATUS_FILE,
    INDEX_STATUS_SCHEMA,
    IndexingInProgressError,
)
from vidxp.ports import IndexBackend
from vidxp.repository_layout import RepositoryLayout
from vidxp.runtime import ModelRuntime


class VidXPApplication:
    """The transport-neutral command and query boundary."""

    def __init__(
        self,
        *,
        layout: RepositoryLayout,
        registry: CapabilityRegistry,
        runtime: ModelRuntime,
        index_backend: IndexBackend,
    ) -> None:
        self.layout = layout
        self.registry = registry
        self.runtime = runtime
        self.index_backend = index_backend

    @property
    def index_directory(self) -> Path:
        return self.layout.local_index

    @property
    def device(self) -> str:
        return self.runtime.backends.torch_device

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

    def active_config(self) -> tuple[IndexConfig, dict[str, Any]]:
        return self.index_backend.active_config(
            self.index_directory,
            device=self.device,
        )

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
            raise ValueError(
                "These capabilities do not support indexing: "
                + ", ".join(non_indexable)
            )
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
        return IndexResult(
            summary=self.index_backend.create(
                command.path,
                config=config,
                progress=progress_callback,
                cancellation=cancellation,
                source_name=command.source_name,
            )
        )

    def indexing_in_progress(self) -> bool:
        return self.index_backend.indexing_in_progress(
            self._base_config()
        )

    def check_dependencies(
        self,
        command: DependencyCheckCommand,
    ) -> DependencyCheckResult:
        selected = self.registry.validate_names(command.modalities)
        checks = self.registry.dependency_checks(selected)
        return DependencyCheckResult(
            ok=all(check["ok"] for check in checks),
            modalities=selected,
            checks=checks,
        )

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
        failures = [check for check in checks if not check["ok"]]
        if failures:
            details = "; ".join(
                f"{check['name']}: {check['error']}" for check in failures
            )
            extras = ",".join(self.registry.get(name).extra for name in selected)
            raise ApplicationError(
                "capability_dependencies_unavailable",
                ErrorCategory.unavailable,
                f"{details}. {capability_install_hint(extras)}",
                details={"failures": failures},
            )

        prepared: list[str] = []
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
            raise ValueError(
                f"Capability {capability!r} has no operation {operation!r}. "
                f"Available operations: {available}."
            ) from exc

        config = None
        if contract.requires_index:
            config, _ = self.active_config()
            if capability not in config.enabled_modalities:
                raise ValueError(
                    f"The {capability} capability is not present in this index."
                )
        request = contract.input_model.model_validate(payload)
        try:
            response = handler(
                CapabilityContext(config=config, runtime=self.runtime),
                request,
            )
        except ModuleNotFoundError as exc:
            dependency = exc.name or "optional dependency"
            raise DependencyUnavailableError(
                dependency,
                capability_install_hint(definition.extra),
            ) from exc
        return contract.output_model.model_validate(response)

    def search(self, command: SearchCommand) -> SearchResult:
        return cast(
            SearchResult,
            self.execute(
                command.modality,
                "search",
                {"query": command.query, "top_k": command.top_k},
            ),
        )

    def actor_clusters(self) -> tuple[ActorClusterSummary, ...]:
        result = self.execute("actor", "clusters", {})
        return tuple(result.clusters)

    def actor_detections(self, cluster_id: str) -> list[ActorDetection]:
        result = self.execute(
            "actor",
            "detections",
            {"cluster_id": cluster_id},
        )
        return list(result.detections)

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
                config, _ = self.active_config()
            except (IndexSchemaError, KeyError, TypeError, ValueError):
                config = base_config
        else:
            config = base_config
        try:
            self.index_backend.clear(config)
        except ModuleNotFoundError as exc:
            dependency = exc.name or "optional storage dependency"
            raise DependencyUnavailableError(
                dependency,
                capability_install_hint("storage"),
            ) from exc

        for name in (
            INDEX_STATUS_FILE,
            MANIFEST_FILE,
            TIMINGS_FILE,
            FAILURES_FILE,
            COMPLETION_FILE,
        ):
            (self.index_directory / name).unlink(missing_ok=True)
        checkpoint_directory = self.index_directory / CHECKPOINT_DIRECTORY
        if checkpoint_directory.is_dir():
            for checkpoint in checkpoint_directory.glob("*.json"):
                checkpoint.unlink()
            try:
                checkpoint_directory.rmdir()
            except OSError:
                pass
        return True

    def _base_config(self) -> IndexConfig:
        return IndexConfig.local(
            storage_directory=self.index_directory,
            enabled_modalities=self.registry.index_names(),
            collection_names=self.registry.collection_names(),
            device=self.device,
        )


VidXPService = VidXPApplication
