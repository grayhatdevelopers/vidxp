from __future__ import annotations

from typing import Literal

from vidxp.application_models import (
    ApplicationModel,
    CapabilityInfo,
    ErrorDetail,
)


class ErrorEnvelope(ApplicationModel):
    error: ErrorDetail


class HealthResponse(ApplicationModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(ApplicationModel):
    ready: bool
    status: Literal["ready", "not_ready"]


class CapabilityList(ApplicationModel):
    items: tuple[CapabilityInfo, ...] = ()
