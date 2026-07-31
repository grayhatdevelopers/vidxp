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

from vidxp.application_models import (
    DraftAnswer,
    Job,
    JobPage,
    JobQueue,
    JobRequest,
    ListJobsCommand,
    QueryModelIdentity,
    QueryPlan,
    QueryPlanningRequest,
    QuerySynthesisRequest,
    RuntimeProfile,
)
from vidxp.core.artifacts import (
    ArtifactRecord,
    StagedArtifact,
    StoredArtifact,
)
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
)
from vidxp.core.indexing_common import ProgressCallback
from vidxp.core.media import (
    MediaProbe,
    MediaRecord,
    StagedMedia,
    StoredMedia,
)
from vidxp.model_contracts import ArtifactSpec, ModelKey, ModelSpec


class InvalidJobBackendRequestError(ValueError):
    """Raised when a durable job identifier or cursor is malformed."""


class JobIdempotencyConflictError(RuntimeError):
    """Raised when one workflow ID is reused for a different request."""


class QueryProviderError(RuntimeError):
    """Raised when the optional language-model provider cannot respond."""


@runtime_checkable
class QueryModelPort(Protocol):
    @property
    def identity(self) -> QueryModelIdentity: ...

    def plan(self, request: QueryPlanningRequest) -> QueryPlan: ...

    def synthesize(self, request: QuerySynthesisRequest) -> DraftAnswer: ...


class LocalFileResource:
    """Authorized local delivery handle; never serialize this object."""

    __slots__ = ("path", "filename", "mime_type", "byte_size", "etag")

    def __init__(
        self,
        *,
        path: Path,
        filename: str,
        mime_type: str,
        byte_size: int,
        etag: str,
    ) -> None:
        self.path = path
        self.filename = filename
        self.mime_type = mime_type
        self.byte_size = byte_size
        self.etag = etag


@runtime_checkable
class MediaCatalogPort(Protocol):
    def get_media(self, media_id: str) -> MediaRecord | None: ...

    def get_media_by_checksum(self, sha256: str) -> MediaRecord | None: ...

    def put_media(self, record: MediaRecord) -> MediaRecord: ...

    def list_media(
        self,
        *,
        limit: int,
        offset: int = 0,
    ) -> tuple[MediaRecord, ...]: ...

    def count_media(self) -> int: ...

    def reserve_media_import(
        self,
        request_key: str,
        request_fingerprint: str,
    ) -> MediaRecord | None: ...

    def complete_media_import(
        self,
        request_key: str,
        request_fingerprint: str,
        record: MediaRecord,
    ) -> None: ...


@runtime_checkable
class MediaStorePort(Protocol):
    def stage_local(self, path: Path) -> StagedMedia: ...

    def publication_lock(self, sha256: str) -> ContextManager[None]: ...

    def publish(self, staged: StagedMedia) -> StoredMedia: ...

    def discard(self, staged: StagedMedia) -> None: ...

    def delete(self, storage_key: str) -> None: ...

    def verify(
        self,
        storage_key: str,
        *,
        sha256: str,
        byte_size: int,
    ) -> Path: ...

    def resolve(self, storage_key: str) -> Path: ...


@runtime_checkable
class MediaProbePort(Protocol):
    def probe(self, path: Path) -> MediaProbe: ...


@runtime_checkable
class ArtifactCatalogPort(Protocol):
    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None: ...

    def get_artifact_by_request(
        self,
        request_key: str,
    ) -> ArtifactRecord | None: ...

    def invalidate_artifact_request(
        self,
        request_key: str,
        artifact_id: str,
    ) -> None: ...

    def put_artifact(self, record: ArtifactRecord) -> ArtifactRecord: ...


@runtime_checkable
class ArtifactStorePort(Protocol):
    def stage(self, artifact_id: str, *, suffix: str) -> StagedArtifact: ...

    def recover(
        self,
        artifact_id: str,
        *,
        suffix: str,
    ) -> StoredArtifact | None: ...

    def publish(self, staged: StagedArtifact) -> StoredArtifact: ...

    def discard(self, staged: StagedArtifact) -> None: ...

    def delete(self, storage_key: str) -> None: ...

    def verify(
        self,
        storage_key: str,
        *,
        sha256: str,
        byte_size: int,
    ) -> Path: ...

    def resolve(self, storage_key: str) -> Path: ...


@runtime_checkable
class ActorRendererPort(Protocol):
    def render(
        self,
        source: Path,
        destination: Path,
        cluster_id: str,
        detections: list[dict[str, Any]],
        *,
        cancellation: CancellationToken,
        progress: ProgressCallback | None,
    ) -> None: ...


@runtime_checkable
class SnippetRendererPort(Protocol):
    def render(
        self,
        source: Path,
        destination: Path,
        *,
        start_seconds: float,
        end_seconds: float,
        compatible_mp4: bool,
        cancellation: CancellationToken,
        progress: ProgressCallback | None,
    ) -> None: ...


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

    def resolve_model(
        self,
        spec: ModelSpec,
        *,
        download: bool = False,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path: ...

    def resolve_artifact(
        self,
        spec: ArtifactSpec,
        *,
        download: bool = False,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path: ...

    def record_compute_precision(
        self,
        capability: str,
        precision: str,
    ) -> None: ...

    def describe(self) -> dict[str, Any]: ...


@runtime_checkable
class IndexReader(Protocol):
    """Read-only vector records available to application queries."""

    def size_bytes(self) -> int | None: ...

    def query(
        self,
        modality: str,
        embedding: list[float],
        *,
        top_k: int,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def records(
        self,
        modality: str,
        *,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    def count_records(
        self,
        modality: str,
        *,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> int: ...


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

class IndexBackend(Protocol):
    """Infrastructure operations needed by the application layer."""

    def status(self, index_directory: Path) -> dict[str, Any] | None: ...

    def active_config(
        self,
        index_directory: Path,
        *,
        device: str,
    ) -> IndexConfig: ...

    def config_for_snapshot(
        self,
        index_directory: Path,
        *,
        snapshot_id: str,
        snapshot_sha256: str,
        device: str,
    ) -> IndexConfig: ...

    def create(
        self,
        path: Path,
        *,
        config: IndexConfig,
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
        source_name: str | None,
        source_checksum: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]: ...

    def indexing_in_progress(self, config: IndexConfig) -> bool: ...

    def open_store(self, config: IndexConfig) -> ContextManager[IndexReader]: ...

    def remove(self, config: IndexConfig, media_id: str) -> bool: ...

    def clear(self, config: IndexConfig) -> bool: ...


class JobBackend(Protocol):
    """Durable lifecycle operations owned by the workflow engine."""

    def start(self) -> None: ...

    def submit(
        self,
        request: JobRequest,
        *,
        queue: JobQueue,
        job_id: str | None = None,
    ) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def list(self, command: ListJobsCommand) -> JobPage: ...

    def cancel(self, job_id: str) -> Job | None: ...

    def retry(
        self,
        job_id: str,
        *,
        retry_id: str | None = None,
    ) -> Job | None: ...

    def health(self) -> None: ...

    def stop_worker(self) -> bool: ...

    def close(self) -> None: ...
