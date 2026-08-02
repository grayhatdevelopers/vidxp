from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from vidxp.core.identifiers import (
    JobId,
    MediaId,
    MimeType,
    Sha256,
    Uuid4Hex,
)
from vidxp.core.media import validate_display_filename


class UploadState(StrEnum):
    pending = "pending"
    accepted = "accepted"
    processing = "processing"
    ready = "ready"
    indexed = "indexed"
    failed = "failed"
    expired = "expired"


class UploadTransferBackend(StrEnum):
    tus = "tus"
    multipart = "multipart"
    local_path = "local_path"


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
    byte_size: int = Field(ge=0)
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
    transfer_backend: UploadTransferBackend = UploadTransferBackend.tus
    index_after_import: bool = True
    index_modalities: tuple[str, ...] = ()
    index_job_id: JobId | None = None
    index_command: dict[str, Any] | None = None
    source_path: str | None = Field(default=None, max_length=32767)
    content_sha256: Sha256 | None = None
    failure_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9_]+$",
    )
    failure_message: str | None = Field(default=None, min_length=1, max_length=512)

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
        } and self.upload_id is None:
            raise ValueError(f"{self.state} uploads require an upload identifier")
        if self.state in {
            UploadState.processing,
            UploadState.ready,
            UploadState.indexed,
        } and self.job_id is None:
            raise ValueError(f"{self.state} uploads require a job identifier")
        if self.state in {
            UploadState.pending,
            UploadState.accepted,
        } and self.job_id is not None:
            raise ValueError(f"{self.state} uploads cannot have a job identifier")
        if self.state in {UploadState.ready, UploadState.indexed} and self.media_id is None:
            raise ValueError("registered uploads require a media identifier")
        if self.media_id is not None and self.state not in {
            UploadState.ready,
            UploadState.indexed,
            UploadState.failed,
        }:
            raise ValueError("only registered uploads may reference media")
        if self.transfer_backend == UploadTransferBackend.local_path:
            if self.source_path is None:
                raise ValueError("local-path ingestion requires its canonical source")
        elif self.source_path is not None:
            raise ValueError("only local-path ingestion may retain a source path")
        if (
            self.content_sha256 is not None
            and self.transfer_backend != UploadTransferBackend.multipart
        ):
            raise ValueError("only multipart ingestion may retain a content digest")
        if self.index_job_id is not None and self.media_id is None:
            raise ValueError("index jobs require a registered media identifier")
        if not self.index_after_import and self.index_job_id is not None:
            raise ValueError("index opt-out cannot reference an index job")
        if (self.failure_code is None) != (self.failure_message is None):
            raise ValueError("upload failure code and message must be stored together")
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
    transfer_backend: UploadTransferBackend = UploadTransferBackend.tus
    index_after_import: bool = True
    index_modalities: tuple[str, ...] = ()

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
