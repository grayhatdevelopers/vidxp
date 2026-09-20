from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from vidxp.core.identifiers import MediaId, MimeType, Sha256, VideoId
from vidxp.core.storage_keys import validate_storage_key


MEDIA_SCHEMA_VERSION = 1


class InvalidMediaError(ValueError):
    """Raised when media cannot be safely probed as a supported video."""


class MediaProbeUnavailableError(RuntimeError):
    """Raised when the configured media probe is unavailable."""


class MediaStoreIntegrityError(RuntimeError):
    """Raised when managed content no longer matches catalog authority."""


class MediaImportLimitError(ValueError):
    """Raised when a local import exceeds the configured byte limit."""


class MediaUnavailableError(FileNotFoundError):
    """Raised when a cataloged media asset cannot be materialized."""


class MediaState(StrEnum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class _MediaModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        strict=True,
    )


class MediaStream(_MediaModel):
    index: int = Field(ge=0)
    kind: str = Field(min_length=1)
    codec: str = Field(min_length=1)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    sample_rate: int | None = Field(default=None, gt=0)


class MediaProbe(_MediaModel):
    detected_mime_type: MimeType
    container: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0)
    streams: tuple[MediaStream, ...] = Field(min_length=1)

    @property
    def has_video(self) -> bool:
        return any(stream.kind == "video" for stream in self.streams)

    @model_validator(mode="after")
    def _require_video_stream(self) -> "MediaProbe":
        if not self.has_video:
            raise ValueError("media probe must contain a video stream")
        return self


class StoredMedia(_MediaModel):
    """Internal managed-media location produced by a media store."""

    sha256: Sha256
    byte_size: int = Field(gt=0)
    storage_key: str = Field(min_length=1)
    local_path: Path

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str) -> str:
        return validate_storage_key(value)


class StagedMedia(_MediaModel):
    """Quarantined media awaiting probe and managed-store publication."""

    sha256: Sha256
    byte_size: int = Field(gt=0)
    storage_key: str = Field(min_length=1)
    path: Path

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str) -> str:
        return validate_storage_key(value)


class QuarantinedMedia(_MediaModel):
    """Adapter-staged media accepted by the shared ingestion boundary."""

    path: Path
    original_filename: str = Field(min_length=1, max_length=255)
    declared_mime_type: MimeType | None = None

    @field_validator("original_filename")
    @classmethod
    def _filename_only(cls, value: str) -> str:
        return validate_display_filename(value)


class MediaRecord(_MediaModel):
    """Authoritative internal catalog entry; storage_key is never projected."""

    schema_version: Literal[MEDIA_SCHEMA_VERSION] = MEDIA_SCHEMA_VERSION
    media_id: MediaId
    video_id: VideoId
    sha256: Sha256
    original_filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    declared_mime_type: MimeType | None = None
    detected_mime_type: MimeType | None = None
    container: str | None = Field(default=None, min_length=1)
    duration_seconds: float | None = Field(default=None, gt=0)
    streams: tuple[MediaStream, ...] = ()
    storage_key: str = Field(min_length=1)
    state: MediaState = MediaState.ready
    created_at: AwareDatetime

    @field_validator("original_filename")
    @classmethod
    def _filename_only(cls, value: str) -> str:
        return validate_display_filename(value)

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str) -> str:
        return validate_storage_key(value)

    @model_validator(mode="after")
    def _require_ready_probe(self) -> "MediaRecord":
        if self.state != MediaState.ready:
            return self
        if (
            self.detected_mime_type is None
            or self.container is None
            or self.duration_seconds is None
            or not any(stream.kind == "video" for stream in self.streams)
        ):
            raise ValueError("ready media must contain a video stream")
        return self


def utc_now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


def validate_display_filename(value: str) -> str:
    if (
        Path(value).name != value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            "original_filename must be a basename without control characters"
        )
    return value


def safe_media_suffix(path: Path) -> str:
    """Return a bounded suffix suitable for temporary media staging."""

    suffix = path.suffix.lower()
    if (
        len(suffix) <= 10
        and suffix.startswith(".")
        and suffix[1:].isalnum()
    ):
        return suffix
    return ".bin"
