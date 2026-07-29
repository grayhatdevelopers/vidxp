from __future__ import annotations

import logging
import json
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Callable, TypeVar

import anyio
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.transport_security import TransportSecurityMiddleware
from mcp.shared.exceptions import MCPError
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from vidxp import __version__
from vidxp.application_models import (
    ApplicationError,
    CapabilityInfo,
    CapabilityList,
    CreateIndexCommand,
    ErrorCategory,
    Identifier,
    IndexStatus,
    Job,
    JobId,
    JobPage,
    ListJobsCommand,
    ListMediaCommand,
    MediaAsset,
    MediaId,
    MediaPage,
    Principal,
    PrepareModelsCommand,
    QueryVideoCommand,
    RuntimeReadiness,
    SearchCommand,
)
from vidxp.authentication import (
    OIDCBearerAuthenticator,
    create_authenticator,
)
from vidxp.authorization import RepositoryPermission
from vidxp.composition import ControlPlaneContext, HttpApplicationContext
from vidxp.idempotency import IdempotencyKey, scoped_job_id
from vidxp.settings import HttpAuthMode


_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")
_REQUEST_PRINCIPAL: ContextVar[Principal | None] = ContextVar(
    "vidxp_mcp_principal",
    default=None,
)
_ERROR_CODES = {
    ErrorCategory.validation: -32602,
    ErrorCategory.authentication: -32001,
    ErrorCategory.authorization: -32003,
    ErrorCategory.not_found: -32004,
    ErrorCategory.conflict: -32009,
    ErrorCategory.resource_limit: -32029,
    ErrorCategory.cancelled: -32040,
    ErrorCategory.unavailable: -32050,
    ErrorCategory.internal: -32603,
}
_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_SUBMIT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_CANCEL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


class PrincipalBridge:
    """Carry the principal validated by the outer ASGI boundary into tools."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        principal = scope.get("vidxp.principal")
        reset = None
        if isinstance(principal, Principal):
            reset = _REQUEST_PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            if reset is not None:
                _REQUEST_PRINCIPAL.reset(reset)


class VidXPTokenVerifier:
    """Adapt the shared OIDC validator to the SDK resource-server contract."""

    def __init__(self, authenticator: OIDCBearerAuthenticator) -> None:
        self.authenticator = authenticator

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            verified = await anyio.to_thread.run_sync(
                self.authenticator.authenticate_bearer,
                token,
            )
        except ApplicationError:
            return None
        principal = verified.principal
        return AccessToken(
            token=token,
            client_id=principal.client_id or principal.subject,
            subject=principal.subject,
            scopes=sorted(principal.scopes),
            expires_at=verified.expires_at,
            resource=verified.resource,
            claims=verified.claims,
        )


@dataclass(frozen=True)
class RemoteMCP:
    server: MCPServer
    app: ASGIApp
    owns_authentication: bool
    transport_security: TransportSecuritySettings


def _principal(default: Principal | None) -> Principal:
    current = _REQUEST_PRINCIPAL.get()
    if current is not None:
        return current
    token = get_access_token()
    if token is not None:
        return Principal(
            subject=token.subject or token.client_id,
            client_id=token.client_id,
            scopes=frozenset(token.scopes),
        )
    if default is not None:
        return default
    raise MCPError(
        -32001,
        "Valid bearer authentication is required.",
        {
            "code": "authentication_required",
            "category": "authentication",
            "retryable": False,
        },
    )


def _application_error(exc: ApplicationError) -> ToolError:
    detail = exc.detail
    data = {
        "code": detail.code,
        "category": detail.category.value,
        "retryable": detail.retryable,
    }
    if detail.correlation_id is not None:
        data["correlation_id"] = detail.correlation_id
    for key in (
        "capability",
        "errors",
        "install_hint",
        "remediation",
        "required_scope",
    ):
        if key in detail.details:
            data[key] = detail.details[key]
    return ToolError(
        json.dumps(
            {
                "error": {
                    "protocol_code": _ERROR_CODES[detail.category],
                    "message": detail.message,
                    **data,
                }
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _invoke(
    context: ControlPlaneContext,
    *,
    default_principal: Principal | None,
    permission: RepositoryPermission,
    operation: Callable[[Principal], _T],
) -> _T:
    try:
        principal = context.authorization.require(
            _principal(default_principal),
            permission,
        )
        return operation(principal)
    except MCPError:
        raise
    except ApplicationError as exc:
        raise _application_error(exc) from exc
    except Exception as exc:
        _LOGGER.exception("Unexpected MCP tool failure.")
        raise MCPError(
            -32603,
            "The MCP tool failed unexpectedly.",
            {
                "code": "internal_error",
                "category": "internal",
                "retryable": False,
            },
        ) from exc


async def _invoke_async(
    context: ControlPlaneContext,
    *,
    default_principal: Principal | None,
    permission: RepositoryPermission,
    operation: Callable[[Principal], _T],
) -> _T:
    return await anyio.to_thread.run_sync(
        partial(
            _invoke,
            context,
            default_principal=default_principal,
            permission=permission,
            operation=operation,
        ),
        abandon_on_cancel=True,
    )


class MCPTransportSecurityBoundary:
    """Run the SDK's transport checks before outer static authentication."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: TransportSecuritySettings,
    ) -> None:
        self.app = app
        self.security = TransportSecurityMiddleware(settings)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] == "http"
            and str(scope.get("path", "")) in {"/mcp", "/mcp/"}
        ):
            response = await self.security.validate_request(
                Request(scope, receive=receive),
                is_post=str(scope.get("method", "")).upper() == "POST",
            )
            if response is not None:
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_mcp_server(
    context: ControlPlaneContext,
    *,
    default_principal: Principal | None = None,
    oidc_authentication: bool = False,
) -> MCPServer:
    settings = context.settings
    token_verifier = None
    auth = None
    if oidc_authentication:
        assert settings.http_oidc_issuer is not None
        assert settings.mcp_public_url is not None
        if (
            isinstance(context, HttpApplicationContext)
            and isinstance(
                context.authenticator,
                OIDCBearerAuthenticator,
            )
        ):
            authenticator = context.authenticator.for_audience(
                settings.mcp_public_url,
            )
        else:
            authenticator = create_authenticator(
                settings,
                audience=settings.mcp_public_url,
                required_scopes=(),
            )
        if not isinstance(authenticator, OIDCBearerAuthenticator):
            raise ValueError("OIDC MCP requires the OIDC authenticator.")
        token_verifier = VidXPTokenVerifier(authenticator)
        auth = AuthSettings(
            issuer_url=settings.http_oidc_issuer,
            resource_server_url=settings.mcp_public_url,
            required_scopes=list(settings.http_required_scopes),
        )

    server = MCPServer(
        name="vidxp",
        title="VidXP",
        description="Index and search registered video media.",
        instructions=(
            "Call get_runtime_readiness before indexing. If selected model "
            "artifacts are missing, submit prepare_models and poll get_job "
            "until it completes. Discover registered media with list_media. "
            "Register and upload new video through the HTTP/tus API, then use "
            "its media_id with the durable indexing and query tools. Use "
            "list_jobs to recover job IDs across agent sessions."
        ),
        version=__version__,
        token_verifier=token_verifier,
        auth=auth,
    )

    @server.tool(
        description="List installed VidXP capabilities.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def list_capabilities() -> CapabilityList:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: CapabilityList(
                items=context.application.list_capabilities()
            ),
        )

    @server.tool(
        description="Get one capability and its public operation schemas.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_capability(name: Identifier) -> CapabilityInfo:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.application.get_capability(name),
        )

    @server.tool(
        description=(
            "Check runtime components and whether model artifacts are prepared."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_runtime_readiness() -> RuntimeReadiness:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.application.runtime_readiness(),
        )

    @server.tool(
        description="List registered video metadata without transferring bytes.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def list_media(
        page_size: Annotated[int, Field(gt=0, le=100)] = 50,
        cursor: Annotated[
            str | None,
            Field(min_length=1, max_length=512),
        ] = None,
    ) -> MediaPage:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.application.list_media(
                ListMediaCommand(page_size=page_size, cursor=cursor)
            ),
        )

    @server.tool(
        description="Get metadata for one registered video.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_media(media_id: MediaId) -> MediaAsset:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.application.get_media(media_id),
        )

    @server.tool(
        description="Inspect the active index state.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_index_status() -> IndexStatus:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.application.index_status(),
        )

    @server.tool(
        description="Submit durable indexing for a registered media ID.",
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def start_indexing(
        command: CreateIndexCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            context.application.require_models(command.modalities)
            return context.jobs.submit_index(
                command,
                job_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation="index",
                    idempotency_key=idempotency_key,
                ),
            )

        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=submit,
        )

    @server.tool(
        description=(
            "Explicitly download and validate selected model artifacts. Poll "
            "get_job for byte progress and completion before indexing."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def prepare_models(
        command: PrepareModelsCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            return context.jobs.submit_prepare_models(
                command,
                job_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation="prepare-models",
                    idempotency_key=idempotency_key,
                ),
            )

        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=submit,
        )

    @server.tool(
        description="Submit a durable search over the active index.",
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def search_moments(
        command: SearchCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            return context.jobs.submit_search(
                command,
                job_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation="search",
                    idempotency_key=idempotency_key,
                ),
            )

        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=submit,
        )

    @server.tool(
        description=(
            "Submit a durable grounded natural-language query over indexed "
            "moments and actor evidence."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def query_video(
        command: QueryVideoCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            return context.jobs.submit_query(
                command,
                job_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation="query",
                    idempotency_key=idempotency_key,
                ),
            )

        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=submit,
        )

    @server.tool(
        description="List durable jobs so work can be recovered across sessions.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def list_jobs(
        page_size: Annotated[int, Field(gt=0, le=100)] = 50,
        cursor: Annotated[
            str | None,
            Field(min_length=1, max_length=512),
        ] = None,
    ) -> JobPage:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.jobs.list(
                ListJobsCommand(page_size=page_size, cursor=cursor)
            ),
        )

    @server.tool(
        description="Poll a durable VidXP job and its typed result.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_job(job_id: JobId) -> Job:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.jobs.get(job_id),
        )

    @server.tool(
        description="Retry a failed or cancelled durable job.",
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def retry_job(
        job_id: JobId,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def retry(actor: Principal) -> Job:
            return context.jobs.retry(
                job_id,
                retry_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation=f"retry:{job_id}",
                    idempotency_key=idempotency_key,
                ),
            )

        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=retry,
        )

    @server.tool(
        description="Explicitly cancel an active durable job.",
        annotations=_CANCEL,
        structured_output=True,
    )
    async def cancel_job(job_id: JobId) -> Job:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=lambda _actor: context.jobs.cancel(job_id),
        )

    return server


def create_remote_mcp(context: ControlPlaneContext) -> RemoteMCP:
    owns_authentication = (
        context.settings.http_auth_mode == HttpAuthMode.oidc
    )
    server = create_mcp_server(
        context,
        oidc_authentication=owns_authentication,
    )
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(context.settings.mcp_allowed_hosts),
        allowed_origins=list(context.settings.mcp_allowed_origins),
    )
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=False,
        max_request_body_size=(
            context.settings.mcp_max_request_body_bytes
        ),
        transport_security=transport_security,
        host=context.settings.http_bind_host,
    )
    return RemoteMCP(
        server=server,
        app=PrincipalBridge(app),
        owns_authentication=owns_authentication,
        transport_security=transport_security,
    )
