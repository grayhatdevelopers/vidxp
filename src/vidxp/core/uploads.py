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


class UploadSessionState(StrEnum):
    open = "open"
    closed = "closed"
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


class UploadSessionRecord(BaseModel):
    """Capability-authorized container for independently bound uploads."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    session_id: Uuid4Hex
    request_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    selector: str = Field(pattern=r"^[0-9a-f]{32}$")
    capability_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    initiating_subject: str = Field(min_length=1, max_length=255)
    initiating_client_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    repository_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: str = Field(default="media-upload", pattern=r"^[a-z0-9-]{1,64}$")
    state: UploadSessionState
    maximum_files: int = Field(gt=0)
    maximum_file_bytes: int = Field(gt=0)
    maximum_aggregate_bytes: int = Field(gt=0)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    browser_session_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def _validate_session(self) -> "UploadSessionRecord":
        if self.expires_at <= self.created_at:
            raise ValueError("upload session expiry must follow creation")
        if self.maximum_aggregate_bytes < self.maximum_file_bytes:
            raise ValueError("aggregate limit must allow at least one maximum file")
        return self


class UploadSessionFileRecord(BaseModel):
    """One stable browser file key bound to one authoritative upload intent."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    session_id: Uuid4Hex
    client_file_key: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9._~-]+$",
    )
    intent_id: Uuid4Hex
    created_at: AwareDatetime
    creation_grant_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    creation_grant_expires_at: AwareDatetime | None = None
    creation_grant_consumed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_grant(self) -> "UploadSessionFileRecord":
        if (self.creation_grant_digest is None) != (
            self.creation_grant_expires_at is None
        ):
            raise ValueError("creation grant digest and expiry must be stored together")
        if (
            self.creation_grant_consumed_at is not None
            and self.creation_grant_digest is None
        ):
            raise ValueError("a consumed creation grant must retain its digest")
        return self
