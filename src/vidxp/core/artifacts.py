from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from vidxp.core.identifiers import (
    ArtifactId,
    IndexGenerationId,
    JobId,
    MediaId,
    MimeType,
    Sha256,
)
from vidxp.core.storage_keys import validate_storage_key


ARTIFACT_SCHEMA_VERSION = 1


class ArtifactIntegrityError(RuntimeError):
    """Raised when managed artifact bytes no longer match catalog authority."""


class ArtifactRenderError(RuntimeError):
    """Raised when an artifact renderer cannot produce the requested output."""


class ArtifactRendererUnavailableError(RuntimeError):
    """Raised when the configured artifact renderer is unavailable."""


class ArtifactKind(StrEnum):
    actor_overlay = "actor_overlay"
    snippet = "snippet"


class ArtifactState(StrEnum):
    ready = "ready"


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        strict=True,
    )


class ArtifactRecord(_ArtifactModel):
    """Authoritative internal artifact entry."""

    schema_version: Literal[ARTIFACT_SCHEMA_VERSION] = ARTIFACT_SCHEMA_VERSION
    artifact_id: ArtifactId
    media_id: MediaId
    generation_id: IndexGenerationId | None = None
    request_key: Sha256
    job_id: JobId | None = None
    kind: ArtifactKind
    profile: str = Field(min_length=1)
    mime_type: MimeType
    byte_size: int = Field(gt=0)
    sha256: Sha256
    storage_key: str = Field(min_length=1)
    state: ArtifactState = ArtifactState.ready
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str) -> str:
        return validate_storage_key(value)


class StagedArtifact(_ArtifactModel):
    artifact_id: ArtifactId
    path: Path


class StoredArtifact(_ArtifactModel):
    sha256: Sha256
    byte_size: int = Field(gt=0)
    storage_key: str = Field(min_length=1)
    local_path: Path
