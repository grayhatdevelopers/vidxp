from __future__ import annotations

from importlib.resources import files
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Request, Response

from vidxp.api_models import (
    UploadCreationGrantResponse,
    UploadHandoffBootstrapRequest,
    UploadPageSessionResponse,
)
from vidxp.api_routes.dependencies import context, write_principal
from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    Principal,
    UploadIntentId,
)
from vidxp.composition import HttpApplicationContext
from vidxp.upload_service import RemoteUploadService, UploadBrowserSession


router = APIRouter(prefix="/upload-handoff", tags=["upload-handoff"])
_SESSION_COOKIE = "__Secure-vidxp-upload"
_ASSETS = {
    "upload-page.js": "text/javascript; charset=utf-8",
    "upload-page.css": "text/css; charset=utf-8",
    "THIRD_PARTY_NOTICES.txt": "text/plain; charset=utf-8",
}


def _uploads(service: HttpApplicationContext) -> RemoteUploadService:
    if service.uploads is None:
        raise ApplicationError(
            "remote_upload_unavailable",
            ErrorCategory.unavailable,
            "Remote resumable uploads are not configured.",
        )
    return service.uploads


def _asset(name: str) -> bytes:
    if name not in _ASSETS:
        raise ApplicationError(
            "resource_not_found",
            ErrorCategory.not_found,
            "The requested upload-page asset was not found.",
        )
    return files("vidxp").joinpath("assets", "upload_page", name).read_bytes()


def _require_same_origin(
    request: Request,
    service: HttpApplicationContext,
) -> None:
    public_url = service.settings.upload_handoff_public_url
    if public_url is None:
        raise ApplicationError(
            "remote_upload_handoff_unavailable",
            ErrorCategory.unavailable,
            "Browser upload handoffs are not configured.",
        )
    parsed = urlsplit(public_url)
    expected = f"{parsed.scheme}://{parsed.netloc}"
    if (
        request.headers.get("origin") != expected
        or request.headers.get(
            "sec-fetch-site",
            "same-origin",
        )
        != "same-origin"
    ):
        raise ApplicationError(
            "upload_handoff_origin_forbidden",
            ErrorCategory.authorization,
            "The upload handoff request must come from its VidXP page.",
        )


def _session_response(
    session: UploadBrowserSession,
) -> UploadPageSessionResponse:
    return UploadPageSessionResponse(
        status=session.status,
        creation_url=session.creation_url,
        resume_url=session.resume_url,
    )


def _set_session_cookie(
    response: Response,
    intent_id: UploadIntentId,
    session: UploadBrowserSession,
    service: HttpApplicationContext,
) -> None:
    response.set_cookie(
        _SESSION_COOKIE,
        session.session_token,
        max_age=service.settings.upload_intent_ttl_seconds,
        expires=session.session_expires_at,
        path=f"/upload-handoff/{intent_id}",
        secure=True,
        httponly=True,
        samesite="strict",
    )


@router.get("/assets/{asset_name}", include_in_schema=False)
def upload_page_asset(asset_name: str) -> Response:
    return Response(content=_asset(asset_name), media_type=_ASSETS[asset_name])


@router.get("/{intent_id}", include_in_schema=False)
def upload_page(intent_id: UploadIntentId) -> Response:
    del intent_id
    content = (
        files("vidxp").joinpath("assets", "upload_page", "index.html").read_bytes()
    )
    return Response(content=content, media_type="text/html; charset=utf-8")


@router.post(
    "/{intent_id}/bootstrap",
    response_model=UploadPageSessionResponse,
    include_in_schema=False,
)
def bootstrap_upload_page(
    intent_id: UploadIntentId,
    command: UploadHandoffBootstrapRequest,
    request: Request,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    current_session: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> UploadPageSessionResponse:
    _require_same_origin(request, service)
    session = _uploads(service).exchange_handoff(
        intent_id,
        capability=command.capability,
        current_session=current_session,
    )
    _set_session_cookie(response, intent_id, session, service)
    return _session_response(session)


@router.post(
    "/{intent_id}/authenticate",
    response_model=UploadPageSessionResponse,
    include_in_schema=False,
)
def authenticate_upload_page(
    intent_id: UploadIntentId,
    request: Request,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    current_session: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> UploadPageSessionResponse:
    _require_same_origin(request, service)
    session = _uploads(service).exchange_authenticated_handoff(
        intent_id,
        principal=actor,
        current_session=current_session,
    )
    _set_session_cookie(response, intent_id, session, service)
    return _session_response(session)


@router.get(
    "/{intent_id}/status",
    response_model=UploadPageSessionResponse,
    include_in_schema=False,
)
def upload_page_status(
    intent_id: UploadIntentId,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> UploadPageSessionResponse:
    return _session_response(
        _uploads(service).browser_session(
            intent_id,
            session_token=session_token,
        )
    )


@router.post(
    "/{intent_id}/creation-grant",
    response_model=UploadCreationGrantResponse,
    include_in_schema=False,
)
def create_upload_grant(
    intent_id: UploadIntentId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> UploadCreationGrantResponse:
    _require_same_origin(request, service)
    grant = _uploads(service).issue_creation_grant(
        intent_id,
        session_token=session_token,
    )
    return UploadCreationGrantResponse(
        grant=grant.token,
        expires_at=grant.expires_at,
    )
