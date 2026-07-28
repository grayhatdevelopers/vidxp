from __future__ import annotations

from pathlib import Path
from typing import (
    Any,
    Callable,
    ContextManager,
    Iterable,
    Mapping,
    Protocol,
    runtime_checkable,
)

from vidxp.application_models import RuntimeProfile
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
)
from vidxp.core.indexing_common import ProgressCallback
from vidxp.model_contracts import ArtifactSpec, ModelKey, ModelSpec


class ResourceLimitError(RuntimeError):
    """Raised when configured host capacity cannot admit model work."""


@runtime_checkable
class ResourceSchedulerPort(Protocol):
    def indexing(self): ...

    def inference(self): ...


@runtime_checkable
class ModelRuntimePort(Protocol):
    backends: RuntimeProfile
    scheduler: ResourceSchedulerPort

    @property
    def model_cache(self) -> Path: ...

    @property
    def cpu_thread_budget(self) -> int: ...

    def device_for(self, capability: str) -> str: ...

    def get_or_load(
        self,
        key: ModelKey,
        loader: Callable[[], Any],
    ) -> Any: ...

    def resolve_model(self, spec: ModelSpec) -> Path: ...

    def resolve_artifact(self, spec: ArtifactSpec) -> Path: ...

    def record_compute_precision(
        self,
        capability: str,
        precision: str,
    ) -> None: ...

    def describe(self) -> dict[str, Any]: ...


@runtime_checkable
class IndexReader(Protocol):
    """Read-only vector records available to application queries."""

    def size_bytes(self) -> int: ...

    def query(
        self,
        modality: str,
        embedding: list[float],
        *,
        top_k: int,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class IndexStore(IndexReader, Protocol):
    """Mutable vector records available only to indexing infrastructure."""

    def clear(self, modalities: Iterable[str] | None = None) -> None: ...

    def delete_video(self, modality: str, video_id: str) -> None: ...

    def delete_generation(
        self,
        generation_id: str,
        modalities: Iterable[str] | None = None,
    ) -> None: ...

    def delete_records(
        self,
        modality: str,
        *,
        video_id: str,
        filters: Mapping[str, Any] | None = None,
    ) -> None: ...

    def upsert(
        self,
        modality: str,
        records: list[StorageRecord],
        *,
        batch_size: int,
        cancellation: CancellationToken,
    ) -> int: ...

    def records(
        self,
        modality: str,
        *,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...


class IndexBackend(Protocol):
    """Infrastructure operations needed by the application layer."""

    def status(self, index_directory: Path) -> dict[str, Any] | None: ...

    def active_config(
        self,
        index_directory: Path,
        *,
        device: str,
    ) -> tuple[IndexConfig, dict[str, Any]]: ...

    def create(
        self,
        path: Path,
        *,
        config: IndexConfig,
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
        source_name: str | None,
    ) -> dict[str, Any]: ...

    def indexing_in_progress(self, config: IndexConfig) -> bool: ...

    def open_store(self, config: IndexConfig) -> ContextManager[IndexReader]: ...

    def remove(self, config: IndexConfig, media_id: str) -> bool: ...

    def clear(self, config: IndexConfig) -> bool: ...
