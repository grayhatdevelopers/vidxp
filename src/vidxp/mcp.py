from __future__ import annotations

import logging
import json
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Callable, Literal, TypeVar
from urllib.parse import quote

import anyio
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.transport_security import TransportSecurityMiddleware
from mcp.shared.exceptions import MCPError
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import (
    CallToolResult,
    Icon,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)
from pydantic import Field
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from vidxp import __version__
from vidxp.application_models import (
    ApplicationError,
    ArtifactDeliveryMode,
    ArtifactDownload,
    CapabilityInfo,
    CapabilityList,
    CreateSnippetCommand,
    CreateIndexCommand,
    ErrorCategory,
    ErrorDetail,
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
    MediaUploadSessionStatus,
    Principal,
    PrepareModelsCommand,
    QueryVideoCommand,
    RuntimeReadiness,
    SearchCommand,
    WorkspaceOverview,
    UploadSessionId,
)
from vidxp.artifact_delivery import (
    ArtifactDownloadCapabilities,
    artifact_binding,
    require_resource_binding,
    verified_local_path,
)
from vidxp.authentication import (
    OIDCBearerAuthenticator,
    create_authenticator,
)
from vidxp.authorization import RepositoryPermission
from vidxp.branding import (
    ICON_MIME_TYPE,
    ICON_SIZE,
    PROJECT_URL,
    icon_data_uri,
)
from vidxp.composition import ControlPlaneContext, HttpApplicationContext
from vidxp.idempotency import (
    IdempotencyKey,
    scoped_job_id,
    scoped_request_key,
)
from vidxp.core.identifiers import ArtifactId
from vidxp.settings import HttpAuthMode
from vidxp.upload_service import RemoteUploadService


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


class MediaUploadSessionLink(MediaUploadSessionStatus):
    upload_session_url: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "Short-lived HTTPS capability link for the user to open. The "
            "fragment is a bearer secret and is never sent through native "
            "URL elicitation."
        ),
    )


def _uploads(context: ControlPlaneContext) -> RemoteUploadService:
    if context.uploads is None:
        raise ApplicationError(
            "remote_upload_unavailable",
            ErrorCategory.unavailable,
            "Remote resumable uploads are not configured.",
        )
    return context.uploads


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


def _translate_application_result(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except ApplicationError as exc:
        raise _application_error(exc) from exc


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
    artifact_delivery: Literal["local_stdio", "streamable_http"] = "local_stdio",
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
        website_url=PROJECT_URL,
        icons=[
            Icon(
                src=icon_data_uri(),
                mimeType=ICON_MIME_TYPE,
                sizes=[ICON_SIZE],
            )
        ],
        instructions=(
            "Call get_workspace before planning index, search, query, or actor "
            "work; it reports valid capability roles for each media item. Call "
            "get_runtime_readiness before indexing. If selected model "
            "artifacts are missing, submit prepare_models and poll get_job "
            "until it completes. "
            "Use create_media_upload to give the user a secure upload page, "
            "then poll get_media_upload until it returns a media_id. Use that "
            "media_id with start_indexing. get_index_status identifies the "
            "media included in the active index snapshot. For search_moments "
            "and query_video, provide command.media_id to restrict work to one "
            "video, or omit it to search/query across every media item in that "
            "snapshot. To deliver a matching time range, submit create_clip, "
            "poll get_job, then call get_artifact_download with the completed "
            "job's artifact_id. Use list_jobs to recover job IDs across agent "
            "sessions."
        ),
        version=__version__,
        token_verifier=token_verifier,
        auth=auth,
    )

    async def artifact_bytes(
        artifact_id: ArtifactId,
        *,
        expected_mime_type: str,
    ) -> bytes:
        resource = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: (
                context.application.open_artifact_content(artifact_id)
            ),
        )
        if resource.mime_type != expected_mime_type:
            raise MCPError(
                -32602,
                "The artifact URI does not match its media type.",
                {
                    "code": "artifact_media_type_mismatch",
                    "category": "validation",
                    "retryable": False,
                },
            )
        return await anyio.to_thread.run_sync(resource.path.read_bytes)

    @server.resource(
        "vidxp://artifacts/{artifact_id}/content.mp4",
        name="vidxp_artifact_mp4",
        title="VidXP MP4 artifact",
        description=(
            "Binary content for a generated VidXP clip or video artifact."
        ),
        mime_type="video/mp4",
    )
    async def read_mp4_artifact(artifact_id: ArtifactId) -> bytes:
        return await artifact_bytes(
            artifact_id,
            expected_mime_type="video/mp4",
        )

    @server.resource(
        "vidxp://artifacts/{artifact_id}/content.mkv",
        name="vidxp_artifact_matroska",
        title="VidXP Matroska artifact",
        description=(
            "Binary content for a source-profile VidXP clip artifact."
        ),
        mime_type="video/x-matroska",
    )
    async def read_matroska_artifact(artifact_id: ArtifactId) -> bytes:
        return await artifact_bytes(
            artifact_id,
            expected_mime_type="video/x-matroska",
        )

    @server.tool(
        description=(
            "Inspect registered media, the active index, installed capabilities, "
            "and the searchable, queryable, inspectable, or renderable roles "
            "currently usable for each media item. Call this before planning "
            "index, search, query, or actor work."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_workspace(
        page_size: Annotated[int, Field(gt=0, le=100)] = 50,
        cursor: Annotated[
            str | None,
            Field(min_length=1, max_length=512),
        ] = None,
    ) -> WorkspaceOverview:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.application.workspace(
                ListMediaCommand(page_size=page_size, cursor=cursor)
            ),
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
        description=(
            "List registered video filenames, metadata, and stable media IDs "
            "without transferring video bytes. Registration does not imply "
            "that a video is present in the active index snapshot."
        ),
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
        description=(
            "Get one registered video's metadata by the stable media ID "
            "returned by list_media."
        ),
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
        description=(
            "Create an idempotent multi-file upload session and return its "
            "short-lived capability link. The user selects files in the "
            "browser; filenames, sizes, MIME types, and video bytes are not "
            "tool inputs."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def create_media_upload(
        idempotency_key: IdempotencyKey,
    ) -> Annotated[CallToolResult, MediaUploadSessionLink]:
        link = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=lambda actor: _uploads(context).create_upload_session(
                principal=actor,
                request_key=scoped_request_key(
                    principal=actor,
                    transport="mcp",
                    operation="media-upload-session",
                    idempotency_key=idempotency_key,
                ),
            ),
        )
        assert context.settings.upload_handoff_public_url is not None
        page_url = (
            f"{context.settings.upload_handoff_public_url}/"
            f"{link.status.session_id}#capability="
            f"{quote(link.capability, safe='')}"
        )
        result = MediaUploadSessionLink(
            **link.status.model_dump(),
            upload_session_url=page_url,
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        "Open this short-lived VidXP upload session to select "
                        f"and upload videos:\n{page_url}"
                    ),
                )
            ],
            structured_content=result.model_dump(mode="json"),
        )

    @server.tool(
        description=(
            "Get durable aggregate and per-file state for an upload session. "
            "Ready children include media_id; processing or failed children "
            "include job_id and actionable next steps."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_media_upload(
        upload_session_id: UploadSessionId,
    ) -> MediaUploadSessionStatus:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda actor: _uploads(context).get_status(
                upload_session_id,
                principal=actor,
            ),
        )

    @server.tool(
        description=(
            "Inspect the active index snapshot, including its indexed media "
            "count and media IDs."
        ),
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
        description=(
            "Add or replace one registered media ID in the active multi-video "
            "index snapshot. Obtain the ID from list_media or a completed "
            "upload, then poll get_job."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def start_indexing(
        command: CreateIndexCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
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
        description=(
            "Submit a durable ranked moment search. Set command.media_id to "
            "search one registered video; omit it to search across every media "
            "item in the active index snapshot. Poll get_job for top-k results."
        ),
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
            "moments and actor evidence. Set command.media_id for one video, "
            "or omit it to query across every media item in the active index "
            "snapshot. Poll get_job for the answer and evidence."
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
        description=(
            "Create a downloadable clip from a media ID and time range returned "
            "by search_moments or query_video. Poll get_job, then pass the "
            "completed result's artifact_id to get_artifact_download."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def create_clip(
        command: CreateSnippetCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            return context.jobs.submit_snippet(
                command,
                job_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation="snippet",
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
            "Return a readable MCP resource link plus transport-appropriate "
            "delivery metadata for a completed clip or video artifact. Local "
            "stdio may expose its verified path; Streamable HTTP returns a "
            "short-lived browser download without exposing server paths."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_artifact_download(
        artifact_id: ArtifactId,
    ) -> Annotated[CallToolResult, ArtifactDownload]:
        artifact = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: (
                context.application.get_artifact(artifact_id)
            ),
        )
        binding = _translate_application_result(lambda: artifact_binding(artifact))
        resource_uri = (
            f"vidxp://artifacts/{artifact.artifact_id}/"
            f"content.{binding.extension}"
        )
        link = ResourceLink(
            name=binding.filename,
            title=f"VidXP {artifact.kind.value.replace('_', ' ')}",
            uri=resource_uri,
            description=(
                f"Generated from media {artifact.media_id}; "
                f"{artifact.byte_size:,} bytes."
            ),
            mimeType=artifact.mime_type,
            size=artifact.byte_size,
        )
        local_path = None
        file_uri = None
        download_url = None
        download_expires_at = None
        delivery_error = None
        if artifact_delivery == "streamable_http":
            issued = _translate_application_result(
                lambda: ArtifactDownloadCapabilities(context.settings).issue(
                    artifact
                )
            )
            delivery_mode = ArtifactDeliveryMode.https_download
            download_url = issued.url
            download_expires_at = issued.expires_at
        elif context.settings.mcp_stdio_filesystem_accessible:
            resource = await _invoke_async(
                context,
                default_principal=default_principal,
                permission=RepositoryPermission.read,
                operation=lambda _actor: (
                    context.application.open_artifact_content(artifact_id)
                ),
            )
            _translate_application_result(
                lambda: require_resource_binding(binding, resource)
            )
            resolved = _translate_application_result(
                lambda: verified_local_path(resource.path)
            )
            delivery_mode = ArtifactDeliveryMode.local_file
            local_path = resolved
            file_uri = resolved.as_uri()
        elif context.settings.artifact_download_public_url is not None:
            issued = _translate_application_result(
                lambda: ArtifactDownloadCapabilities(context.settings).issue(
                    artifact
                )
            )
            delivery_mode = ArtifactDeliveryMode.https_download
            download_url = issued.url
            download_expires_at = issued.expires_at
        else:
            delivery_mode = ArtifactDeliveryMode.mcp_resource
            delivery_error = ErrorDetail(
                code="local_path_unavailable",
                category=ErrorCategory.unavailable,
                message=(
                    "The stdio client is configured as filesystem-isolated; "
                    "read the MCP resource or configure a public download origin."
                ),
            )
        result = ArtifactDownload(
            artifact_id=artifact.artifact_id,
            filename=binding.filename,
            mime_type=artifact.mime_type,
            byte_size=artifact.byte_size,
            sha256=artifact.sha256,
            etag=f'"{artifact.sha256}"',
            state=artifact.state,
            resource_uri=resource_uri,
            delivery_mode=delivery_mode,
            local_path=local_path,
            file_uri=file_uri,
            download_url=download_url,
            download_expires_at=download_expires_at,
            delivery_error=delivery_error,
        )
        return CallToolResult(
            content=[link],
            structured_content=result.model_dump(mode="json"),
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
        artifact_delivery="streamable_http",
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
