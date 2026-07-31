from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import Depends, FastAPI, Request, Response, Security
from fastapi.security import HTTPBearer
from vidxp import __version__
from vidxp.api_errors import install_exception_handlers
from vidxp.api_middleware import (
    ApiCORSMiddleware,
    BearerAuthenticationMiddleware,
    RequestBodyLimitMiddleware,
    RequestBodyTooLarge,
    TypedTrustedHostMiddleware,
    request_too_large_response,
)
from vidxp.api_models import HealthResponse, ReadinessResponse
from vidxp.api_routes import create_api_router
from vidxp.api_routes.dependencies import context
from vidxp.composition import HttpApplicationContext, create_http_application
from vidxp.mcp import MCPTransportSecurityBoundary, create_remote_mcp
from vidxp.settings import VidXPSettings


_BEARER_SECURITY = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description=(
        "Bearer access token. Authentication is enforced once by the "
        "server middleware."
    ),
)


def create_app(
    settings: VidXPSettings | None = None,
    *,
    context: HttpApplicationContext | None = None,
) -> FastAPI:
    active_context = context or create_http_application(settings)
    active_settings = active_context.settings
    active_settings.validate_http_server()
    owns_context = context is None
    try:
        remote_mcp = create_remote_mcp(active_context)
    except Exception:
        if owns_context:
            active_context.close()
        raise
    mcp_paths = ("/mcp", "/mcp/")
    delegated_auth_paths = (
        (
            *mcp_paths,
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource/mcp/",
        )
        if remote_mcp.owns_authentication
        else ()
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            active_context.jobs.start()
            async with remote_mcp.server.session_manager.run():
                yield
        finally:
            if owns_context:
                active_context.close()

    publish_docs = active_settings.http_auth_mode.value == "none"
    app = FastAPI(
        title="VidXP API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if publish_docs else None,
        redoc_url="/redoc" if publish_docs else None,
        openapi_url="/openapi.json",
    )
    app.state.vidxp = active_context
    install_exception_handlers(app)

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="health",
        summary="Check process liveness",
    )
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get(
        "/favicon.ico",
        include_in_schema=False,
        status_code=204,
    )
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get(
        "/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
        operation_id="ready",
        summary="Check aggregate readiness",
    )
    def ready(
        response: Response,
        service: Annotated[HttpApplicationContext, Depends(context)],
    ) -> ReadinessResponse:
        is_ready = service.readiness.ready()
        if not is_ready:
            response.status_code = 503
        return ReadinessResponse(
            ready=is_ready,
            status="ready" if is_ready else "not_ready",
        )

    api_dependencies = (
        [Security(_BEARER_SECURITY)]
        if active_settings.http_auth_mode.value != "none"
        else []
    )
    app.include_router(
        create_api_router(),
        dependencies=api_dependencies,
    )
    app.mount("/", remote_mcp.app)
    app.add_exception_handler(
        RequestBodyTooLarge,
        _request_body_too_large_response,
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        json_limit=active_settings.http_max_json_body_bytes,
        upload_limit=active_settings.http_max_small_upload_bytes,
        delegated_paths=mcp_paths,
    )
    app.add_middleware(
        BearerAuthenticationMiddleware,
        authenticator=active_context.authenticator,
        delegated_paths=delegated_auth_paths,
    )
    if active_settings.http_allowed_origins:
        app.add_middleware(
            ApiCORSMiddleware,
            allow_origins=active_settings.http_allowed_origins,
        )
    app.add_middleware(
        TypedTrustedHostMiddleware,
        allowed_hosts=active_settings.http_trusted_hosts,
    )
    app.add_middleware(
        CorrelationIdMiddleware,
        header_name="X-Request-ID",
    )
    app.add_middleware(
        MCPTransportSecurityBoundary,
        settings=remote_mcp.transport_security,
    )
    return app


async def _request_body_too_large_response(
    _request: Request,
    _exc: RequestBodyTooLarge,
) -> Response:
    return request_too_large_response()
