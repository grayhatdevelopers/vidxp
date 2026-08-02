from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from starlette.datastructures import UploadFile

from vidxp.api_models import (
    UploadCreationGrantResponse,
    UploadHandoffBootstrapRequest,
    UploadPageSessionResponse,
)
from vidxp.api_routes.dependencies import context
from vidxp.api_routes.dependencies import copy_upload
from vidxp.application_models import (
    ApplicationError,
    CreateUploadFileCommand,
    ErrorCategory,
    MediaUploadSessionStatus,
    UploadIntentId,
    UploadSessionId,
)
from vidxp.browser_capability import BrowserCapabilitySurface
from vidxp.composition import HttpApplicationContext
from vidxp.upload_service import RemoteUploadService, UploadBrowserSession


router = APIRouter(prefix="/upload-handoff", tags=["upload-handoff"])
_SESSION_COOKIE = "__Secure-vidxp-upload"
_ASSETS = {
    "upload-page.js": "text/javascript; charset=utf-8",
    "upload-page.css": "text/css; charset=utf-8",
    "THIRD_PARTY_NOTICES.txt": "text/plain; charset=utf-8",
}


def _surface(service: HttpApplicationContext) -> BrowserCapabilitySurface:
    return BrowserCapabilitySurface(
        public_url=service.settings.upload_handoff_public_url,
        package_directory="upload_page",
        assets=_ASSETS,
        cookie_name=_SESSION_COOKIE,
        unavailable_code="remote_upload_handoff_unavailable",
        unavailable_message="Browser upload handoffs are not configured.",
        forbidden_code="upload_handoff_origin_forbidden",
        forbidden_message=(
            "The upload handoff request must come from its VidXP page."
        ),
    )


def _uploads(service: HttpApplicationContext) -> RemoteUploadService:
    if service.uploads is None:
        raise ApplicationError(
            "remote_upload_unavailable",
            ErrorCategory.unavailable,
            "Remote resumable uploads are not configured.",
        )
    return service.uploads


def _require_same_origin(
    request: Request,
    service: HttpApplicationContext,
) -> None:
    _surface(service).require_same_origin(request)


def _session_response(
    session: UploadBrowserSession,
) -> UploadPageSessionResponse:
    return UploadPageSessionResponse(
        status=session.status,
        creation_url=session.creation_url,
        resume_urls=session.resume_urls,
    )


def _set_session_cookie(
    response: Response,
    session_id: UploadSessionId,
    session: UploadBrowserSession,
    service: HttpApplicationContext,
) -> None:
    _surface(service).establish_session(
        response,
        token=session.session_token,
        expires_at=session.session_expires_at,
        path=f"/upload-handoff/{session_id}",
    )


@router.get("/assets/{asset_name}", include_in_schema=False)
def upload_page_asset(
    asset_name: str,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    return _surface(service).asset(asset_name)


@router.get("/{session_id}", include_in_schema=False)
def upload_page(
    session_id: UploadSessionId,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    del session_id
    return _surface(service).page()


@router.post(
    "/{session_id}/bootstrap",
    response_model=UploadPageSessionResponse,
    include_in_schema=False,
)
def bootstrap_upload_page(
    session_id: UploadSessionId,
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
    session = _uploads(service).exchange_upload_session(
        session_id,
        capability=command.capability,
        current_session=current_session,
    )
    _set_session_cookie(response, session_id, session, service)
    return _session_response(session)


@router.get(
    "/{session_id}/status",
    response_model=UploadPageSessionResponse,
    include_in_schema=False,
)
def upload_page_status(
    session_id: UploadSessionId,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> UploadPageSessionResponse:
    return _session_response(
        _uploads(service).browser_session(
            session_id,
            session_token=session_token,
        )
    )


@router.post(
    "/{session_id}/files",
    response_model=UploadCreationGrantResponse,
    include_in_schema=False,
)
def create_upload_grant(
    session_id: UploadSessionId,
    command: CreateUploadFileCommand,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> UploadCreationGrantResponse:
    _require_same_origin(request, service)
    authorization = _uploads(service).authorize_session_file(
        session_id,
        command,
        session_token=session_token,
    )
    return UploadCreationGrantResponse(
        status=authorization.status,
        grant=authorization.grant,
        expires_at=authorization.grant_expires_at,
        resume_url=authorization.resume_url,
    )


@router.post(
    "/{session_id}/files/{intent_id}/content",
    response_model=MediaUploadSessionStatus,
    include_in_schema=False,
)
async def upload_multipart_file(
    session_id: UploadSessionId,
    intent_id: UploadIntentId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> MediaUploadSessionStatus:
    _require_same_origin(request, service)
    uploads = _uploads(service)
    await asyncio.to_thread(
        uploads.browser_session,
        session_id,
        session_token=session_token,
    )
    async with request.form(max_files=1, max_fields=0) as form:
        upload = form.get("upload")
        if not isinstance(upload, UploadFile):
            raise ApplicationError(
                "upload_body_invalid",
                ErrorCategory.validation,
                "The multipart request must contain one upload file.",
            )
        staged = await asyncio.to_thread(
            copy_upload,
            upload,
            maximum=service.settings.http_max_small_upload_bytes,
            directory=service.settings.quarantine_root,
        )
        return await asyncio.to_thread(
            uploads.complete_multipart_file,
            session_id,
            intent_id,
            staged_path=staged,
            original_filename=upload.filename or "",
            declared_mime_type=upload.content_type,
            byte_size=staged.stat().st_size,
            session_token=session_token,
        )


@router.post(
    "/{session_id}/files/{intent_id}/cancel",
    response_model=MediaUploadSessionStatus,
    include_in_schema=False,
)
def cancel_upload_file(
    session_id: UploadSessionId,
    intent_id: UploadIntentId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> MediaUploadSessionStatus:
    _require_same_origin(request, service)
    return _uploads(service).cancel_browser_file(
        session_id,
        intent_id,
        session_token=session_token,
    )


@router.post(
    "/{session_id}/close",
    response_model=MediaUploadSessionStatus,
    include_in_schema=False,
)
def close_upload_session(
    session_id: UploadSessionId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
    session_token: Annotated[
        str | None,
        Cookie(alias=_SESSION_COOKIE),
    ] = None,
) -> MediaUploadSessionStatus:
    _require_same_origin(request, service)
    return _uploads(service).close_browser_session(
        session_id,
        session_token=session_token,
    )
