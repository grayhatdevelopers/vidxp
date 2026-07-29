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
    ListMediaCommand,
    MediaAsset,
    MediaPage,
    Principal,
)
from vidxp.composition import HttpApplicationContext
from vidxp.core.identifiers import MediaId


router = APIRouter(prefix="/media", tags=["media"])


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
