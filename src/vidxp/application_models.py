from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Generic, Literal, Mapping, TypeAlias, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from vidxp.core.identifiers import (
    ActorClusterId as ActorClusterId,
    ArtifactId as ArtifactId,
    Identifier as Identifier,
    IndexGenerationId as IndexGenerationId,
    IndexSnapshotId as IndexSnapshotId,
    JobId as JobId,
    MediaId as MediaId,
    MimeType,
    Sha256,
    UploadIntentId as UploadIntentId,
    UploadSessionId as UploadSessionId,
    VideoId as VideoId,
)
from vidxp.core.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactKind,
    ArtifactState,
)
from vidxp.core.contracts import INDEX_SCHEMA_VERSION
from vidxp.core.media import (
    MEDIA_SCHEMA_VERSION,
    MediaState,
    MediaStream,
    validate_display_filename,
)
from vidxp.core.uploads import (
    UploadSessionState,
    UploadState,
    UploadTransferBackend,
)
from vidxp.index_state import INDEX_STATUS_MEDIA_ID_LIMIT

T = TypeVar("T")
SearchQuery: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4096),
]


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
    media_import = "media_import"
    index = "index"
    search = "search"
    query = "query"
    snippet = "snippet"
    actor_overlay = "actor_overlay"
    evidence_board = "evidence_board"
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
            f"Dependencies for the {label} capability are unavailable. {install_hint}",
            details={
                "capabilities": list(capabilities),
                "install_hint": install_hint,
            },
        )


class ModelUnavailableError(ApplicationError):
    def __init__(self, capability: str) -> None:
        modality = capability.split(".", 1)[0]
        super().__init__(
            "model_unavailable",
            ErrorCategory.unavailable,
            f"Model artifacts for the {capability} capability are not "
            "available locally. Run "
            f"`vidxp prepare --modalities {modality}` before retrying.",
            details={
                "capability": capability,
                "remediation": (f"vidxp prepare --modalities {modality}"),
            },
        )


class ModelDownloadError(ApplicationError):
    def __init__(
        self,
        capability: str,
        model_id: str,
        *,
        attempts: int,
        reason: str,
        resumable: bool,
        retryable: bool,
    ) -> None:
        modality = capability.split(".", 1)[0]
        remediation = f"vidxp prepare --modalities {modality}"
        retry_message = (
            "Partial files were kept and the next preparation attempt will resume them."
            if resumable
            else "The next preparation attempt will restart this file."
        )
        super().__init__(
            "model_download_failed",
            ErrorCategory.unavailable,
            f"Downloading {model_id} failed after {attempts} attempt(s) "
            f"({reason}). {retry_message} Run "
            f"`{remediation}` again when the connection is available.",
            details={
                "capability": capability,
                "model": model_id,
                "attempts": attempts,
                "reason": reason,
                "partial_files_preserved": resumable,
                "remediation": remediation,
            },
            retryable=retryable,
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
    model = "model"


class CapabilityDependencyCheck(ApplicationModel):
    capability: str = Field(min_length=1)
    provenance: CapabilityProvenance | None = None
    kind: DependencyKind
    name: str = Field(min_length=1)
    requirement: str | None = None
    installed_version: str | None = None
    download_size_bytes: int | None = Field(default=None, ge=1)
    ok: bool
    error: str | None = None


class CapabilityOperationInfo(ApplicationModel):
    name: str = Field(min_length=1)
    requires_index: bool
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]


class CapabilityRole(StrEnum):
    searchable = "searchable"
    queryable = "queryable"
    inspectable = "inspectable"
    renderable = "renderable"


class CapabilityIdentityMode(StrEnum):
    not_applicable = "not_applicable"
    anonymous_clusters = "anonymous_clusters"
    registered_entities = "registered_entities"


class CapabilitySummary(ApplicationModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    install_extra: str = Field(min_length=1)
    supports_indexing: bool
    prepares_models: bool
    roles: tuple[CapabilityRole, ...] = ()
    identity_mode: CapabilityIdentityMode = CapabilityIdentityMode.not_applicable
    provenance: CapabilityProvenance | None = None


class CapabilityInfo(CapabilitySummary):
    operations: tuple[CapabilityOperationInfo, ...] = ()


class CapabilityList(ApplicationModel):
    items: tuple[CapabilitySummary, ...] = ()


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
    media_id: MediaId = Field(
        description=(
            "Stable registered-media identifier used by indexing and optional "
            "single-media search/query filters."
        )
    )
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
    page_size: int = Field(
        default=50,
        gt=0,
        le=100,
        description="Maximum registered media records to return.",
    )
    cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Opaque next_cursor from the previous list_media page.",
    )


class MediaPage(Page[MediaAsset]):
    pass


class CreateUploadIntentCommand(ApplicationModel):
    original_filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    declared_mime_type: MimeType | None = None

    @field_validator("original_filename")
    @classmethod
    def _filename_only(cls, value: str) -> str:
        return validate_display_filename(value)


class UploadIntent(ApplicationModel):
    intent_id: UploadIntentId
    original_filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    declared_mime_type: MimeType | None = None
    state: UploadState
    created_at: AwareDatetime
    expires_at: AwareDatetime
    job_id: JobId | None = None
    media_id: MediaId | None = None


class MediaUploadStatus(ApplicationModel):
    """Actionable public projection of the shared upload intent state."""

    intent_id: UploadIntentId
    client_file_key: str = Field(min_length=1, max_length=255)
    state: UploadState
    original_filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(ge=0)
    declared_mime_type: MimeType | None = None
    expires_at: AwareDatetime
    phase: Literal[
        "transferring",
        "uploaded",
        "importing",
        "registered",
        "indexing",
        "index_failed",
        "indexed",
        "failed",
    ] = "transferring"
    transport: UploadTransferBackend = UploadTransferBackend.tus
    resumable: bool = True
    job_id: JobId | None = None
    import_job_id: JobId | None = None
    index_job_id: JobId | None = None
    media_id: MediaId | None = None
    generation_id: IndexGenerationId | None = None
    snapshot_id: IndexSnapshotId | None = None
    searchable: bool = False
    index_after_import: bool = True
    index_modalities: tuple[str, ...] = ()
    error: ErrorDetail | None = None
    terminal: bool = False
    poll_after_seconds: int = Field(default=2, ge=0, le=60)
    status: str = Field(min_length=1, max_length=512)
    next_action: str = Field(min_length=1, max_length=1024)


class CreateUploadFileCommand(ApplicationModel):
    client_file_key: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9._~-]+$",
    )
    original_filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    declared_mime_type: MimeType | None = None

    @field_validator("original_filename")
    @classmethod
    def _filename_only(cls, value: str) -> str:
        return validate_display_filename(value)


class MediaIngestionOptions(ApplicationModel):
    index_after_import: bool = True
    modalities: tuple[Identifier, ...] | None = Field(
        default=None,
        description=(
            "Indexable capabilities to run after registration. Omit to use "
            "the repository runtime's complete indexable capability set."
        ),
    )

    @field_validator("modalities")
    @classmethod
    def _unique_modalities(
        cls,
        values: tuple[Identifier, ...] | None,
    ) -> tuple[Identifier, ...] | None:
        if values is not None and len(values) != len(set(values)):
            raise ValueError("Ingestion modalities must be unique.")
        return values


class LocalMediaIngestionCommand(MediaIngestionOptions):
    paths: tuple[str, ...] = Field(min_length=1, max_length=10)

    @field_validator("paths")
    @classmethod
    def _nonempty_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if any(not value for value in cleaned):
            raise ValueError("Local media paths must not be empty.")
        return cleaned


class MediaUploadSessionStatus(ApplicationModel):
    session_id: UploadSessionId
    session_state: UploadSessionState
    aggregate_state: Literal[
        "empty",
        "uploading",
        "processing",
        "ready",
        "index_failed",
        "partial_index_failure",
        "partial_failure",
        "failed",
    ]
    transfer_backend: UploadTransferBackend = UploadTransferBackend.tus
    resumable: bool = True
    index_after_import: bool = True
    index_modalities: tuple[str, ...] = ()
    expires_at: AwareDatetime
    maximum_files: int = Field(gt=0)
    maximum_file_bytes: int = Field(gt=0)
    maximum_aggregate_bytes: int = Field(gt=0)
    file_count: NonNegativeInt
    total_bytes: NonNegativeInt
    reserved_file_count: NonNegativeInt
    reserved_bytes: NonNegativeInt
    uploaded_file_count: NonNegativeInt
    uploaded_bytes: NonNegativeInt
    ready_file_count: NonNegativeInt
    searchable_file_count: NonNegativeInt = 0
    failed_file_count: NonNegativeInt
    index_failed_file_count: NonNegativeInt = 0
    items: tuple[MediaUploadStatus, ...] = ()
    terminal: bool = False
    poll_after_seconds: int = Field(default=2, ge=0, le=60)
    status: str = Field(min_length=1, max_length=512)
    next_action: str = Field(min_length=1, max_length=1024)


class CreateIndexCommand(ApplicationModel):
    media_id: MediaId = Field(
        description=(
            "Stable identifier returned by list_media, get_media, or a "
            "completed upload."
        )
    )
    modalities: tuple[str, ...]
    frame_stride: int = Field(
        default=1,
        gt=0,
        description=(
            "Materialize every Nth frame for actor and legacy visual indexing."
        ),
    )
    scene_sample_fps: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Target scene samples per second. Sources below this rate use "
            "every available frame without duplication."
        ),
    )
    capability_options: Mapping[str, Mapping[str, JsonValue]] = Field(
        default_factory=dict
    )

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_scene_sampling(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        raw_options = payload.get("capability_options")
        if not isinstance(raw_options, Mapping):
            return payload
        raw_scene = raw_options.get("scene")
        if not isinstance(raw_scene, Mapping) or "sample_fps" not in raw_scene:
            return payload

        nested_value = raw_scene["sample_fps"]
        explicit_value = payload.get("scene_sample_fps")
        if explicit_value is not None:
            try:
                conflicts = float(explicit_value) != float(nested_value)
            except (TypeError, ValueError):
                conflicts = True
            if conflicts:
                raise ValueError(
                    "scene_sample_fps conflicts with "
                    "capability_options.scene.sample_fps."
                )
        else:
            payload["scene_sample_fps"] = nested_value

        options = dict(raw_options)
        scene = dict(raw_scene)
        scene.pop("sample_fps")
        if scene:
            options["scene"] = scene
        else:
            options.pop("scene", None)
        payload["capability_options"] = options
        return payload

    @model_validator(mode="after")
    def _scene_sampling_requires_scene(self) -> "CreateIndexCommand":
        if self.scene_sample_fps is not None and "scene" not in self.modalities:
            raise ValueError("scene_sample_fps requires the scene modality.")
        return self


class IndexResult(ApplicationModel):
    media_id: MediaId
    generation_id: IndexGenerationId
    snapshot_id: IndexSnapshotId
    active_media_count: int = Field(gt=0)
    record_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)


class RemoveIndexCommand(ApplicationModel):
    media_id: MediaId


class Artifact(ApplicationModel):
    schema_version: Literal[ARTIFACT_SCHEMA_VERSION] = ARTIFACT_SCHEMA_VERSION
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


class ArtifactDeliveryMode(StrEnum):
    local_file = "local_file"
    https_download = "https_download"
    mcp_resource = "mcp_resource"
    unavailable = "unavailable"


class ArtifactDownload(ApplicationModel):
    artifact_id: ArtifactId
    filename: str = Field(min_length=1, max_length=255)
    mime_type: MimeType
    byte_size: int = Field(gt=0)
    sha256: Sha256
    etag: str = Field(pattern=r'^"[0-9a-f]{64}"$')
    state: ArtifactState
    resource_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    delivery_mode: ArtifactDeliveryMode
    local_path: str | None = Field(default=None, max_length=4096)
    file_uri: str | None = Field(default=None, max_length=4096)
    download_url: str | None = Field(default=None, max_length=4096)
    download_expires_at: AwareDatetime | None = None
    delivery_error: ErrorDetail | None = None


class ActorOverlayProfile(StrEnum):
    default = "default"


class CreateActorOverlayCommand(ApplicationModel):
    cluster_id: ActorClusterId
    profile: ActorOverlayProfile = ActorOverlayProfile.default


class SnippetProfile(StrEnum):
    source = "source"
    compatible_mp4 = "compatible_mp4"


class CreateSnippetCommand(ApplicationModel):
    media_id: MediaId = Field(
        description=("Source video ID, normally copied from a search or query result.")
    )
    start_seconds: float = Field(
        ge=0,
        description="Inclusive clip start from the selected result, in seconds.",
    )
    end_seconds: float = Field(
        gt=0,
        description="Exclusive clip end from the selected result, in seconds.",
    )
    profile: SnippetProfile = Field(
        default=SnippetProfile.compatible_mp4,
        description=(
            "Use compatible_mp4 for a broadly playable download; source "
            "preserves source codecs in a Matroska container."
        ),
    )

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
    media_count: int = Field(
        ge=0,
        description="Number of media items in the active index snapshot.",
    )
    media_ids: tuple[MediaId, ...] = Field(
        default=(),
        max_length=INDEX_STATUS_MEDIA_ID_LIMIT,
        description=(
            "Stable IDs included in the active snapshot. The list is capped; "
            "check media_ids_truncated before treating it as complete."
        ),
    )
    media_ids_truncated: bool = Field(
        default=False,
        description="Whether active snapshot media IDs were omitted by the cap.",
    )
    modalities: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_media_id_window(self) -> "IndexStatusSummary":
        if self.media_count < len(self.media_ids):
            raise ValueError("media_count must cover every returned media ID")
        if self.media_ids_truncated != (self.media_count > len(self.media_ids)):
            raise ValueError(
                "media_ids_truncated must reflect the returned media-ID window"
            )
        return self


class WorkspaceCapability(CapabilitySummary):
    models_ready: bool | None = Field(
        default=None,
        description=(
            "Whether required model artifacts are prepared. Null means the "
            "capability does not prepare model artifacts."
        ),
    )


class WorkspaceMediaCapability(ApplicationModel):
    name: Identifier
    indexed: bool
    record_count: NonNegativeInt | None = None
    roles: tuple[CapabilityRole, ...] = Field(
        default=(),
        description="Capability roles currently usable for this media item.",
    )
    identity_mode: CapabilityIdentityMode = CapabilityIdentityMode.not_applicable


class WorkspaceMedia(ApplicationModel):
    media_id: MediaId
    original_filename: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0)
    state: MediaState
    in_active_snapshot: bool
    capabilities: tuple[WorkspaceMediaCapability, ...] = ()


class WorkspaceOverview(ApplicationModel):
    capabilities: tuple[WorkspaceCapability, ...] = ()
    media: tuple[WorkspaceMedia, ...] = ()
    media_total: NonNegativeInt
    next_cursor: str | None = None
    index: IndexStatus
    next_actions: tuple[str, ...] = ()


class FusionProfile(StrEnum):
    reciprocal_rank = "rrf_v1"


class EvidenceDeliveryMode(StrEnum):
    none = "none"
    keyframes = "keyframes"
    keyframes_and_clips = "keyframes_and_clips"


class EvidenceDeliveryPolicy(ApplicationModel):
    mode: EvidenceDeliveryMode = EvidenceDeliveryMode.none
    include_board: bool = False
    max_items: int = Field(default=3, ge=1, le=10)
    padding_before_seconds: float = Field(default=2.0, ge=0, le=30)
    padding_after_seconds: float = Field(default=2.0, ge=0, le=30)
    clip_profile: SnippetProfile = SnippetProfile.compatible_mp4


class InitialEvidenceDeliveryPolicy(EvidenceDeliveryPolicy):
    include_board: bool = True
    max_items: int = Field(default=3, ge=1, le=5)


class EvidenceBoardCandidate(ApplicationModel):
    evidence_id: Sha256
    rank: int = Field(gt=0, le=200)
    media_id: MediaId
    generation_id: IndexGenerationId
    modalities: tuple[Identifier, ...] = Field(min_length=1, max_length=8)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    representative_timestamp: float = Field(ge=0)
    frame_index: int | None = Field(default=None, ge=0)
    frame_match: "EvidenceFrameMatch"
    score: float | None = None
    display_text: str | None = Field(default=None, max_length=512)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_interval(self) -> "EvidenceBoardCandidate":
        if self.end < self.start:
            raise ValueError("Evidence board candidate end precedes its start.")
        return self


class SearchCommand(ApplicationModel):
    query: SearchQuery = Field(description="Text to match against indexed moments.")
    modalities: tuple[Identifier, ...] = Field(
        default=(),
        description=(
            "Indexed capabilities to search. Omit to use every searchable "
            "capability in the active snapshot."
        ),
    )
    media_id: MediaId | None = Field(
        default=None,
        description=(
            "Optional single-media filter. Provide a stable ID from list_media "
            "to search only that video; omit it to rank results across every "
            "media item in the active index snapshot."
        ),
    )
    top_k: int = Field(
        default=10,
        gt=0,
        le=100,
        description="Maximum fused moments to return across the selected scope.",
    )
    evidence_delivery: InitialEvidenceDeliveryPolicy | None = Field(
        default=None,
        description=(
            "Optional evidence-board and bounded frame/clip delivery. Omit to "
            "preserve the transport-neutral application default; MCP supplies "
            "an evidence-board default."
        ),
    )

    @field_validator("modalities")
    @classmethod
    def _unique_modalities(
        cls,
        values: tuple[Identifier, ...],
    ) -> tuple[Identifier, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Search modalities must be unique.")
        return values


class SearchHit(ApplicationModel):
    rank: int = Field(gt=0)
    media_id: MediaId
    video_id: VideoId
    generation_id: IndexGenerationId
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    score: float
    raw_distance: float
    modality: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _reject_internal_metadata(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        forbidden: set[str] = set()

        def inspect(item: JsonValue) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if (
                        key == "path"
                        or key == "storage_key"
                        or key.endswith("_path")
                        or key.endswith("_directory")
                    ):
                        forbidden.add(key)
                    inspect(nested)
            elif isinstance(item, list):
                for nested in item:
                    inspect(nested)

        inspect(value)
        if forbidden:
            raise ValueError("Search metadata contains internal location fields.")
        return value

    @model_validator(mode="after")
    def _validate_interval(self) -> "SearchHit":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SearchResult(ApplicationModel):
    schema_version: int = INDEX_SCHEMA_VERSION
    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    modality: str = Field(min_length=1)
    hits: tuple[SearchHit, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_prediction(self) -> dict[str, list[dict[str, Any]]]:
        return {self.query_id: [hit.model_dump(mode="json") for hit in self.hits]}


class FusionProvenance(ApplicationModel):
    profile: Literal[FusionProfile.reciprocal_rank] = FusionProfile.reciprocal_rank
    rank_constant: int = Field(default=60, gt=0)
    overlap_rule: Literal["connected_intervals"] = "connected_intervals"
    requested_modalities: tuple[Identifier, ...] = ()
    searched_modalities: tuple[Identifier, ...] = ()


class FusedMoment(ApplicationModel):
    moment_id: Sha256 | None = None
    rank: int = Field(gt=0)
    score: float = Field(gt=0)
    media_id: MediaId
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    modalities: tuple[Identifier, ...]
    hits: tuple[SearchHit, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_fused_moment(self) -> "FusedMoment":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        if any(hit.media_id != self.media_id for hit in self.hits):
            raise ValueError("Fused moment hits must belong to one media item.")
        if set(self.modalities) != {hit.modality for hit in self.hits}:
            raise ValueError("Fused moment modalities must match its hits.")
        return self


class FusedSearchResult(ApplicationModel):
    schema_version: int = INDEX_SCHEMA_VERSION
    query_id: str = Field(min_length=1)
    query: SearchQuery
    modalities: tuple[Identifier, ...]
    moments: tuple[FusedMoment, ...] = ()
    fusion: FusionProvenance
    evidence_delivery: "EvidenceDeliveryResult | None" = None


class QueryVideoCommand(ApplicationModel):
    question: SearchQuery = Field(
        description="Natural-language question grounded in indexed evidence."
    )
    media_id: MediaId | None = Field(
        default=None,
        description=(
            "Optional single-media filter. Provide a stable ID from list_media "
            "to use evidence only from that video; omit it to use evidence "
            "from every media item in the active index snapshot."
        ),
    )
    modalities: tuple[Identifier, ...] = Field(
        default=(),
        max_length=8,
        description=(
            "Indexed capabilities to use as evidence. Omit to use every "
            "queryable capability in the active snapshot."
        ),
    )
    top_k: int = Field(
        default=10,
        gt=0,
        le=50,
        description="Maximum ranked evidence moments used for the answer.",
    )
    evidence_delivery: InitialEvidenceDeliveryPolicy | None = Field(
        default=None,
        description=(
            "Optional evidence-board and bounded frame/clip delivery. Omit to "
            "preserve the transport-neutral application default; MCP supplies "
            "an evidence-board default."
        ),
    )

    @field_validator("modalities")
    @classmethod
    def _unique_modalities(
        cls,
        values: tuple[Identifier, ...],
    ) -> tuple[Identifier, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Query modalities must be unique.")
        return values


class SearchMomentsPlanStep(ApplicationModel):
    kind: Literal["search_moments"] = "search_moments"
    modality: Identifier
    query: SearchQuery


class ActorOverviewPlanStep(ApplicationModel):
    kind: Literal["actor_overview"] = "actor_overview"


QueryPlanStep = Annotated[
    SearchMomentsPlanStep | ActorOverviewPlanStep,
    Field(discriminator="kind"),
]


class QueryPlan(ApplicationModel):
    steps: tuple[QueryPlanStep, ...] = Field(min_length=1, max_length=8)


class QueryPlanningRequest(ApplicationModel):
    question: SearchQuery
    allowed_modalities: tuple[Identifier, ...]
    actor_overview_allowed: bool = False


class QueryModelIdentity(ApplicationModel):
    provider: Literal["ollama"]
    model: str = Field(min_length=1, max_length=255)


class MomentEvidence(ApplicationModel):
    kind: Literal["moment"] = "moment"
    evidence_id: Sha256
    snapshot_id: IndexSnapshotId
    media_id: MediaId
    generation_id: IndexGenerationId
    modality: Identifier
    source_id: str = Field(min_length=1, max_length=512)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    display_text: str | None = Field(default=None, max_length=4096)
    hit: SearchHit

    @model_validator(mode="after")
    def _validate_hit_identity(self) -> "MomentEvidence":
        if (
            self.media_id != self.hit.media_id
            or self.generation_id != self.hit.generation_id
            or self.modality != self.hit.modality
            or self.source_id != self.hit.source_id
            or self.start != self.hit.start
            or self.end != self.hit.end
        ):
            raise ValueError("Evidence identity must match its search hit.")
        return self


class ActorEvidence(ApplicationModel):
    kind: Literal["actor"] = "actor"
    evidence_id: Sha256
    snapshot_id: IndexSnapshotId
    media_id: MediaId
    generation_id: IndexGenerationId
    modality: Literal["actor"] = "actor"
    cluster_id: ActorClusterId
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    detection_count: int = Field(ge=0)
    display_text: str = Field(min_length=1, max_length=4096)


Evidence = Annotated[
    MomentEvidence | ActorEvidence,
    Field(discriminator="kind"),
]


class EvidenceFrameMatch(StrEnum):
    exact_indexed_frame = "exact_indexed_frame"
    representative = "representative"


class EvidenceDeliveryState(StrEnum):
    ready = "ready"
    partial = "partial"
    failed = "failed"


class EvidenceRangeResolution(ApplicationModel):
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(ge=0)
    representative_timestamp_seconds: float = Field(ge=0)
    clip_start_seconds: float = Field(ge=0)
    clip_end_seconds: float = Field(gt=0)
    requested_padding_before_seconds: float = Field(ge=0)
    requested_padding_after_seconds: float = Field(ge=0)
    applied_padding_before_seconds: float = Field(ge=0)
    applied_padding_after_seconds: float = Field(ge=0)
    start_clamped: bool = False
    end_clamped: bool = False
    source_interval_truncated: bool = False

    @model_validator(mode="after")
    def _validate_ranges(self) -> "EvidenceRangeResolution":
        if self.source_end_seconds < self.source_start_seconds:
            raise ValueError("source evidence end must not precede its start")
        if self.clip_end_seconds <= self.clip_start_seconds:
            raise ValueError("resolved clip duration must be positive")
        return self


class EvidenceArtifact(ApplicationModel):
    artifact: Artifact
    resource_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    delivery: ArtifactDownload | None = None


class EvidenceKeyframe(ApplicationModel):
    match: EvidenceFrameMatch
    timestamp_seconds: float = Field(ge=0)
    frame_index: int | None = Field(default=None, ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    artifact: EvidenceArtifact


class EvidenceDeliveryItem(ApplicationModel):
    evidence_id: Sha256
    rank: int = Field(gt=0)
    media_id: MediaId
    generation_id: IndexGenerationId
    modalities: tuple[Identifier, ...] = Field(min_length=1)
    score: float | None = None
    provenance: dict[str, JsonValue] = Field(default_factory=dict)
    state: EvidenceDeliveryState
    range: EvidenceRangeResolution | None = None
    keyframe: EvidenceKeyframe | None = None
    clip: EvidenceArtifact | None = None
    errors: tuple[ErrorDetail, ...] = ()


class EvidenceDeliveryResult(ApplicationModel):
    policy: EvidenceDeliveryPolicy
    items: tuple[EvidenceDeliveryItem, ...] = Field(max_length=10)
    board: "EvidenceBoardResult | None" = None


class EvidenceBoardTile(EvidenceBoardCandidate):
    tile_id: Sha256
    page_number: int = Field(gt=0, le=16)
    position: int = Field(gt=0, le=48)
    keyframe_artifact_id: ArtifactId | None = None
    state: EvidenceDeliveryState
    errors: tuple[ErrorDetail, ...] = ()


class EvidenceBoardPage(ApplicationModel):
    page_number: int = Field(gt=0, le=16)
    media_id: MediaId
    generation_id: IndexGenerationId
    artifact: EvidenceArtifact
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    columns: int = Field(gt=0, le=12)
    rows: int = Field(gt=0, le=12)
    tile_ids: tuple[Sha256, ...] = Field(min_length=1, max_length=48)


class EvidenceBoardResult(ApplicationModel):
    source_job_id: JobId
    source_fingerprint: Sha256
    requested_count: int = Field(ge=0, le=200)
    rendered_count: int = Field(ge=0, le=200)
    failed_count: int = Field(ge=0, le=200)
    pages: tuple[EvidenceBoardPage, ...] = Field(max_length=16)
    tiles: tuple[EvidenceBoardTile, ...] = Field(max_length=200)
    next_start_rank: int | None = Field(default=None, ge=1, le=200)


class DraftClaim(ApplicationModel):
    text: str = Field(min_length=1, max_length=4096)
    evidence_ids: tuple[Sha256, ...] = Field(min_length=1, max_length=10)

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(
        cls,
        values: tuple[Sha256, ...],
    ) -> tuple[Sha256, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Draft evidence IDs must be unique.")
        return values


class DraftAnswer(ApplicationModel):
    claims: tuple[DraftClaim, ...] = Field(min_length=1, max_length=20)


class QuerySynthesisRequest(ApplicationModel):
    question: SearchQuery
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=200)


class GroundedClaim(ApplicationModel):
    text: str = Field(min_length=1, max_length=4096)
    evidence_ids: tuple[Sha256, ...] = Field(min_length=1, max_length=10)

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(
        cls,
        values: tuple[Sha256, ...],
    ) -> tuple[Sha256, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Claim evidence IDs must be unique.")
        return values


class QueryAnswerMode(StrEnum):
    generated = "generated"
    evidence_only = "evidence_only"
    no_evidence = "no_evidence"


class QueryAnswer(ApplicationModel):
    schema_version: int = INDEX_SCHEMA_VERSION
    question: SearchQuery
    mode: QueryAnswerMode
    plan: QueryPlan
    model: QueryModelIdentity | None = None
    claims: tuple[GroundedClaim, ...] = ()
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=200)
    moments: tuple[FusedMoment, ...] = ()
    fusion: FusionProvenance
    evidence_delivery: EvidenceDeliveryResult | None = None
    fallback_reason: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _validate_answer_grounding(self) -> "QueryAnswer":
        evidence_ids = {item.evidence_id for item in self.evidence}
        cited_ids = {
            evidence_id for claim in self.claims for evidence_id in claim.evidence_ids
        }
        if not cited_ids.issubset(evidence_ids):
            raise ValueError("Every claim citation must resolve to evidence.")
        if self.mode == QueryAnswerMode.generated and not self.claims:
            raise ValueError("Generated answers require grounded claims.")
        if self.mode != QueryAnswerMode.generated and self.claims:
            raise ValueError("Fallback answers must not contain generated claims.")
        if self.mode == QueryAnswerMode.no_evidence and self.evidence:
            raise ValueError("No-evidence answers cannot contain evidence.")
        return self


class PrepareModelsCommand(ApplicationModel):
    modalities: tuple[str, ...]
    capability_options: Mapping[str, Mapping[str, JsonValue]] = Field(
        default_factory=dict
    )


class DependencyCheckCommand(ApplicationModel):
    modalities: tuple[str, ...]
    include_runtime_checks: bool = True
    include_models: bool = False


class DependencyCheckResult(ApplicationModel):
    ok: bool
    modalities: tuple[str, ...]
    checks: tuple[CapabilityDependencyCheck, ...]


class PrepareModelsResult(ApplicationModel):
    prepared: tuple[str, ...]
    modalities: tuple[str, ...]
    runtime: RuntimeProfile


JOB_SCHEMA_VERSION = 2
JOB_PROGRESS_SCHEMA_VERSION = 1


class IndexSnapshotReference(ApplicationModel):
    snapshot_id: IndexSnapshotId
    snapshot_sha256: Sha256


class IndexJobRequest(ApplicationModel):
    kind: Literal[JobKind.index] = JobKind.index
    command: CreateIndexCommand


class MediaImportJobRequest(ApplicationModel):
    kind: Literal[JobKind.media_import] = JobKind.media_import
    upload_id: Identifier | None = None
    command: ImportMediaCommand | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "MediaImportJobRequest":
        if (self.upload_id is None) == (self.command is None):
            raise ValueError("Media import jobs require exactly one source.")
        return self


class SearchJobRequest(ApplicationModel):
    kind: Literal[JobKind.search] = JobKind.search
    command: SearchCommand
    snapshot: IndexSnapshotReference


class QueryJobRequest(ApplicationModel):
    kind: Literal[JobKind.query] = JobKind.query
    command: QueryVideoCommand
    snapshot: IndexSnapshotReference


class SnippetJobRequest(ApplicationModel):
    kind: Literal[JobKind.snippet] = JobKind.snippet
    command: CreateSnippetCommand


class ActorOverlayJobRequest(ApplicationModel):
    kind: Literal[JobKind.actor_overlay] = JobKind.actor_overlay
    command: CreateActorOverlayCommand
    snapshot: IndexSnapshotReference


class EvidenceBoardJobRequest(ApplicationModel):
    kind: Literal[JobKind.evidence_board] = JobKind.evidence_board
    source_job_id: JobId
    source_fingerprint: Sha256
    candidates: tuple[EvidenceBoardCandidate, ...] = Field(
        min_length=1,
        max_length=200,
    )

    @field_validator("candidates")
    @classmethod
    def _unique_candidates(
        cls,
        values: tuple[EvidenceBoardCandidate, ...],
    ) -> tuple[EvidenceBoardCandidate, ...]:
        evidence_ids = tuple(item.evidence_id for item in values)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence board candidates must be unique.")
        return values


class PrepareModelsJobRequest(ApplicationModel):
    kind: Literal[JobKind.prepare_models] = JobKind.prepare_models
    command: PrepareModelsCommand


JobRequest = Annotated[
    MediaImportJobRequest
    | IndexJobRequest
    | SearchJobRequest
    | QueryJobRequest
    | SnippetJobRequest
    | ActorOverlayJobRequest
    | EvidenceBoardJobRequest
    | PrepareModelsJobRequest,
    Field(discriminator="kind"),
]


class IndexJobResult(ApplicationModel):
    kind: Literal[JobKind.index] = JobKind.index
    result: IndexResult


class MediaImportJobResult(ApplicationModel):
    kind: Literal[JobKind.media_import] = JobKind.media_import
    result: MediaAsset


class SearchJobResult(ApplicationModel):
    kind: Literal[JobKind.search] = JobKind.search
    result: FusedSearchResult


class QueryJobResult(ApplicationModel):
    kind: Literal[JobKind.query] = JobKind.query
    result: QueryAnswer


class ArtifactJobResult(ApplicationModel):
    kind: Literal[JobKind.snippet, JobKind.actor_overlay]
    result: Artifact


class EvidenceBoardJobResult(ApplicationModel):
    kind: Literal[JobKind.evidence_board] = JobKind.evidence_board
    result: EvidenceBoardResult


class PrepareModelsJobResult(ApplicationModel):
    kind: Literal[JobKind.prepare_models] = JobKind.prepare_models
    result: PrepareModelsResult


JobResult = Annotated[
    MediaImportJobResult
    | IndexJobResult
    | SearchJobResult
    | QueryJobResult
    | ArtifactJobResult
    | EvidenceBoardJobResult
    | PrepareModelsJobResult,
    Field(discriminator="kind"),
]


class JobProgress(ApplicationModel):
    schema_version: Literal[JOB_PROGRESS_SCHEMA_VERSION] = JOB_PROGRESS_SCHEMA_VERSION
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
    terminal: bool
    poll_after_seconds: int = Field(ge=0, le=60)

    @model_validator(mode="before")
    @classmethod
    def _derive_poll_contract(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        state = JobState(payload.get("state"))
        terminal = state not in {JobState.queued, JobState.running}
        payload.setdefault("terminal", terminal)
        payload.setdefault("poll_after_seconds", 0 if terminal else 1)
        return payload

    @model_validator(mode="after")
    def _validate_terminal_payload(self) -> "Job":
        terminal = self.state not in {JobState.queued, JobState.running}
        if self.terminal != terminal:
            raise ValueError("job terminality must match its state")
        if self.poll_after_seconds != (0 if terminal else 1):
            raise ValueError("job polling cadence must match its state")
        if self.state == JobState.succeeded:
            if self.result is None or self.error is not None:
                raise ValueError("succeeded jobs require a result and no error")
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
