from __future__ import annotations

import logging

from asgi_correlation_id import correlation_id
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from vidxp.api_models import ErrorEnvelope
from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    ErrorDetail,
    InvalidRequestError,
)


LOGGER = logging.getLogger("vidxp.api")

_STATUS_BY_CATEGORY = {
    ErrorCategory.validation: 422,
    ErrorCategory.authentication: 401,
    ErrorCategory.authorization: 403,
    ErrorCategory.not_found: 404,
    ErrorCategory.conflict: 409,
    ErrorCategory.unavailable: 503,
    ErrorCategory.resource_limit: 429,
    ErrorCategory.cancelled: 409,
    ErrorCategory.internal: 500,
}


def public_error_response(
    detail: ErrorDetail,
    *,
    status_code: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = correlation_id.get()
    if detail.correlation_id is None and request_id is not None:
        detail = detail.model_copy(update={"correlation_id": request_id})
    active_headers = dict(headers or {})
    if detail.category == ErrorCategory.authentication:
        active_headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=status_code or _STATUS_BY_CATEGORY[detail.category],
        content=ErrorEnvelope(error=detail).model_dump(mode="json"),
        headers=active_headers,
    )


def _validation_error(request_error: RequestValidationError) -> ErrorDetail:
    errors = [
        {
            "type": item["type"],
            "location": [str(part) for part in item["loc"]],
            "message": item["msg"],
        }
        for item in request_error.errors()
    ]
    return InvalidRequestError(errors=errors).detail


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error_handler(
        _request: Request,
        exc: ApplicationError,
    ) -> JSONResponse:
        status = (
            413
            if exc.code in {"media_too_large", "request_body_too_large"}
            else None
        )
        return public_error_response(exc.detail, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return public_error_response(_validation_error(exc), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error_handler(
        _request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        if exc.status_code == 404:
            detail = ErrorDetail(
                code="http_not_found",
                category=ErrorCategory.not_found,
                message="The requested HTTP resource was not found.",
            )
        elif exc.status_code == 405:
            detail = ErrorDetail(
                code="http_method_not_allowed",
                category=ErrorCategory.validation,
                message="The HTTP method is not allowed for this resource.",
            )
        else:
            detail = ErrorDetail(
                code="http_request_rejected",
                category=ErrorCategory.validation,
                message="The HTTP request was rejected.",
            )
        return public_error_response(
            detail,
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        LOGGER.exception(
            "Unhandled API error for %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return public_error_response(
            ErrorDetail(
                code="internal_error",
                category=ErrorCategory.internal,
                message="The request could not be completed.",
            ),
            status_code=500,
        )
