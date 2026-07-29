from __future__ import annotations

import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Annotated
from uuid import UUID

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
from vidxp.ports import LocalFileResource


HttpIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        pattern=r"^[\x21-\x7e]+$",
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

    digest = scoped_request_key(
        service,
        actor,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    value = bytearray(bytes.fromhex(digest)[:16])
    value[6] = (value[6] & 0x0F) | 0x40
    value[8] = (value[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(value)).hex


def scoped_request_key(
    service: HttpApplicationContext,
    actor: Principal,
    *,
    operation: str,
    idempotency_key: str,
) -> str:
    material = "\0".join(
        (
            "vidxp-http-request-v1",
            service.settings.repository_id,
            actor.subject,
            operation,
            idempotency_key,
        )
    ).encode()
    return sha256(material).hexdigest()


def _etag_matches(request: Request, etag: str) -> bool:
    supplied = request.headers.get("if-none-match")
    if supplied is None:
        return False
    return any(
        candidate == "*" or candidate.removeprefix("W/") == etag
        for candidate in (
            item.strip() for item in supplied.split(",")
        )
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
) -> Path:
    suffix = safe_media_suffix(Path(upload.filename or "upload.bin"))
    handle = tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix="vidxp-upload-",
        suffix=suffix,
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
