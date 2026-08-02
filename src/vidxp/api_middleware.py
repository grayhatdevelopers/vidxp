from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi.security.utils import get_authorization_scheme_param
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware, SAFELISTED_HEADERS
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vidxp.api_errors import public_error_response
from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    ErrorDetail,
)
from vidxp.authentication import Authenticator


PUBLIC_HTTP_PATHS = frozenset({"/favicon.ico", "/health", "/ready"})
UPLOAD_PATH = "/api/v1/media"
_UPLOAD_HANDOFF_PAGE = re.compile(r"^/upload-handoff/[0-9a-f]{32}$")
_UPLOAD_HANDOFF_STATUS = re.compile(r"^/upload-handoff/[0-9a-f]{32}/status$")
_UPLOAD_HANDOFF_BOOTSTRAP = re.compile(r"^/upload-handoff/[0-9a-f]{32}/bootstrap$")
_UPLOAD_SESSION_FILES = re.compile(r"^/upload-handoff/[0-9a-f]{32}/files$")
_UPLOAD_SESSION_CONTENT = re.compile(
    r"^/upload-handoff/[0-9a-f]{32}/files/[0-9a-f]{32}/content$"
)
_UPLOAD_SESSION_CANCEL = re.compile(
    r"^/upload-handoff/[0-9a-f]{32}/files/[0-9a-f]{32}/cancel$"
)
_UPLOAD_SESSION_CLOSE = re.compile(r"^/upload-handoff/[0-9a-f]{32}/close$")
_UPLOAD_HANDOFF_ASSETS = frozenset(
    {
        "/upload-handoff/assets/upload-page.js",
        "/upload-handoff/assets/upload-page.css",
        "/upload-handoff/assets/THIRD_PARTY_NOTICES.txt",
    }
)
_ARTIFACT_DOWNLOAD_PAGE = re.compile(r"^/artifact-download/[0-9a-f]{32}$")
_ARTIFACT_DOWNLOAD_BOOTSTRAP = re.compile(
    r"^/artifact-download/[0-9a-f]{32}/bootstrap$"
)
_ARTIFACT_DOWNLOAD_CONTENT = re.compile(
    r"^/artifact-download/[0-9a-f]{32}/content$"
)
_ARTIFACT_DOWNLOAD_ASSETS = frozenset(
    {
        "/artifact-download/assets/artifact-download.css",
        "/artifact-download/assets/artifact-download.js",
        "/artifact-download/assets/vidxp-logo.png",
    }
)


def _public_upload_handoff_request(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    method = str(scope.get("method", "")).upper()
    return (
        method == "GET"
        and (
            path in _UPLOAD_HANDOFF_ASSETS
            or _UPLOAD_HANDOFF_PAGE.fullmatch(path) is not None
            or _UPLOAD_HANDOFF_STATUS.fullmatch(path) is not None
        )
    ) or (
        method == "POST"
        and (
            _UPLOAD_HANDOFF_BOOTSTRAP.fullmatch(path) is not None
            or _UPLOAD_SESSION_FILES.fullmatch(path) is not None
            or _UPLOAD_SESSION_CONTENT.fullmatch(path) is not None
            or _UPLOAD_SESSION_CANCEL.fullmatch(path) is not None
            or _UPLOAD_SESSION_CLOSE.fullmatch(path) is not None
        )
    )


def _public_artifact_download_request(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    method = str(scope.get("method", "")).upper()
    return (
        method == "GET"
        and (
            path in _ARTIFACT_DOWNLOAD_ASSETS
            or _ARTIFACT_DOWNLOAD_PAGE.fullmatch(path) is not None
            or _ARTIFACT_DOWNLOAD_CONTENT.fullmatch(path) is not None
        )
    ) or (
        method == "HEAD"
        and _ARTIFACT_DOWNLOAD_CONTENT.fullmatch(path) is not None
    ) or (
        method == "POST"
        and _ARTIFACT_DOWNLOAD_BOOTSTRAP.fullmatch(path) is not None
    )


def _browser_capability_path(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    return (
        path == "/upload-handoff"
        or path.startswith("/upload-handoff/")
        or path == "/artifact-download"
        or path.startswith("/artifact-download/")
    )


class RequestBodyTooLarge(HTTPException, OSError):
    """Abort body parsing while preserving Starlette's file cleanup path."""

    def __init__(self) -> None:
        super().__init__(status_code=413)


class ApiCORSMiddleware:
    """Apply Starlette CORS policy only to the REST namespace."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allow_origins: tuple[str, ...],
    ) -> None:
        self.app = app
        self.allow_origins = frozenset(allow_origins)
        self.allow_methods = frozenset({"DELETE", "GET", "HEAD", "POST"})
        self.allow_headers = frozenset(
            header.lower()
            for header in (
                *SAFELISTED_HEADERS,
                "Authorization",
                "Idempotency-Key",
                "Range",
                "X-Request-ID",
            )
        )
        self.cors = CORSMiddleware(
            app,
            allow_origins=list(allow_origins),
            allow_credentials=False,
            allow_methods=sorted(self.allow_methods),
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "Range",
                "X-Request-ID",
            ],
            expose_headers=[
                "Accept-Ranges",
                "Content-Disposition",
                "Content-Length",
                "Content-Range",
                "ETag",
                "Location",
                "X-Request-ID",
            ],
            max_age=600,
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] == "http"
            and str(scope.get("path", "")).startswith("/api/")
        ):
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            requested_method = headers.get("access-control-request-method")
            if origin is not None and requested_method is not None:
                requested_headers = {
                    value.strip().lower()
                    for value in headers.get(
                        "access-control-request-headers",
                        "",
                    ).split(",")
                    if value.strip()
                }
                if (
                    "*" not in self.allow_origins
                    and origin not in self.allow_origins
                ):
                    await public_error_response(
                        ErrorDetail(
                            code="cors_origin_forbidden",
                            category=ErrorCategory.authorization,
                            message="The browser origin is not allowed.",
                        ),
                        status_code=403,
                    )(scope, receive, send)
                    return
                if (
                    requested_method not in self.allow_methods
                    or not requested_headers.issubset(self.allow_headers)
                ):
                    await public_error_response(
                        ErrorDetail(
                            code="cors_preflight_invalid",
                            category=ErrorCategory.validation,
                            message="The CORS preflight request is invalid.",
                        ),
                        status_code=400,
                    )(scope, receive, send)
                    return
            await self.cors(scope, receive, send)
            return
        await self.app(scope, receive, send)


class TypedTrustedHostMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: tuple[str, ...],
    ) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allow_any = "*" in allowed_hosts

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if self.allow_any or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        host = _host_name(Headers(scope=scope).get("host", ""))
        if any(
            host == pattern
            or (
                pattern.startswith("*.")
                and host.endswith(pattern[1:])
            )
            for pattern in self.allowed_hosts
        ):
            await self.app(scope, receive, send)
            return
        await public_error_response(
            ErrorDetail(
                code="host_not_allowed",
                category=ErrorCategory.validation,
                message="The HTTP Host header is not allowed.",
            ),
            status_code=400,
        )(scope, receive, send)


def _host_name(header: str) -> str:
    value = header.lower()
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return ""
        remainder = value[closing + 1 :]
        if remainder and (
            not remainder.startswith(":")
            or not remainder[1:].isdigit()
        ):
            return ""
        return value[1:closing]
    if value.count(":") == 1:
        candidate, port = value.rsplit(":", 1)
        if port.isdigit():
            return candidate
    return value


class BearerAuthenticationMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        authenticator: Authenticator,
        delegated_paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.authenticator = authenticator
        self.delegated_paths = frozenset(delegated_paths)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or str(scope.get("path", "")) in PUBLIC_HTTP_PATHS
            or _public_upload_handoff_request(scope)
            or _public_artifact_download_request(scope)
            or str(scope.get("path", "")) in self.delegated_paths
        ):
            await self.app(scope, receive, send)
            return
        authorization = Headers(scope=scope).get("authorization")
        scheme, credentials = get_authorization_scheme_param(authorization)
        token = credentials if scheme.lower() == "bearer" else None
        try:
            principal = await run_in_threadpool(
                self.authenticator.authenticate,
                token,
            )
        except ApplicationError as exc:
            await public_error_response(exc.detail)(
                scope,
                receive,
                send,
            )
            return
        scope["vidxp.principal"] = principal
        await self.app(scope, receive, send)


class BrowserCapabilitySecurityHeadersMiddleware:
    """Apply no-store browser security headers to capability subtrees."""

    def __init__(self, app: ASGIApp, *, upload_endpoint: str | None) -> None:
        self.app = app
        origin = None
        if upload_endpoint is not None:
            parsed = urlsplit(upload_endpoint)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        connect_sources = "'self'" if origin is None else f"'self' {origin}"
        self.common_headers = {
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Permissions-Policy": (
                "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
            ),
        }
        self.upload_csp = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "style-src-elem 'self'; style-src-attr 'unsafe-inline'; "
            f"connect-src {connect_sources}; img-src 'self' data:; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'; worker-src 'none'"
        )
        self.artifact_csp = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not _browser_capability_path(scope):
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        content_security_policy = (
            self.upload_csp
            if path == "/upload-handoff" or path.startswith("/upload-handoff/")
            else self.artifact_csp
        )

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.common_headers.items():
                    headers[name] = value
                headers["Content-Security-Policy"] = content_security_policy
            await send(message)

        await self.app(scope, receive, add_headers)


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        json_limit: int,
        upload_limit: int,
        delegated_paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.json_limit = json_limit
        self.upload_limit = upload_limit
        self.delegated_paths = frozenset(delegated_paths)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if str(scope.get("path", "")) in self.delegated_paths:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        segments = path.strip("/").split("/")
        is_upload = path == UPLOAD_PATH or (
            len(segments) == 5
            and segments[0] == "upload-handoff"
            and segments[2] == "files"
            and segments[4] == "content"
        )
        limit = (
            self.upload_limit + 1024 * 1024
            if is_upload
            else self.json_limit
        )
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await public_error_response(
                    ErrorDetail(
                        code="content_length_invalid",
                        category=ErrorCategory.validation,
                        message="The Content-Length header is invalid.",
                    ),
                    status_code=400,
                )(scope, receive, send)
                return
            if declared < 0:
                await public_error_response(
                    ErrorDetail(
                        code="content_length_invalid",
                        category=ErrorCategory.validation,
                        message="The Content-Length header is invalid.",
                    ),
                    status_code=400,
                )(scope, receive, send)
                return
            if declared > limit:
                await request_too_large_response()(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await request_too_large_response()(scope, receive, send)


def request_too_large_response():
    return public_error_response(
        ErrorDetail(
            code="request_body_too_large",
            category=ErrorCategory.resource_limit,
            message="The HTTP request body exceeds the configured limit.",
        ),
        status_code=413,
    )
