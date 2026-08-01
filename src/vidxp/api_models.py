from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from vidxp.application_models import (
    ApplicationModel,
    ErrorDetail,
    MediaUploadSessionStatus,
    MediaUploadStatus,
    UploadIntent,
)


class ErrorEnvelope(ApplicationModel):
    error: ErrorDetail


class HealthResponse(ApplicationModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(ApplicationModel):
    ready: bool
    status: Literal["ready", "not_ready"]


class UploadIntentResponse(ApplicationModel):
    intent: UploadIntent
    creation_url: str
    upload_metadata: str
    resume_url: str | None = None


class UploadHandoffBootstrapRequest(ApplicationModel):
    capability: str = Field(min_length=32, max_length=512)


class ArtifactDownloadBootstrapRequest(ApplicationModel):
    capability: str = Field(min_length=32, max_length=2048)


class ArtifactDownloadBootstrapResponse(ApplicationModel):
    content_url: str = Field(min_length=1, max_length=2048)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    expires_at: AwareDatetime


class UploadPageSessionResponse(ApplicationModel):
    status: MediaUploadSessionStatus
    creation_url: str = Field(min_length=1, max_length=2048)
    resume_urls: dict[str, str] = Field(default_factory=dict)


class UploadCreationGrantResponse(ApplicationModel):
    scheme: Literal["VidXP-Handoff"] = "VidXP-Handoff"
    status: MediaUploadStatus
    grant: str | None = Field(default=None, min_length=32, max_length=512)
    expires_at: AwareDatetime | None = None
    resume_url: str | None = Field(default=None, max_length=4096)
