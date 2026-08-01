from __future__ import annotations

import logging
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Annotated, AsyncIterator, Callable, Literal, TypeVar
from urllib.parse import quote
from uuid import uuid4

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.session import ServerSession
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.transport_security import TransportSecurityMiddleware
from mcp.shared.exceptions import MCPError
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Icon, ResourceLink, ToolAnnotations
from pydantic import Field
from pydantic import AwareDatetime
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from vidxp import __version__
from vidxp.application_models import (
    ApplicationError,
    CapabilityInfo,
    CapabilityList,
    CreateSnippetCommand,
    CreateIndexCommand,
    CreateUploadIntentCommand,
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
    MediaUploadStatus,
    Principal,
    PrepareModelsCommand,
    QueryVideoCommand,
    RuntimeReadiness,
    SearchCommand,
    WorkspaceOverview,
    UploadIntentId,
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
from vidxp.core.uploads import UploadState
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
_UPLOAD_ELICITATION_COMPLETE_STATES = frozenset(
    {
        UploadState.processing,
        UploadState.ready,
        UploadState.failed,
        UploadState.expired,
    }
)
_UPLOAD_ELICITATION_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class _MCPLifespan:
    task_group: anyio.abc.TaskGroup


@asynccontextmanager
async def _mcp_lifespan(_server: MCPServer) -> AsyncIterator[_MCPLifespan]:
    async with anyio.create_task_group() as task_group:
        try:
            yield _MCPLifespan(task_group=task_group)
        finally:
            task_group.cancel_scope.cancel()


async def _complete_upload_elicitation(
    service: RemoteUploadService,
    principal: Principal,
    intent_id: UploadIntentId,
    handoff_expires_at: AwareDatetime,
    elicitation_id: str,
    session: ServerSession,
) -> None:
    """Notify the originating MCP session once the browser upload is done."""
    while datetime.now(timezone.utc) < handoff_expires_at:
        try:
            status = await anyio.to_thread.run_sync(
                partial(
                    service.get_status,
                    intent_id,
                    principal=principal,
                )
            )
        except Exception:
            _LOGGER.debug(
                "Could not poll upload %s for elicitation completion.",
                intent_id,
                exc_info=True,
            )
        else:
            if status.state in _UPLOAD_ELICITATION_COMPLETE_STATES:
                break
        remaining = (
            handoff_expires_at - datetime.now(timezone.utc)
        ).total_seconds()
        await anyio.sleep(
            min(_UPLOAD_ELICITATION_POLL_SECONDS, max(0, remaining))
        )
    try:
        await session.send_elicit_complete(elicitation_id)
    except Exception:
        _LOGGER.debug(
            "Could not deliver completion for upload elicitation %s.",
            elicitation_id,
            exc_info=True,
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


class MediaUploadHandoff(MediaUploadStatus):
    upload_page_url: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "HTTPS page for the user to open; the fragment carries the "
            "short-lived handoff capability."
        ),
    )
    handoff_expires_at: AwareDatetime
    elicitation_action: Literal[
        "unsupported",
        "accept",
        "decline",
        "cancel",
        "unavailable",
    ] = "unsupported"


def _upload_handoff_after_elicitation(
    handoff: MediaUploadHandoff,
    action: str,
) -> MediaUploadHandoff:
    next_action = {
        "accept": (
            "Complete the upload in the page approved by the MCP client, then "
            "call get_media_upload again."
        ),
        "decline": (
            "The user declined to open the page. The upload_page_url remains "
            "available if they choose to open it later."
        ),
        "cancel": (
            "The URL prompt was dismissed. The upload_page_url remains "
            "available for a later attempt."
        ),
        "unavailable": (
            "The client could not present the URL prompt. Open upload_page_url "
            "manually, then call get_media_upload again."
        ),
        "unsupported": handoff.next_action,
    }[action]
    return handoff.model_copy(
        update={
            "elicitation_action": action,
            "next_action": next_action,
        }
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
        lifespan=_mcp_lifespan,
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
            "Create an idempotent, metadata-only tus upload handoff. Return a "
            "normal HTTPS page for the user; video bytes never pass through "
            "MCP."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def create_media_upload(
        command: CreateUploadIntentCommand,
        idempotency_key: IdempotencyKey,
        mcp_context: Context,
    ) -> MediaUploadHandoff:
        def create(
            actor: Principal,
        ) -> tuple[MediaUploadHandoff, Principal]:
            service = _uploads(context)
            handoff = service.create_handoff(
                command,
                principal=actor,
                request_key=scoped_request_key(
                    principal=actor,
                    transport="mcp",
                    operation="media-upload",
                    idempotency_key=idempotency_key,
                ),
            )
            assert context.settings.upload_handoff_public_url is not None
            page_url = (
                f"{context.settings.upload_handoff_public_url}/"
                f"{handoff.status.intent_id}#capability="
                f"{quote(handoff.capability, safe='')}"
            )
            return (
                MediaUploadHandoff(
                    **handoff.status.model_dump(),
                    upload_page_url=page_url,
                    handoff_expires_at=handoff.expires_at,
                ),
                actor,
            )

        result, actor = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=create,
        )
        capabilities = mcp_context.client_capabilities
        elicitation = capabilities.elicitation if capabilities is not None else None
        if elicitation is None or elicitation.url is None:
            return _upload_handoff_after_elicitation(result, "unsupported")
        elicitation_id = f"vidxp-upload-{result.intent_id}-{uuid4().hex}"
        try:
            response = await mcp_context.elicit_url(
                message=(
                    "Open VidXP's secure page to upload the declared video "
                    "directly to the resumable upload service."
                ),
                url=result.upload_page_url,
                elicitation_id=elicitation_id,
            )
        except MCPError:
            _LOGGER.warning("The MCP client rejected URL elicitation delivery.")
            return _upload_handoff_after_elicitation(result, "unavailable")
        if response.action == "accept":
            lifespan = mcp_context.request_context.lifespan_context
            lifespan.task_group.start_soon(
                _complete_upload_elicitation,
                _uploads(context),
                actor,
                result.intent_id,
                result.handoff_expires_at,
                elicitation_id,
                mcp_context.session,
            )
        return _upload_handoff_after_elicitation(result, response.action)

    @server.tool(
        description=(
            "Get the current server-owned media upload state and the next "
            "valid action. Processing or failed imports include a job_id for "
            "get_job; ready uploads include a media_id for start_indexing."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_media_upload(
        intent_id: UploadIntentId,
    ) -> MediaUploadStatus:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda actor: _uploads(context).get_status(
                intent_id,
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
            "Return a readable MCP resource link for a completed clip or video "
            "artifact. The artifact_id is in the completed create_clip job "
            "result; clients read the link only when they need the video bytes."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_artifact_download(
        artifact_id: ArtifactId,
    ) -> ResourceLink:
        artifact = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: (
                context.application.get_artifact(artifact_id)
            ),
        )
        suffix = (
            "mp4"
            if artifact.mime_type == "video/mp4"
            else "mkv"
        )
        filename = f"{artifact.kind.value}-{artifact.artifact_id}.{suffix}"
        return ResourceLink(
            name=filename,
            title=f"VidXP {artifact.kind.value.replace('_', ' ')}",
            uri=(
                f"vidxp://artifacts/{artifact.artifact_id}/"
                f"content.{suffix}"
            ),
            description=(
                f"Generated from media {artifact.media_id}; "
                f"{artifact.byte_size:,} bytes."
            ),
            mimeType=artifact.mime_type,
            size=artifact.byte_size,
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
        stateless_http=False,
        json_response=False,
        max_request_body_size=(
            context.settings.mcp_max_request_body_bytes
        ),
        transport_security=transport_security,
        host=context.settings.http_bind_host,
    )
    server.session_manager.session_idle_timeout = (
        context.settings.mcp_session_idle_timeout_seconds
    )
    return RemoteMCP(
        server=server,
        app=PrincipalBridge(app),
        owns_authentication=owns_authentication,
        transport_security=transport_security,
    )
