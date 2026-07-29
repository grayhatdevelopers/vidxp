from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from vidxp.core.identifiers import (
    JobId,
    MediaId,
    MimeType,
    Uuid4Hex,
)
from vidxp.core.media import validate_display_filename


class UploadState(StrEnum):
    pending = "pending"
    accepted = "accepted"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    expired = "expired"


class UploadIntentRecord(BaseModel):
    """Authoritative upload state; tus upload identity stays internal."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    intent_id: Uuid4Hex
    request_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    declared_mime_type: MimeType | None = None
    state: UploadState
    created_at: AwareDatetime
    expires_at: AwareDatetime
    upload_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]*$",
    )
    job_id: JobId | None = None
    media_id: MediaId | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> "UploadIntentRecord":
        validate_display_filename(self.original_filename)
        if self.expires_at <= self.created_at:
            raise ValueError("upload expiry must follow creation")
        if self.state == UploadState.pending and self.upload_id is not None:
            raise ValueError("pending uploads cannot have an upload identifier")
        if self.state in {
            UploadState.accepted,
            UploadState.processing,
            UploadState.failed,
        } and self.upload_id is None:
            raise ValueError(f"{self.state} uploads require an upload identifier")
        if self.state in {
            UploadState.processing,
            UploadState.failed,
            UploadState.ready,
        } and self.job_id is None:
            raise ValueError(f"{self.state} uploads require a job identifier")
        if self.state in {
            UploadState.pending,
            UploadState.accepted,
        } and self.job_id is not None:
            raise ValueError(f"{self.state} uploads cannot have a job identifier")
        if self.state == UploadState.ready and self.media_id is None:
            raise ValueError("ready uploads require a media identifier")
        if self.media_id is not None and self.state != UploadState.ready:
            raise ValueError("only ready uploads may reference media")
        return self
