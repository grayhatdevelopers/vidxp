from base64 import b64encode
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from vidxp.api_routes.dependencies import (
    context,
    copy_upload,
    file_response,
    HttpIdempotencyKey,
    read_principal,
    scoped_request_key,
    write_principal,
)
from vidxp.application_models import (
    ApplicationError,
    CreateUploadIntentCommand,
    ErrorCategory,
    ListMediaCommand,
    MediaAsset,
    MediaPage,
    Principal,
    UploadIntent,
    UploadIntentId,
)
from vidxp.api_models import UploadIntentResponse
from vidxp.composition import HttpApplicationContext
from vidxp.core.identifiers import MediaId


router = APIRouter(prefix="/media", tags=["media"])


def _upload_response(
    service: HttpApplicationContext,
    actor: Principal,
    intent: UploadIntent,
) -> UploadIntentResponse:
    if service.uploads is None:
        raise ApplicationError(
            "remote_upload_unavailable",
            ErrorCategory.unavailable,
            "Remote resumable uploads are not configured.",
        )
    assert service.settings.upload_public_endpoint is not None
    encoded_intent = b64encode(intent.intent_id.encode("ascii")).decode("ascii")
    return UploadIntentResponse(
        intent=intent,
        creation_url=service.settings.upload_public_endpoint,
        upload_metadata=f"intent_id {encoded_intent}",
        resume_url=service.uploads.upload_url(
            intent.intent_id,
            principal=actor,
        ),
    )


def _private_upload_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.post(
    "/uploads",
    response_model=UploadIntentResponse,
    status_code=201,
    operation_id="createUploadIntent",
    summary="Create a resumable media upload",
    dependencies=[Depends(write_principal)],
)
def create_upload_intent(
    command: CreateUploadIntentCommand,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> UploadIntentResponse:
    if service.uploads is None:
        raise ApplicationError(
            "remote_upload_unavailable",
            ErrorCategory.unavailable,
            "Remote resumable uploads are not configured.",
        )
    _private_upload_response(response)
    intent = service.uploads.create_intent(
        command,
        principal=actor,
        request_key=scoped_request_key(
            service,
            actor,
            operation="media-upload-intent",
            idempotency_key=idempotency_key,
        ),
    )
    response.headers["Location"] = f"/api/v1/media/uploads/{intent.intent_id}"
    return _upload_response(service, actor, intent)


@router.get(
    "/uploads/{intent_id}",
    response_model=UploadIntentResponse,
    operation_id="getUploadIntent",
    summary="Get resumable upload state",
    dependencies=[Depends(read_principal)],
)
def get_upload_intent(
    intent_id: UploadIntentId,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(read_principal)],
) -> UploadIntentResponse:
    if service.uploads is None:
        raise ApplicationError(
            "remote_upload_unavailable",
            ErrorCategory.unavailable,
            "Remote resumable uploads are not configured.",
        )
    _private_upload_response(response)
    return _upload_response(
        service,
        actor,
        service.uploads.get_intent(intent_id, principal=actor),
    )


@router.post(
    "",
    response_model=MediaAsset,
    status_code=201,
    operation_id="importSmallMedia",
    summary="Import a small media file",
    dependencies=[Depends(write_principal)],
)
def import_media(
    upload: Annotated[UploadFile, File()],
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> MediaAsset:
    staged = copy_upload(
        upload,
        maximum=service.settings.http_max_small_upload_bytes,
    )
    try:
        return service.application.import_uploaded_media(
            staged_path=staged,
            original_filename=upload.filename or "",
            declared_mime_type=upload.content_type,
            request_key=scoped_request_key(
                service,
                actor,
                operation="media-import",
                idempotency_key=idempotency_key,
            ),
        )
    finally:
        staged.unlink(missing_ok=True)


@router.get(
    "",
    response_model=MediaPage,
    operation_id="listMedia",
    summary="List media",
    description=(
        "List registered filenames, metadata, and stable media IDs. A "
        "registered item is searchable only when its ID is also present in "
        "the active index snapshot."
    ),
    dependencies=[Depends(read_principal)],
)
def list_media(
    service: Annotated[HttpApplicationContext, Depends(context)],
    page_size: Annotated[int, Query(gt=0, le=100)] = 50,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=512),
    ] = None,
) -> MediaPage:
    return service.application.list_media(
        ListMediaCommand(page_size=page_size, cursor=cursor)
    )


@router.get(
    "/{media_id}",
    response_model=MediaAsset,
    operation_id="getMedia",
    summary="Get media metadata",
    description=(
        "Get one registered media item by the stable ID returned from the "
        "media list or a completed upload."
    ),
    dependencies=[Depends(read_principal)],
)
def get_media(
    media_id: MediaId,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> MediaAsset:
    return service.application.get_media(media_id)


def _content(
    media_id: MediaId,
    request: Request,
    service: HttpApplicationContext,
) -> Response:
    return file_response(
        request,
        service.application.open_media_content(media_id),
        disposition="inline",
    )


@router.get(
    "/{media_id}/content",
    response_model=None,
    operation_id="getMediaContent",
    summary="Stream media content",
    dependencies=[Depends(read_principal)],
)
def get_media_content(
    media_id: MediaId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    return _content(media_id, request, service)


@router.head(
    "/{media_id}/content",
    include_in_schema=False,
    dependencies=[Depends(read_principal)],
)
def head_media_content(
    media_id: MediaId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    return _content(media_id, request, service)
