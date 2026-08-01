from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from vidxp.application_models import (
    ApplicationModel,
    ErrorDetail,
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


class UploadPageSessionResponse(ApplicationModel):
    status: MediaUploadStatus
    creation_url: str = Field(min_length=1, max_length=2048)
    resume_url: str | None = Field(default=None, max_length=4096)


class UploadCreationGrantResponse(ApplicationModel):
    scheme: Literal["VidXP-Handoff"] = "VidXP-Handoff"
    grant: str = Field(min_length=32, max_length=512)
    expires_at: AwareDatetime
