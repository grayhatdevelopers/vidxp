from __future__ import annotations

from typing import Literal

from vidxp.application_models import (
    ApplicationModel,
    ErrorDetail,
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
