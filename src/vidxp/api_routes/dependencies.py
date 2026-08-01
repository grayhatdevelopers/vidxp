from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import Header, Request, Response, UploadFile
from fastapi.responses import FileResponse

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    Job,
    Principal,
)
from vidxp.composition import HttpApplicationContext
from vidxp.authorization import AuthorizationPolicy, RepositoryPermission
from vidxp.core.media import safe_media_suffix
from vidxp.idempotency import (
    IdempotencyKey,
    scoped_job_id as derive_scoped_job_id,
    scoped_request_key as derive_scoped_request_key,
)
from vidxp.ports import LocalFileResource


HttpIdempotencyKey = Annotated[
    IdempotencyKey,
    Header(
        alias="Idempotency-Key",
    ),
]


def context(request: Request) -> HttpApplicationContext:
    return request.app.state.vidxp


def principal(
    request: Request,
) -> Principal:
    active = request.scope.get("vidxp.principal")
    if not isinstance(active, Principal):
        raise ApplicationError(
            "authentication_required",
            ErrorCategory.authentication,
            "Valid bearer authentication is required.",
        )
    return active


def _authorized(
    request: Request,
    permission: RepositoryPermission,
) -> Principal:
    active = principal(request)
    service = context(request)
    policy: AuthorizationPolicy = service.authorization
    return policy.require(active, permission)


def read_principal(
    request: Request,
) -> Principal:
    return _authorized(request, RepositoryPermission.read)


def write_principal(
    request: Request,
) -> Principal:
    return _authorized(request, RepositoryPermission.write)


def accepted(response: Response, job: Job) -> Job:
    response.headers["Location"] = f"/api/v1/jobs/{job.job_id}"
    return job


def scoped_job_id(
    service: HttpApplicationContext,
    actor: Principal,
    *,
    operation: str,
    idempotency_key: str,
) -> str:
    """Derive a non-reversible DBOS workflow ID from an HTTP request key."""

    return derive_scoped_job_id(
        principal=actor,
        transport="http",
        operation=operation,
        idempotency_key=idempotency_key,
    )


def scoped_request_key(
    service: HttpApplicationContext,
    actor: Principal,
    *,
    operation: str,
    idempotency_key: str,
) -> str:
    return derive_scoped_request_key(
        principal=actor,
        transport="http",
        operation=operation,
        idempotency_key=idempotency_key,
    )


def _etag_matches(request: Request, etag: str) -> bool:
    supplied = request.headers.get("if-none-match")
    if supplied is None:
        return False
    return any(
        candidate == "*" or candidate.removeprefix("W/") == etag
        for candidate in (item.strip() for item in supplied.split(","))
    )


def file_response(
    request: Request,
    resource: LocalFileResource,
    *,
    disposition: str,
) -> Response:
    etag = f'"{resource.etag}"'
    common_headers = {
        "ETag": etag,
        "Cache-Control": "private, no-store",
        "Accept-Ranges": "bytes",
    }
    if _etag_matches(request, etag):
        return Response(status_code=304, headers=common_headers)
    return FileResponse(
        resource.path,
        media_type=resource.mime_type,
        filename=resource.filename,
        headers=common_headers,
        content_disposition_type=disposition,
    )


def copy_upload(
    upload: UploadFile,
    *,
    maximum: int,
    directory: Path | None = None,
) -> Path:
    suffix = safe_media_suffix(Path(upload.filename or "upload.bin"))
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix="vidxp-upload-",
        suffix=suffix,
        dir=directory,
        delete=False,
    )
    path = Path(handle.name)
    total = 0
    try:
        with handle:
            while chunk := upload.file.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise ApplicationError(
                        "media_too_large",
                        ErrorCategory.resource_limit,
                        "The uploaded media exceeds the configured limit.",
                    )
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
