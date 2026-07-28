from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Mapping, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
)

from vidxp.core.identifiers import (
    ArtifactId as ArtifactId,
    Identifier as Identifier,
    IndexGenerationId as IndexGenerationId,
    IndexSnapshotId as IndexSnapshotId,
    JobId as JobId,
    MediaId as MediaId,
    RepositoryId as RepositoryId,
    VideoId as VideoId,
)

T = TypeVar("T")


class ApplicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class CreateIndexCommand(ApplicationModel):
    path: Path
    media_id: MediaId | None = None
    modalities: tuple[str, ...]
    frame_stride: int = Field(default=1, gt=0)
    capability_options: Mapping[str, Mapping[str, Any]] = Field(
        default_factory=dict
    )
    source_name: str | None = Field(default=None, min_length=1)


class IndexResult(ApplicationModel):
    summary: Mapping[str, Any]


class RemoveIndexCommand(ApplicationModel):
    media_id: MediaId


class IndexStatus(ApplicationModel):
    schema_version: int = Field(ge=1)
    state: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    repository_root: Path
    index_directory: Path
    updated_at: str | None = None
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    video: Mapping[str, Any] | None = None
    summary: Mapping[str, Any] | None = None
    error: str | None = None


class SearchCommand(ApplicationModel):
    modality: str = Field(min_length=1)
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, gt=0, le=100)


class PrepareModelsCommand(ApplicationModel):
    modalities: tuple[str, ...]
    capability_options: Mapping[str, Mapping[str, Any]] = Field(
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
    runtime: Mapping[str, Any]
