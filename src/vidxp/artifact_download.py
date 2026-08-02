from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response

from vidxp.api_models import (
    ArtifactDownloadBootstrapRequest,
    ArtifactDownloadBootstrapResponse,
)
from vidxp.api_routes.dependencies import context, file_response
from vidxp.artifact_delivery import (
    ArtifactDownloadCapabilities,
    artifact_binding,
    require_resource_binding,
)
from vidxp.browser_capability import BrowserCapabilitySurface
from vidxp.composition import HttpApplicationContext
from vidxp.core.identifiers import ArtifactId


router = APIRouter(prefix="/artifact-download", tags=["artifact-download"])
_SESSION_COOKIE = "__Secure-vidxp-artifact"
_ASSETS = {
    "artifact-download.css": "text/css; charset=utf-8",
    "artifact-download.js": "text/javascript; charset=utf-8",
    "vidxp-logo.png": "image/png",
}


def _surface(service: HttpApplicationContext) -> BrowserCapabilitySurface:
    return BrowserCapabilitySurface(
        public_url=service.settings.artifact_download_public_url,
        package_directory="artifact_download",
        assets=_ASSETS,
        cookie_name=_SESSION_COOKIE,
        unavailable_code="public_download_origin_unavailable",
        unavailable_message=(
            "Public artifact downloads are not configured for this deployment."
        ),
        forbidden_code="artifact_download_origin_forbidden",
        forbidden_message=(
            "The capability exchange must come from its VidXP download page."
        ),
    )


def _capabilities(service: HttpApplicationContext) -> ArtifactDownloadCapabilities:
    return ArtifactDownloadCapabilities(service.settings)


@router.get("/assets/{asset_name}", include_in_schema=False)
def artifact_download_asset(
    asset_name: str,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    return _surface(service).asset(asset_name)


@router.get("/{artifact_id}", include_in_schema=False)
def artifact_download_page(
    artifact_id: ArtifactId,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    del artifact_id
    return _surface(service).page()


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
    surface = _surface(service)
    surface.require_same_origin(request)
    artifact = service.application.get_artifact(artifact_id)
    binding = artifact_binding(artifact)
    session_token, expires_at = _capabilities(service).exchange(
        artifact,
        command.capability,
    )
    surface.establish_session(
        response,
        token=session_token,
        expires_at=expires_at,
        path=f"/artifact-download/{artifact_id}",
    )
    return ArtifactDownloadBootstrapResponse(
        content_url=f"/artifact-download/{artifact_id}/content",
        filename=binding.filename,
        mime_type=binding.mime_type,
        byte_size=binding.byte_size,
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
