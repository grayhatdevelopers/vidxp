from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Generic, Literal, Mapping, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    field_validator,
    model_validator,
)

from vidxp.core.identifiers import (
    ArtifactId as ArtifactId,
    Identifier as Identifier,
    IndexGenerationId as IndexGenerationId,
    IndexSnapshotId as IndexSnapshotId,
    JobId as JobId,
    MediaId as MediaId,
    MimeType,
    RepositoryId as RepositoryId,
    Sha256,
    VideoId as VideoId,
)
from vidxp.core.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactKind,
    ArtifactState,
)
from vidxp.core.media import (
    MEDIA_SCHEMA_VERSION,
    MediaState,
    MediaStream,
    validate_display_filename,
)

T = TypeVar("T")


class ApplicationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class Page(ApplicationModel, Generic[T]):
    items: tuple[T, ...] = ()
    total: int = Field(ge=0)
    next_cursor: str | None = None


class RuntimeProfile(ApplicationModel):
    requested: str
    torch_device: str
    transcription_device: str
    actor_device: str = "cpu"
    mps_available: bool = False
    cuda_available: bool = False


class Principal(ApplicationModel):
    subject: str = Field(min_length=1, max_length=255)
    client_id: str | None = Field(default=None, min_length=1, max_length=255)
    scopes: frozenset[str] = Field(default_factory=frozenset)


class ErrorCategory(StrEnum):
    validation = "validation"
    authentication = "authentication"
    authorization = "authorization"
    not_found = "not_found"
    conflict = "conflict"
    unavailable = "unavailable"
    resource_limit = "resource_limit"
    cancelled = "cancelled"
    internal = "internal"


class JobKind(StrEnum):
    index = "index"
    snippet = "snippet"
    actor_overlay = "actor_overlay"
    prepare_models = "prepare_models"


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    recovery_exhausted = "recovery_exhausted"


class JobQueue(StrEnum):
    cpu = "cpu"
    gpu = "gpu"


class ErrorDetail(ApplicationModel):
    code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    category: ErrorCategory
    message: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    retryable: bool = False
    correlation_id: str | None = None


class ApplicationError(RuntimeError):
    def __init__(
        self,
        code: str,
        category: ErrorCategory,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
        correlation_id: str | None = None,
    ) -> None:
        self.detail = ErrorDetail(
            code=code,
            category=category,
            message=message,
            details=details or {},
            retryable=retryable,
            correlation_id=correlation_id,
        )
        super().__init__(message)

    @property
    def code(self) -> str:
        return self.detail.code

    @property
    def category(self) -> ErrorCategory:
        return self.detail.category

    @property
    def retryable(self) -> bool:
        return self.detail.retryable

    def to_dict(self) -> dict[str, Any]:
        return self.detail.model_dump(mode="json")


class DependencyUnavailableError(ApplicationError):
    def __init__(
        self,
        capabilities: tuple[str, ...],
        install_hint: str,
    ) -> None:
        label = capabilities[0] if len(capabilities) == 1 else "selected"
        super().__init__(
            "dependency_unavailable",
            ErrorCategory.unavailable,
            f"Dependencies for the {label} capability are unavailable. "
            f"{install_hint}",
            details={
                "capabilities": list(capabilities),
                "install_hint": install_hint,
            },
        )


class InvalidRequestError(ApplicationError):
    def __init__(
        self,
        *,
        errors: list[dict[str, JsonValue]] | None = None,
    ) -> None:
        super().__init__(
            "invalid_request",
            ErrorCategory.validation,
            "The request is invalid.",
            details={"errors": errors or []},
        )


class ResourceNotFoundError(ApplicationError):
    def __init__(self, resource: str) -> None:
        super().__init__(
            "resource_not_found",
            ErrorCategory.not_found,
            f"The requested {resource} was not found.",
            details={"resource": resource},
        )


class CapabilityProvenance(ApplicationModel):
    distribution: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    version: str | None = None


class DependencyKind(StrEnum):
    distribution = "distribution"
    runtime = "runtime"


class CapabilityDependencyCheck(ApplicationModel):
    capability: str = Field(min_length=1)
    provenance: CapabilityProvenance | None = None
    kind: DependencyKind
    name: str = Field(min_length=1)
    requirement: str | None = None
    installed_version: str | None = None
    ok: bool
    error: str | None = None


class CapabilityOperationInfo(ApplicationModel):
    name: str = Field(min_length=1)
    requires_index: bool
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]


class CapabilityInfo(ApplicationModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    install_extra: str = Field(min_length=1)
    supports_indexing: bool
    prepares_models: bool
    operations: tuple[CapabilityOperationInfo, ...] = ()
    provenance: CapabilityProvenance | None = None


class ComponentReadiness(ApplicationModel):
    name: str = Field(min_length=1)
    ready: bool
    message: str = Field(min_length=1)


class RuntimeReadiness(ApplicationModel):
    ready: bool
    runtime: RuntimeProfile | None
    components: tuple[ComponentReadiness, ...]
    dependencies: DependencyCheckResult | None


class ImportMediaCommand(ApplicationModel):
    """Local-adapter command; remote ingestion uses the upload workflow."""

    path: Path
    original_filename: str | None = Field(default=None, min_length=1)
    declared_mime_type: MimeType | None = None

    @field_validator("original_filename")
    @classmethod
    def _filename_only(cls, value: str | None) -> str | None:
        return None if value is None else validate_display_filename(value)


class MediaAsset(ApplicationModel):
    schema_version: Literal[MEDIA_SCHEMA_VERSION] = MEDIA_SCHEMA_VERSION
    media_id: MediaId
    video_id: VideoId
    original_filename: str = Field(min_length=1)
    sha256: Sha256
    byte_size: int = Field(gt=0)
    declared_mime_type: MimeType | None = None
    detected_mime_type: MimeType
    container: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0)
    streams: tuple[MediaStream, ...] = Field(min_length=1)
    state: MediaState
    created_at: AwareDatetime


class ListMediaCommand(ApplicationModel):
    page_size: int = Field(default=50, gt=0, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


class MediaPage(Page[MediaAsset]):
    pass


class CreateIndexCommand(ApplicationModel):
    media_id: MediaId
    modalities: tuple[str, ...]
    frame_stride: int = Field(default=1, gt=0)
    capability_options: Mapping[str, Mapping[str, JsonValue]] = Field(
        default_factory=dict
    )


class IndexResult(ApplicationModel):
    media_id: MediaId
    generation_id: IndexGenerationId
    snapshot_id: IndexSnapshotId
    active_media_count: int = Field(gt=0)
    record_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)


class RemoveIndexCommand(ApplicationModel):
    media_id: MediaId


class Artifact(ApplicationModel):
    schema_version: Literal[ARTIFACT_SCHEMA_VERSION] = (
        ARTIFACT_SCHEMA_VERSION
    )
    artifact_id: ArtifactId
    media_id: MediaId
    generation_id: IndexGenerationId | None = None
    job_id: JobId | None = None
    kind: ArtifactKind
    profile: str = Field(min_length=1)
    mime_type: MimeType
    byte_size: int = Field(gt=0)
    sha256: Sha256
    state: ArtifactState
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None


class ActorOverlayProfile(StrEnum):
    default = "default"


class CreateActorOverlayCommand(ApplicationModel):
    media_id: MediaId
    generation_id: IndexGenerationId
    cluster_id: str = Field(min_length=1)
    profile: ActorOverlayProfile = ActorOverlayProfile.default


class SnippetProfile(StrEnum):
    source = "source"
    compatible_mp4 = "compatible_mp4"


class CreateSnippetCommand(ApplicationModel):
    media_id: MediaId
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    profile: SnippetProfile = SnippetProfile.compatible_mp4

    def model_post_init(self, _context: Any) -> None:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")


class IndexStatus(ApplicationModel):
    schema_version: int = Field(ge=1)
    state: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    updated_at: AwareDatetime | None = None
    summary: "IndexStatusSummary | None" = None


class IndexStatusSummary(ApplicationModel):
    index_schema_version: int = Field(ge=1)
    snapshot_id: IndexSnapshotId
    media_count: int = Field(ge=0)
    media_ids: tuple[MediaId, ...] = ()
    modalities: tuple[str, ...] = ()


class SearchCommand(ApplicationModel):
    modality: str = Field(min_length=1)
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, gt=0, le=100)


class PrepareModelsCommand(ApplicationModel):
    modalities: tuple[str, ...]
    capability_options: Mapping[str, Mapping[str, JsonValue]] = Field(
        default_factory=dict
    )


class DependencyCheckCommand(ApplicationModel):
    modalities: tuple[str, ...]


class DependencyCheckResult(ApplicationModel):
    ok: bool
    modalities: tuple[str, ...]
    checks: tuple[CapabilityDependencyCheck, ...]


class PrepareModelsResult(ApplicationModel):
    prepared: tuple[str, ...]
    modalities: tuple[str, ...]
    runtime: RuntimeProfile


JOB_SCHEMA_VERSION = 1


class IndexJobRequest(ApplicationModel):
    kind: Literal[JobKind.index] = JobKind.index
    command: CreateIndexCommand


class SnippetJobRequest(ApplicationModel):
    kind: Literal[JobKind.snippet] = JobKind.snippet
    command: CreateSnippetCommand


class ActorOverlayJobRequest(ApplicationModel):
    kind: Literal[JobKind.actor_overlay] = JobKind.actor_overlay
    command: CreateActorOverlayCommand


class PrepareModelsJobRequest(ApplicationModel):
    kind: Literal[JobKind.prepare_models] = JobKind.prepare_models
    command: PrepareModelsCommand


JobRequest = Annotated[
    IndexJobRequest
    | SnippetJobRequest
    | ActorOverlayJobRequest
    | PrepareModelsJobRequest,
    Field(discriminator="kind"),
]


class IndexJobResult(ApplicationModel):
    kind: Literal[JobKind.index] = JobKind.index
    result: IndexResult


class ArtifactJobResult(ApplicationModel):
    kind: Literal[JobKind.snippet, JobKind.actor_overlay]
    result: Artifact


class PrepareModelsJobResult(ApplicationModel):
    kind: Literal[JobKind.prepare_models] = JobKind.prepare_models
    result: PrepareModelsResult


JobResult = Annotated[
    IndexJobResult | ArtifactJobResult | PrepareModelsJobResult,
    Field(discriminator="kind"),
]


class JobProgress(ApplicationModel):
    schema_version: Literal[JOB_SCHEMA_VERSION] = JOB_SCHEMA_VERSION
    stage: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, gt=0)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_position(self) -> "JobProgress":
        if (
            self.current is not None
            and self.total is not None
            and self.current > self.total
        ):
            raise ValueError("current must not exceed total")
        return self


class Job(ApplicationModel):
    schema_version: Literal[JOB_SCHEMA_VERSION] = JOB_SCHEMA_VERSION
    job_id: JobId
    kind: JobKind
    state: JobState
    queue: JobQueue
    progress: JobProgress | None = None
    result: JobResult | None = None
    error: ErrorDetail | None = None
    recovery_attempts: int = Field(default=0, ge=0)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_terminal_payload(self) -> "Job":
        if self.state == JobState.succeeded:
            if self.result is None or self.error is not None:
                raise ValueError(
                    "succeeded jobs require a result and no error"
                )
            if self.result.kind != self.kind:
                raise ValueError("job result kind must match job kind")
        elif self.result is not None:
            raise ValueError("only succeeded jobs may contain a result")
        if self.state in {JobState.queued, JobState.running}:
            if self.error is not None:
                raise ValueError("active jobs may not contain an error")
        elif self.state in {JobState.failed, JobState.recovery_exhausted}:
            if self.error is None:
                raise ValueError("failed jobs require a typed error")
        return self


class ListJobsCommand(ApplicationModel):
    page_size: int = Field(default=50, gt=0, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


class JobPage(ApplicationModel):
    items: tuple[Job, ...] = ()
    next_cursor: str | None = None
