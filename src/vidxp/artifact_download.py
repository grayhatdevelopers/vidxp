from __future__ import annotations

from importlib.resources import files
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Request, Response

from vidxp.api_models import (
    ArtifactDownloadBootstrapRequest,
    ArtifactDownloadBootstrapResponse,
)
from vidxp.api_routes.dependencies import context, file_response
from vidxp.application_models import ApplicationError, ErrorCategory
from vidxp.artifact_delivery import (
    ArtifactDownloadCapabilities,
    artifact_binding,
    require_resource_binding,
)
from vidxp.composition import HttpApplicationContext
from vidxp.core.identifiers import ArtifactId
from vidxp.core.media import utc_now


router = APIRouter(prefix="/artifact-download", tags=["artifact-download"])
_SESSION_COOKIE = "__Secure-vidxp-artifact"
_SCRIPT = "artifact-download.js"


def _capabilities(service: HttpApplicationContext) -> ArtifactDownloadCapabilities:
    return ArtifactDownloadCapabilities(service.settings)


def _require_same_origin(
    request: Request,
    service: HttpApplicationContext,
) -> None:
    public_url = service.settings.artifact_download_public_url
    if public_url is None:
        raise ApplicationError(
            "public_download_origin_unavailable",
            ErrorCategory.unavailable,
            "Public artifact downloads are not configured for this deployment.",
        )
    parsed = urlsplit(public_url)
    expected = f"{parsed.scheme}://{parsed.netloc}"
    origin = request.headers.get("origin", "")
    if (
        origin.lower() != expected.lower()
        or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
    ):
        raise ApplicationError(
            "artifact_download_origin_forbidden",
            ErrorCategory.authorization,
            "The capability exchange must come from its VidXP download page.",
        )


@router.get("/assets/{asset_name}", include_in_schema=False)
def artifact_download_asset(asset_name: str) -> Response:
    if asset_name != _SCRIPT:
        raise ApplicationError(
            "resource_not_found",
            ErrorCategory.not_found,
            "The requested artifact-download asset was not found.",
        )
    content = files("vidxp").joinpath(
        "assets", "artifact_download", _SCRIPT
    ).read_bytes()
    return Response(content=content, media_type="text/javascript; charset=utf-8")


@router.get("/{artifact_id}", include_in_schema=False)
def artifact_download_page(artifact_id: ArtifactId) -> Response:
    del artifact_id
    content = files("vidxp").joinpath(
        "assets", "artifact_download", "index.html"
    ).read_bytes()
    return Response(content=content, media_type="text/html; charset=utf-8")


@router.post(
    "/{artifact_id}/bootstrap",
    response_model=ArtifactDownloadBootstrapResponse,
    include_in_schema=False,
)
def bootstrap_artifact_download(
    artifact_id: ArtifactId,
    command: ArtifactDownloadBootstrapRequest,
    request: Request,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> ArtifactDownloadBootstrapResponse:
    _require_same_origin(request, service)
    artifact = service.application.get_artifact(artifact_id)
    session_token, expires_at = _capabilities(service).exchange(
        artifact,
        command.capability,
    )
    response.set_cookie(
        _SESSION_COOKIE,
        session_token,
        max_age=max(0, int((expires_at - utc_now()).total_seconds())),
        expires=expires_at,
        path=f"/artifact-download/{artifact_id}",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return ArtifactDownloadBootstrapResponse(
        content_url=f"/artifact-download/{artifact_id}/content",
        expires_at=expires_at,
    )


def _content(
    artifact_id: ArtifactId,
    request: Request,
    service: HttpApplicationContext,
    session_token: str | None,
) -> Response:
    artifact = service.application.get_artifact(artifact_id)
    _capabilities(service).authorize(artifact, session_token)
    resource = service.application.open_artifact_content(artifact_id)
    require_resource_binding(artifact_binding(artifact), resource)
    return file_response(
        request,
        resource,
        disposition="attachment",
    )


@router.get("/{artifact_id}/content", include_in_schema=False)
def get_artifact_download_content(
    artifact_id: ArtifactId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> Response:
    return _content(artifact_id, request, service, session_token)


@router.head("/{artifact_id}/content", include_in_schema=False)
def head_artifact_download_content(
    artifact_id: ArtifactId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> Response:
    return _content(artifact_id, request, service, session_token)
