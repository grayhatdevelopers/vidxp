from __future__ import annotations

import logging
import json
import base64
from contextlib import asynccontextmanager
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
    ImageContent,
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
    Artifact,
    ArtifactDeliveryMode,
    ArtifactDownload,
    CapabilityInfo,
    CapabilityList,
    CreateSnippetCommand,
    CreateIndexCommand,
    ErrorCategory,
    ErrorDetail,
    EvidenceDeliveryItem,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    EvidenceDeliveryResult,
    EvidenceBoardResult,
    DEFAULT_JOB_WAIT_SECONDS,
    FusedSearchResult,
    Identifier,
    InitialEvidenceDeliveryPolicy,
    IndexStatus,
    Job,
    JobId,
    JobKind,
    JobPage,
    JobState,
    JobSummary,
    JobWaitResult,
    ListJobsCommand,
    ListMediaCommand,
    LocalMediaIngestionCommand,
    MAX_JOB_WAIT_SECONDS,
    MediaAsset,
    MediaId,
    MediaPage,
    MediaUploadSessionStatus,
    Principal,
    PrepareModelsCommand,
    QueryVideoCommand,
    QueryAnswer,
    RuntimeReadiness,
    SearchCommand,
    Sha256,
    SnippetProfile,
    WorkspaceOverview,
    UploadSessionId,
)
from vidxp.artifact_delivery import (
    ArtifactDownloadCapabilities,
    artifact_binding,
    require_resource_binding,
    verified_local_path,
)
from vidxp.capabilities.contracts import CapabilityRequestError
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
from vidxp.mcp_app import (
    MCP_APP_MIME_TYPE,
    MCP_APP_RESOURCE_URI,
    load_mcp_app_html,
)
from vidxp.core.identifiers import ArtifactId
from vidxp.evidence_delivery import (
    EvidenceDeliveryService,
    require_completed_evidence_result,
)
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


def _mcp_app_tool_meta(invoking: str, invoked: str) -> dict[str, object]:
    return {
        "ui": {"resourceUri": MCP_APP_RESOURCE_URI},
        "openai/outputTemplate": MCP_APP_RESOURCE_URI,
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }


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
            "Media ingestion is unavailable on this transport.",
        )
    return context.uploads


def _evidence_delivery(context: ControlPlaneContext) -> EvidenceDeliveryService:
    if context.evidence_delivery is None:
        raise ApplicationError(
            "evidence_delivery_unavailable",
            ErrorCategory.unavailable,
            "Evidence materialization is unavailable on this runtime.",
        )
    return context.evidence_delivery


def _ingestion_modalities(
    context: ControlPlaneContext,
    requested: tuple[Identifier, ...] | None,
) -> tuple[str, ...] | None:
    capabilities = getattr(context.application, "capabilities", None)
    registry = getattr(capabilities, "registry", None)
    if registry is None:
        return None if requested is None else tuple(requested)
    selected = registry.index_names() if requested is None else tuple(requested)
    try:
        validated = registry.validate_names(selected)
    except CapabilityRequestError as exc:
        raise ApplicationError(
            "ingestion_capabilities_unavailable",
            ErrorCategory.validation,
            str(exc),
            details={
                "remediation": (
                    "Choose indexable capabilities returned by get_workspace."
                )
            },
        ) from exc
    unsupported = tuple(
        name for name in validated if name not in registry.index_names()
    )
    if unsupported:
        raise ApplicationError(
            "ingestion_capabilities_unavailable",
            ErrorCategory.validation,
            "Automatic ingestion requested capabilities that cannot be indexed.",
            details={
                "remediation": (
                    "Choose indexable capabilities returned by get_workspace: "
                    + ", ".join(registry.index_names())
                )
            },
        )
    return validated


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


def _public_download_unavailable() -> ErrorDetail:
    return ErrorDetail(
        code="public_download_origin_unavailable",
        category=ErrorCategory.unavailable,
        message=(
            "Public artifact downloads are not configured; read the native "
            "MCP resource instead."
        ),
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
        if scope["type"] == "http" and str(scope.get("path", "")) in {"/mcp", "/mcp/"}:
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
    filesystem_accessible: bool = False,
) -> MCPServer:
    settings = context.settings
    token_verifier = None
    auth = None
    if oidc_authentication:
        assert settings.http_oidc_issuer is not None
        assert settings.mcp_public_url is not None
        if isinstance(context, HttpApplicationContext) and isinstance(
            context.authenticator,
            OIDCBearerAuthenticator,
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

    browser_upload_available = (
        not filesystem_accessible
        and context.uploads is not None
        and settings.upload_handoff_public_url is not None
    )
    if filesystem_accessible:
        ingestion_instructions = (
            "Use ingest_local_media for up to ten local paths; no media bytes pass "
            "through MCP. Poll get_media_ingestion until each successful file is "
            "indexed and searchable. "
        )
    elif browser_upload_available:
        ingestion_instructions = (
            "Use create_media_upload to give the user the returned capability "
            "page, then poll get_media_upload until every successful file is "
            "indexed and searchable. The returned status states whether the "
            "active backend is bounded multipart or resumable tus. "
        )
    else:
        ingestion_instructions = (
            "Browser upload tools are unavailable because this listener has no "
            "secure advertised upload-handoff origin. Configure an HTTPS "
            "VIDXP_UPLOAD_HANDOFF_PUBLIC_URL and dedicated secret, or use local "
            "stdio ingestion. "
        )

    @asynccontextmanager
    async def lifecycle(_server):
        context.start(eager_jobs=not filesystem_accessible)
        try:
            yield None
        finally:
            context.stop()

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
            "artifacts are missing, submit prepare_models and use wait_job "
            "until it completes, then fetch the full job once. "
            f"{ingestion_instructions}"
            "Automatic indexing uses every indexable capability exposed by "
            "the repository runtime unless modalities are supplied; set "
            "index_after_import=false only for advanced registration-only "
            "workflows. get_index_status identifies the "
            "media included in the active index snapshot. For search_moments "
            "and query_video, provide command.media_id to restrict work to one "
            "video, or omit it to search/query across every media item in that "
            "snapshot. MCP defaults to an annotated evidence board in the same "
            "completed search/query job; request keyframes or "
            "keyframes_and_clips only for standalone drill-down artifacts. The "
            "ordinary flow is submit search/query, use wait_job for bounded "
            "status observation, then call get_job once and "
            "inspect its board. Use create_evidence_board only for custom selections or "
            "continuation pages. Use materialize_job_evidence with evidence "
            "IDs from the completed result to inspect additional candidates in "
            "batches of ten without rerunning retrieval or supplying timestamps. "
            "create_evidence_clip, create_clip, and get_artifact_download remain "
            "advanced fallbacks. Use list_jobs to recover job IDs across agent "
            "sessions."
        ),
        version=__version__,
        token_verifier=token_verifier,
        auth=auth,
        lifespan=lifecycle,
    )

    @server.resource(
        MCP_APP_RESOURCE_URI,
        name="vidxp_mcp_app",
        title="VidXP video workspace",
        description=(
            "Interactive upload progress and evidence review for MCP Apps hosts."
        ),
        mime_type=MCP_APP_MIME_TYPE,
        meta={
            "ui": {
                "prefersBorder": True,
                "csp": {
                    "connectDomains": [],
                    "resourceDomains": [],
                },
            }
        },
    )
    async def read_mcp_app() -> str:
        return load_mcp_app_html()

    async def artifact_bytes(
        artifact_id: ArtifactId,
        *,
        expected_mime_type: str,
    ) -> bytes:
        resource = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.application.open_artifact_content(
                artifact_id
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
        maximum = settings.mcp_max_resource_bytes
        try:
            actual_size = await anyio.to_thread.run_sync(
                lambda: resource.path.stat().st_size
            )
        except OSError as exc:
            raise MCPError(
                -32603,
                "The artifact resource is unavailable.",
                {
                    "code": "artifact_resource_unavailable",
                    "category": "unavailable",
                    "retryable": True,
                },
            ) from exc
        if actual_size != resource.byte_size:
            raise MCPError(
                -32603,
                "The artifact resource size no longer matches its record.",
                {
                    "code": "artifact_resource_size_mismatch",
                    "category": "internal",
                    "recorded_bytes": resource.byte_size,
                    "actual_bytes": actual_size,
                    "retryable": False,
                },
            )
        if resource.byte_size > maximum or actual_size > maximum:
            if artifact_delivery == "local_stdio":
                remediation = (
                    "Use the verified local_path returned by get_artifact_download."
                )
            elif settings.artifact_download_public_url is not None:
                remediation = (
                    "Use the streaming/range-capable download_url returned by "
                    "get_artifact_download."
                )
            else:
                remediation = (
                    "Configure VIDXP_ARTIFACT_DOWNLOAD_PUBLIC_URL and its "
                    "dedicated secret, or request a smaller artifact."
                )
            raise MCPError(
                -32602,
                "The artifact is too large for an in-memory MCP resource read. "
                + remediation,
                {
                    "code": "artifact_resource_too_large",
                    "category": "resource_limit",
                    "maximum_bytes": maximum,
                    "actual_bytes": actual_size,
                    "remediation": remediation,
                    "retryable": False,
                },
            )

        def bounded_read() -> bytes:
            with resource.path.open("rb") as handle:
                return handle.read(maximum + 1)

        content = await anyio.to_thread.run_sync(bounded_read)
        if len(content) != actual_size:
            raise MCPError(
                -32603,
                "The artifact changed while it was being read.",
                {
                    "code": "artifact_resource_size_mismatch",
                    "category": "internal",
                    "recorded_bytes": resource.byte_size,
                    "actual_bytes": len(content),
                    "retryable": False,
                },
            )
        return content

    @server.resource(
        "vidxp://artifacts/{artifact_id}/content.mp4",
        name="vidxp_artifact_mp4",
        title="VidXP MP4 artifact",
        description=("Binary content for a generated VidXP clip or video artifact."),
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
        description=("Binary content for a source-profile VidXP clip artifact."),
        mime_type="video/x-matroska",
    )
    async def read_matroska_artifact(artifact_id: ArtifactId) -> bytes:
        return await artifact_bytes(
            artifact_id,
            expected_mime_type="video/x-matroska",
        )

    @server.resource(
        "vidxp://artifacts/{artifact_id}/content.png",
        name="vidxp_evidence_frame_png",
        title="VidXP evidence frame",
        description="PNG frame extracted from authoritative indexed evidence.",
        mime_type="image/png",
    )
    async def read_png_artifact(artifact_id: ArtifactId) -> bytes:
        return await artifact_bytes(
            artifact_id,
            expected_mime_type="image/png",
        )

    @server.resource(
        "vidxp://artifacts/{artifact_id}/content.jpg",
        name="vidxp_evidence_board_jpeg",
        title="VidXP evidence board",
        description="JPEG overview page compiled from authoritative evidence frames.",
        mime_type="image/jpeg",
    )
    async def read_jpeg_artifact(artifact_id: ArtifactId) -> bytes:
        return await artifact_bytes(
            artifact_id,
            expected_mime_type="image/jpeg",
        )

    async def project_artifact_delivery(
        artifact: Artifact,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> tuple[ArtifactDownload, ResourceLink | None]:
        binding = _translate_application_result(lambda: artifact_binding(artifact))
        resource_uri = None
        link = None
        if artifact.byte_size <= settings.mcp_max_resource_bytes:
            resource_uri = (
                f"vidxp://artifacts/{artifact.artifact_id}/content.{binding.extension}"
            )
            link = ResourceLink(
                name=binding.filename,
                title=title or f"VidXP {artifact.kind.value.replace('_', ' ')}",
                uri=resource_uri,
                description=description
                or (
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
        if (
            artifact_delivery == "streamable_http"
            and context.settings.artifact_download_public_url is not None
        ):
            issued = _translate_application_result(
                lambda: ArtifactDownloadCapabilities(context.settings).issue(artifact)
            )
            delivery_mode = ArtifactDeliveryMode.https_download
            download_url = issued.url
            download_expires_at = issued.expires_at
        elif artifact_delivery == "streamable_http":
            if link is not None:
                delivery_mode = ArtifactDeliveryMode.mcp_resource
                delivery_error = _public_download_unavailable()
            else:
                delivery_mode = ArtifactDeliveryMode.unavailable
                delivery_error = ErrorDetail(
                    code="artifact_delivery_unavailable",
                    category=ErrorCategory.unavailable,
                    message=(
                        "This artifact exceeds VIDXP_MCP_MAX_RESOURCE_BYTES. "
                        "Configure VIDXP_ARTIFACT_DOWNLOAD_PUBLIC_URL and "
                        "VIDXP_ARTIFACT_DOWNLOAD_SECRET on the HTTP listener."
                    ),
                )
        elif context.settings.mcp_stdio_filesystem_accessible:
            resource = await _invoke_async(
                context,
                default_principal=default_principal,
                permission=RepositoryPermission.read,
                operation=lambda _actor: context.application.open_artifact_content(
                    artifact.artifact_id
                ),
            )
            _translate_application_result(
                lambda: require_resource_binding(binding, resource)
            )
            resolved = _translate_application_result(
                lambda: verified_local_path(resource.path)
            )
            delivery_mode = ArtifactDeliveryMode.local_file
            local_path = str(resolved)
            file_uri = resolved.as_uri()
        elif context.settings.artifact_download_public_url is not None:
            issued = _translate_application_result(
                lambda: ArtifactDownloadCapabilities(context.settings).issue(artifact)
            )
            delivery_mode = ArtifactDeliveryMode.https_download
            download_url = issued.url
            download_expires_at = issued.expires_at
        else:
            if link is not None:
                delivery_mode = ArtifactDeliveryMode.mcp_resource
                delivery_error = ErrorDetail(
                    code="local_path_unavailable",
                    category=ErrorCategory.unavailable,
                    message=(
                        "The stdio client is configured as filesystem-isolated; "
                        "read the MCP resource or configure a public download origin."
                    ),
                )
            else:
                delivery_mode = ArtifactDeliveryMode.unavailable
                delivery_error = ErrorDetail(
                    code="artifact_delivery_unavailable",
                    category=ErrorCategory.unavailable,
                    message=(
                        "This artifact exceeds VIDXP_MCP_MAX_RESOURCE_BYTES and "
                        "the client cannot access local files. Configure "
                        "VIDXP_ARTIFACT_DOWNLOAD_PUBLIC_URL and "
                        "VIDXP_ARTIFACT_DOWNLOAD_SECRET."
                    ),
                )
        return ArtifactDownload(
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
        ), link

    async def project_evidence_delivery(
        delivery: EvidenceDeliveryResult,
    ) -> tuple[
        EvidenceDeliveryResult,
        list[ImageContent | ResourceLink | TextContent],
    ]:
        projected_board = None
        blocks: list[ImageContent | ResourceLink | TextContent] = []
        if delivery.board is not None:
            projected_board, board_blocks = await project_evidence_board(delivery.board)
            blocks.extend(board_blocks)
        projected_items = []
        for item in delivery.items:
            keyframe = item.keyframe
            clip = item.clip
            label = (
                f"evidence {item.evidence_id} rank {item.rank}; "
                f"media {item.media_id}; {','.join(item.modalities)}"
            )
            if item.range is not None:
                label += (
                    f"; {item.range.source_start_seconds:.3f}-"
                    f"{item.range.source_end_seconds:.3f}s"
                )
            if keyframe is not None:
                frame_delivery, frame_link = await project_artifact_delivery(
                    keyframe.artifact.artifact,
                    title=f"VidXP evidence frame #{item.rank}",
                    description=label,
                )
                keyframe = keyframe.model_copy(
                    update={
                        "artifact": keyframe.artifact.model_copy(
                            update={
                                "resource_uri": frame_delivery.resource_uri,
                                "delivery": frame_delivery,
                            }
                        )
                    }
                )
                frame = keyframe.artifact.artifact
                if (
                    frame.byte_size <= 512_000
                    and frame.byte_size <= settings.mcp_max_resource_bytes
                    and keyframe.width <= 1280
                    and keyframe.height <= 1280
                ):
                    image_bytes = await artifact_bytes(
                        frame.artifact_id,
                        expected_mime_type="image/png",
                    )
                    blocks.append(
                        ImageContent(
                            data=base64.b64encode(image_bytes).decode(),
                            mimeType="image/png",
                        )
                    )
                if frame_link is not None:
                    blocks.append(frame_link)
            if clip is not None and item.range is not None:
                clip_delivery, clip_link = await project_artifact_delivery(
                    clip.artifact,
                    title=f"VidXP evidence clip #{item.rank}",
                    description=(
                        f"{label}; resolved clip "
                        f"{item.range.clip_start_seconds:.3f}-"
                        f"{item.range.clip_end_seconds:.3f}s"
                    ),
                )
                clip = clip.model_copy(
                    update={
                        "resource_uri": clip_delivery.resource_uri,
                        "delivery": clip_delivery,
                    }
                )
                if clip_link is not None:
                    blocks.append(clip_link)
            projected_items.append(
                item.model_copy(update={"keyframe": keyframe, "clip": clip})
            )
        return (
            delivery.model_copy(
                update={
                    "items": tuple(projected_items),
                    "board": projected_board,
                }
            ),
            blocks,
        )

    async def project_evidence_board(
        board: EvidenceBoardResult,
    ) -> tuple[EvidenceBoardResult, list[ImageContent | ResourceLink | TextContent]]:
        blocks: list[ImageContent | ResourceLink | TextContent] = []
        projected_pages = []
        inline_budget = min(settings.mcp_max_resource_bytes, 4 * 1024 * 1024)
        inlined_bytes = 0
        for page in board.pages:
            artifact = page.artifact.artifact
            delivery, link = await project_artifact_delivery(
                artifact,
                title=f"VidXP evidence board page {page.page_number}",
                description=(
                    f"Evidence overview for media {page.media_id}; "
                    f"{len(page.tile_ids)} tiles."
                ),
            )
            projected_pages.append(
                page.model_copy(
                    update={
                        "artifact": page.artifact.model_copy(
                            update={
                                "resource_uri": delivery.resource_uri,
                                "delivery": delivery,
                            }
                        )
                    }
                )
            )
            if (
                artifact.byte_size <= inline_budget - inlined_bytes
                and artifact.byte_size <= settings.mcp_max_resource_bytes
            ):
                image_bytes = await artifact_bytes(
                    artifact.artifact_id,
                    expected_mime_type="image/jpeg",
                )
                blocks.append(
                    ImageContent(
                        data=base64.b64encode(image_bytes).decode(),
                        mimeType="image/jpeg",
                    )
                )
                inlined_bytes += artifact.byte_size
            if link is not None:
                blocks.append(link)
        return board.model_copy(update={"pages": tuple(projected_pages)}), blocks

    def concise_text(value: str | None, *, limit: int = 320) -> str | None:
        if value is None:
            return None
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 1].rstrip()}…"

    def presentation_target(delivery: ArtifactDownload | None) -> str | None:
        if delivery is None:
            return None
        if delivery.local_path is not None:
            return f"local_path={delivery.local_path}"
        if delivery.download_url is not None:
            return f"download_url={delivery.download_url}"
        if delivery.resource_uri is not None:
            return f"resource_uri={delivery.resource_uri}"
        return None

    def presentation_artifacts(delivery: EvidenceDeliveryResult) -> list[str]:
        artifacts: list[str] = []
        if delivery.board is not None:
            for page in delivery.board.pages:
                target = presentation_target(page.artifact.delivery)
                if target is not None:
                    artifacts.append(f"- board page {page.page_number} | {target}")
        for item in delivery.items:
            if item.keyframe is not None:
                target = presentation_target(item.keyframe.artifact.delivery)
                if target is not None:
                    artifacts.append(f"- evidence {item.evidence_id} frame | {target}")
            if item.clip is not None:
                target = presentation_target(item.clip.delivery)
                if target is not None:
                    artifacts.append(f"- evidence {item.evidence_id} clip | {target}")
        return artifacts

    def evidence_index(
        *,
        source_job_id: JobId,
        delivery: EvidenceDeliveryResult,
        query_result: QueryAnswer | None = None,
    ) -> str:
        lines = [f"VidXP evidence for job {source_job_id}."]
        if query_result is not None:
            if query_result.claims:
                lines.append("Grounded answer:")
                for claim in query_result.claims:
                    text = concise_text(claim.text, limit=512) or ""
                    citations = ", ".join(claim.evidence_ids)
                    lines.append(f"- {text} [evidence: {citations}]")
            elif query_result.fallback_reason:
                lines.append(
                    "Answer unavailable: "
                    + (concise_text(query_result.fallback_reason, limit=512) or "")
                )

        board = delivery.board
        if board is not None:
            lines.append(
                f"Board: {board.rendered_count}/{board.requested_count} candidates "
                f"rendered across {len(board.pages)} page(s); "
                f"{board.failed_count} failed."
            )
            candidates = board.tiles
        else:
            candidates = delivery.items

        if candidates:
            lines.append(
                "Evidence index (rank | source seconds | modalities | ID | label):"
            )
        for candidate in candidates:
            if isinstance(candidate, EvidenceDeliveryItem):
                resolved = candidate.range
                start = resolved.source_start_seconds if resolved is not None else 0.0
                end = resolved.source_end_seconds if resolved is not None else start
                label = None
            else:
                start = candidate.start
                end = candidate.end
                label = concise_text(candidate.display_text)
            line = (
                f"- {candidate.rank} | {start:.3f}-{end:.3f} | "
                f"{','.join(candidate.modalities)} | {candidate.evidence_id}"
            )
            if label:
                line += f" | {label}"
            lines.append(line)

        artifacts = presentation_artifacts(delivery)
        if artifacts:
            lines.append(
                "User-presentable artifacts (embed local_path or link download_url; "
                "do not invent an unlinked evidence label):"
            )
            lines.extend(artifacts)

        if board is not None and board.next_start_rank is not None:
            lines.append(f"More candidates start at rank {board.next_start_rank}.")
        lines.append(
            "Use materialize_job_evidence with this job ID and up to ten evidence "
            "IDs for standalone frames or clips."
        )
        return "\n".join(lines)

    def evidence_app_payload(
        *,
        job: Job,
        source_job_id: JobId,
        delivery: EvidenceDeliveryResult,
        query_result: QueryAnswer | None,
    ) -> dict[str, object]:
        board = delivery.board
        pages: list[dict[str, object]] = []
        tiles: list[dict[str, object]] = []
        if board is not None:
            for page in board.pages:
                artifact = page.artifact
                delivery_info = artifact.delivery
                pages.append(
                    {
                        "page_number": page.page_number,
                        "media_id": page.media_id,
                        "width": page.width,
                        "height": page.height,
                        "tile_ids": list(page.tile_ids),
                        "resource_uri": artifact.resource_uri,
                        "download_url": (
                            delivery_info.download_url
                            if delivery_info is not None
                            else None
                        ),
                    }
                )
            for tile in board.tiles:
                tiles.append(
                    {
                        "evidence_id": tile.evidence_id,
                        "rank": tile.rank,
                        "page_number": tile.page_number,
                        "position": tile.position,
                        "media_id": tile.media_id,
                        "modalities": list(tile.modalities),
                        "start": tile.start,
                        "end": tile.end,
                        "display_text": concise_text(tile.display_text),
                        "state": tile.state.value,
                    }
                )
            requested_count = board.requested_count
            rendered_count = board.rendered_count
            failed_count = board.failed_count
            next_start_rank = board.next_start_rank
        else:
            for item in delivery.items:
                resolved = item.range
                tiles.append(
                    {
                        "evidence_id": item.evidence_id,
                        "rank": item.rank,
                        "page_number": None,
                        "position": item.rank,
                        "media_id": item.media_id,
                        "modalities": list(item.modalities),
                        "start": (
                            resolved.source_start_seconds
                            if resolved is not None
                            else 0.0
                        ),
                        "end": (
                            resolved.source_end_seconds
                            if resolved is not None
                            else 0.0
                        ),
                        "display_text": None,
                        "state": item.state.value,
                    }
                )
            requested_count = len(delivery.items)
            rendered_count = sum(
                item.state.value == "ready" for item in delivery.items
            )
            failed_count = requested_count - rendered_count
            next_start_rank = None

        answer: dict[str, object] | None = None
        if query_result is not None:
            answer = {
                "mode": query_result.mode.value,
                "claims": [
                    {
                        "text": concise_text(claim.text, limit=512) or "",
                        "evidence_ids": list(claim.evidence_ids),
                    }
                    for claim in query_result.claims
                ],
                "fallback_reason": concise_text(
                    query_result.fallback_reason,
                    limit=512,
                ),
            }

        return {
            "view": "evidence",
            "job_id": job.job_id,
            "source_job_id": source_job_id,
            "job_kind": job.kind.value,
            "answer": answer,
            "board": {
                "requested_count": requested_count,
                "rendered_count": rendered_count,
                "failed_count": failed_count,
                "next_start_rank": next_start_rank,
                "pages": pages,
                "tiles": tiles,
            },
        }

    async def evidence_presentation(
        job: Job,
    ) -> tuple[
        dict[str, object],
        list[ImageContent | ResourceLink | TextContent],
    ]:
        query_result = None
        if job.kind in {JobKind.search, JobKind.query}:
            result = job.result.result
            delivery = result.evidence_delivery
            if delivery is None:
                raise ApplicationError(
                    "job_evidence_unavailable",
                    ErrorCategory.conflict,
                    "The completed job does not contain deliverable evidence.",
                )
            if job.kind == JobKind.query:
                query_result = result
            projected_delivery, blocks = await project_evidence_delivery(delivery)
            index = evidence_index(
                source_job_id=job.job_id,
                delivery=projected_delivery,
                query_result=query_result,
            )
            source_job_id = job.job_id
        else:
            board = job.result.result
            projected_board, blocks = await project_evidence_board(board)
            projected_delivery = EvidenceDeliveryResult(
                policy=EvidenceDeliveryPolicy(mode=EvidenceDeliveryMode.none),
                items=(),
                board=projected_board,
            )
            index = evidence_index(
                source_job_id=board.source_job_id,
                delivery=projected_delivery,
            )
            source_job_id = board.source_job_id
        payload = evidence_app_payload(
            job=job,
            source_job_id=source_job_id,
            delivery=projected_delivery,
            query_result=query_result,
        )
        return payload, [TextContent(type="text", text=index), *blocks]

    def completed_evidence_result(
        source_job_id: JobId,
    ) -> FusedSearchResult | QueryAnswer:
        return require_completed_evidence_result(context.jobs.get(source_job_id))

    @server.tool(
        title="Inspect VidXP workspace",
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
        title="List capabilities",
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
        title="Get capability",
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
        title="Check runtime readiness",
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
        title="List media",
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
        title="Get media",
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
        title="Create media upload",
        description=(
            "Create an idempotent multi-file upload session and return its "
            "short-lived capability link. The user selects files in the "
            "browser; filenames, sizes, MIME types, and video bytes are not "
            "tool inputs. Native vidxp-api uses bounded non-resumable "
            "multipart transfer; deployed server mode retains resumable tus. "
            "Automatic indexing defaults on. Poll only get_media_upload."
        ),
        annotations=_SUBMIT,
        meta=_mcp_app_tool_meta(
            "Creating a VidXP upload session…",
            "VidXP upload session ready.",
        ),
        structured_output=True,
    )
    async def create_media_upload(
        idempotency_key: IdempotencyKey,
        index_after_import: bool = True,
        modalities: tuple[Identifier, ...] | None = None,
    ) -> Annotated[CallToolResult, MediaUploadSessionLink]:
        def create(actor: Principal):
            selected = _ingestion_modalities(context, modalities)
            return _uploads(context).create_upload_session(
                principal=actor,
                request_key=scoped_request_key(
                    principal=actor,
                    transport="mcp",
                    operation="media-upload-session",
                    idempotency_key=idempotency_key,
                ),
                index_after_import=index_after_import,
                index_modalities=selected,
            )

        link = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=create,
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
        title="Get media upload",
        description=(
            "Get durable aggregate and per-file state for an upload session. "
            "Poll this single operation through uploaded, importing, "
            "registered, indexing, indexed, index_failed, or failed. Each item "
            "reports the import job, index job, media, generation, snapshot, "
            "and structured "
            "failure without losing successful siblings. terminal=true means "
            "the currently accepted files are complete and polling should stop; "
            "session_state=open may still allow the user to add another file, "
            "which makes the status non-terminal again."
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
        title="Ingest local media",
        description=(
            "Local stdio only: ingest one to ten filesystem paths without "
            "transferring media bytes through MCP. Paths are canonicalized and "
            "checked against trusted import roots. Automatic indexing defaults "
            "on; poll only get_media_ingestion for partial per-file results."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def ingest_local_media(
        command: LocalMediaIngestionCommand,
        idempotency_key: IdempotencyKey,
    ) -> MediaUploadSessionStatus:
        def ingest(actor: Principal) -> MediaUploadSessionStatus:
            selected = _ingestion_modalities(context, command.modalities)
            return _uploads(context).create_local_ingestion(
                command.paths,
                principal=actor,
                request_key=scoped_request_key(
                    principal=actor,
                    transport="mcp-stdio",
                    operation="local-media-ingestion",
                    idempotency_key=idempotency_key,
                ),
                index_after_import=command.index_after_import,
                index_modalities=selected,
            )

        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=ingest,
        )

    @server.tool(
        title="Get media ingestion",
        description=(
            "Local stdio only: recover and poll one durable local-path "
            "ingestion batch until every successful file is indexed/searchable "
            "or has an explicit terminal failure."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_media_ingestion(
        ingestion_id: UploadSessionId,
    ) -> MediaUploadSessionStatus:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda actor: _uploads(context).get_status(
                ingestion_id,
                principal=actor,
            ),
        )

    @server.tool(
        title="Get index status",
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
        title="Start indexing",
        description=(
            "Add or replace one registered media ID in the active multi-video "
            "index snapshot. Obtain the ID from list_media or a completed "
            "upload, then observe it with wait_job and fetch get_job once."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def start_indexing(
        command: CreateIndexCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            job_id = scoped_job_id(
                principal=actor,
                transport="mcp",
                operation="index",
                idempotency_key=idempotency_key,
            )
            if context.uploads is not None:
                return context.uploads.start_indexing(command, job_id=job_id)
            return context.jobs.submit_index(command, job_id=job_id)

        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.write,
            operation=submit,
        )

    @server.tool(
        title="Prepare models",
        description=(
            "Explicitly download and validate selected model artifacts. Poll "
            "with wait_job for byte progress and completion before indexing."
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
        title="Search moments",
        description=(
            "Submit a durable ranked moment search. Set command.media_id to "
            "search one registered video; omit it to search across every media "
            "item in the active index snapshot. MCP returns an annotated board "
            "of ranked results by default. Set command.evidence_delivery.mode "
            "to keyframes or keyframes_and_clips only when standalone artifacts "
            "are also needed, then use wait_job and get_job_evidence."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def search_moments(
        command: SearchCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            projected = command
            if command.evidence_delivery is None:
                projected = command.model_copy(
                    update={
                        "evidence_delivery": InitialEvidenceDeliveryPolicy(
                            mode=EvidenceDeliveryMode.none
                        )
                    }
                )
            return context.jobs.submit_search(
                projected,
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
            permission=(
                RepositoryPermission.write
                if command.evidence_delivery is not None
                and command.evidence_delivery.mode
                == EvidenceDeliveryMode.keyframes_and_clips
                else RepositoryPermission.read
            ),
            operation=submit,
        )

    @server.tool(
        title="Query video",
        description=(
            "Submit a durable grounded natural-language query over indexed "
            "moments and actor evidence. Put the question in command.question. "
            "Set command.media_id for one video, "
            "or omit it to query across every media item in the active index "
            "snapshot. MCP returns an annotated board of ranked evidence by "
            "default. Set command.evidence_delivery.mode to keyframes or "
            "keyframes_and_clips only when standalone artifacts are also needed, "
            "then use wait_job and get_job_evidence."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def query_video(
        command: QueryVideoCommand,
        idempotency_key: IdempotencyKey,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            projected = command
            if command.evidence_delivery is None:
                projected = command.model_copy(
                    update={
                        "evidence_delivery": InitialEvidenceDeliveryPolicy(
                            mode=EvidenceDeliveryMode.none
                        )
                    }
                )
            return context.jobs.submit_query(
                projected,
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
            permission=(
                RepositoryPermission.write
                if command.evidence_delivery is not None
                and command.evidence_delivery.mode
                == EvidenceDeliveryMode.keyframes_and_clips
                else RepositoryPermission.read
            ),
            operation=submit,
        )

    @server.tool(
        title="Create clip",
        description=(
            "Create a downloadable clip from a media ID and time range returned "
            "by search_moments or query_video. Wait with wait_job, then pass the "
            "completed get_job result's artifact_id to get_artifact_download."
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
        title="Create evidence clip",
        description=(
            "Advanced fallback: create a clip from authoritative evidence in a "
            "completed search/query job. VidXP resolves and clamps the range; "
            "the caller never supplies timestamps."
        ),
        annotations=_SUBMIT,
        structured_output=True,
    )
    async def create_evidence_clip(
        source_job_id: JobId,
        evidence_id: Sha256,
        idempotency_key: IdempotencyKey,
        padding_before_seconds: Annotated[float, Field(ge=0, le=30)] = 2.0,
        padding_after_seconds: Annotated[float, Field(ge=0, le=30)] = 2.0,
        profile: SnippetProfile = SnippetProfile.compatible_mp4,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            result = completed_evidence_result(source_job_id)
            candidate, resolved = _evidence_delivery(context).resolve_job_evidence(
                result,
                evidence_id,
                padding_before=padding_before_seconds,
                padding_after=padding_after_seconds,
            )
            return context.jobs.submit_snippet(
                CreateSnippetCommand(
                    media_id=candidate.media_id,
                    start_seconds=resolved.clip_start_seconds,
                    end_seconds=resolved.clip_end_seconds,
                    profile=profile,
                ),
                job_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation=(
                        f"evidence-clip:{source_job_id}:{evidence_id}:"
                        f"{padding_before_seconds}:{padding_after_seconds}:"
                        f"{profile.value}"
                    ),
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
        title="Materialize job evidence",
        description=(
            "Prepare keyframes and optional clips for one to ten evidence IDs "
            "from a completed search/query job. Use this to inspect candidates "
            "outside the initial bounded evidence delivery without supplying "
            "timestamps or rerunning retrieval. Returns model-visible images "
            "and links without duplicating the full structured result."
        ),
        annotations=_SUBMIT,
    )
    async def materialize_job_evidence(
        source_job_id: JobId,
        evidence_ids: Annotated[
            tuple[Sha256, ...],
            Field(min_length=1, max_length=10),
        ],
        mode: Literal[
            EvidenceDeliveryMode.keyframes,
            EvidenceDeliveryMode.keyframes_and_clips,
        ] = EvidenceDeliveryMode.keyframes,
        padding_before_seconds: Annotated[float, Field(ge=0, le=30)] = 2.0,
        padding_after_seconds: Annotated[float, Field(ge=0, le=30)] = 2.0,
        profile: SnippetProfile = SnippetProfile.compatible_mp4,
    ) -> CallToolResult:
        if len(evidence_ids) != len(set(evidence_ids)):
            raise _application_error(
                ApplicationError(
                    "duplicate_evidence_ids",
                    ErrorCategory.validation,
                    "Evidence IDs must be unique within one materialization request.",
                )
            )

        def materialize(_actor: Principal) -> EvidenceDeliveryResult:
            result = completed_evidence_result(source_job_id)
            return _evidence_delivery(context).deliver_selected(
                result,
                evidence_ids,
                EvidenceDeliveryPolicy(
                    mode=mode,
                    max_items=len(evidence_ids),
                    padding_before_seconds=padding_before_seconds,
                    padding_after_seconds=padding_after_seconds,
                    clip_profile=profile,
                ),
            )

        delivery = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=(
                RepositoryPermission.write
                if mode == EvidenceDeliveryMode.keyframes_and_clips
                else RepositoryPermission.read
            ),
            operation=materialize,
        )
        projected_delivery, blocks = await project_evidence_delivery(delivery)
        blocks.insert(
            0,
            TextContent(
                type="text",
                text=evidence_index(
                    source_job_id=source_job_id,
                    delivery=projected_delivery,
                ),
            ),
        )
        return CallToolResult(content=blocks)

    @server.tool(
        title="Create evidence board",
        description=(
            "Compile ranked evidence from a completed search/query job into "
            "media-separated annotated image pages. Use the returned tile map "
            "to choose evidence IDs for exact frame or clip drill-down. Boards "
            "use bounded pages and return next_start_rank when more remain."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def create_evidence_board(
        source_job_id: JobId,
        idempotency_key: IdempotencyKey,
        evidence_ids: Annotated[
            tuple[Sha256, ...] | None,
            Field(max_length=200),
        ] = None,
        start_rank: Annotated[int, Field(ge=1, le=200)] = 1,
    ) -> Job:
        def submit(actor: Principal) -> Job:
            source = completed_evidence_result(source_job_id)
            prepared = _evidence_delivery(context).prepare_board_request(
                source_job_id=source_job_id,
                evidence_ids=evidence_ids,
                start_rank=start_rank,
                result=source,
            )
            return context.jobs.submit_evidence_board(
                prepared,
                job_id=scoped_job_id(
                    principal=actor,
                    transport="mcp",
                    operation="evidence-board",
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
        title="Get artifact download",
        description=(
            "Return a native lazy MCP ResourceLink plus transport-authoritative "
            "delivery metadata for a completed clip or video artifact. Resource "
            "bytes are available only when the artifact is within "
            "VIDXP_MCP_MAX_RESOURCE_BYTES. Local stdio may expose its verified "
            "path; Streamable HTTP uses the structured delivery mode and, when "
            "configured, a short-lived HTTPS download without server paths."
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
            operation=lambda _actor: context.application.get_artifact(artifact_id),
        )
        result, link = await project_artifact_delivery(artifact)
        return CallToolResult(
            content=[] if link is None else [link],
            structured_content=result.model_dump(mode="json"),
        )

    @server.tool(
        title="List jobs",
        description=(
            "List durable jobs and IDs so work can be recovered across sessions. "
            "Use get_job_evidence to present completed search/query evidence, or "
            "get_job for the full machine record."
        ),
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
        title="Get job",
        description=(
            "Fetch the full typed machine record for a durable VidXP job. Use "
            "get_job_status or wait_job while work is active. For completed "
            "search/query evidence, prefer get_job_evidence so images remain "
            "model-visible without duplicating this full record."
        ),
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
        title="Present job evidence",
        description=(
            "Present a completed search, query, or evidence-board job as a "
            "concise evidence index plus model-visible board images and resource "
            "links. The compact structured result drives the optional VidXP "
            "evidence-review UI without exposing the full machine record."
        ),
        annotations=_READ_ONLY,
        meta=_mcp_app_tool_meta(
            "Opening VidXP evidence…",
            "VidXP evidence ready.",
        ),
    )
    async def get_job_evidence(job_id: JobId) -> CallToolResult:
        def completed_evidence_job(_actor: Principal) -> Job:
            job = context.jobs.get(job_id)
            if (
                job.state != JobState.succeeded
                or job.result is None
                or job.kind
                not in {JobKind.search, JobKind.query, JobKind.evidence_board}
            ):
                raise ApplicationError(
                    "job_evidence_not_ready",
                    ErrorCategory.conflict,
                    "Evidence presentation requires a completed search, query, "
                    "or evidence-board job.",
                )
            return job

        job = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=completed_evidence_job,
        )
        try:
            structured_content, blocks = await evidence_presentation(job)
        except ApplicationError as exc:
            raise _application_error(exc) from exc
        return CallToolResult(
            content=blocks,
            structured_content=structured_content,
        )

    @server.tool(
        title="Get compact job status",
        description=(
            "Return compact durable job status without its typed result, "
            "evidence payloads, or ResourceLinks. Prefer wait_job after this "
            "initial observation."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_job_status(job_id: JobId) -> JobSummary:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.jobs.summary(job_id),
        )

    @server.tool(
        title="Wait for job change",
        description=(
            "Wait up to 30 seconds for a durable job to change stage or reach a "
            "terminal state. Pass the previous observation token on subsequent "
            "calls. Evidence rendering is treated as one observable phase "
            "rather than waking once per artifact. Returns compact status only; "
            "after completion use "
            "get_job_evidence for visual search/query output, or get_job when "
            "the full machine record is needed."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def wait_job(
        job_id: JobId,
        after_observation_token: Sha256 | None = None,
        timeout_seconds: Annotated[
            int,
            Field(gt=0, le=MAX_JOB_WAIT_SECONDS),
        ] = DEFAULT_JOB_WAIT_SECONDS,
    ) -> JobWaitResult:
        return await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.jobs.wait_for_change(
                job_id,
                after=after_observation_token,
                timeout_seconds=timeout_seconds,
            ),
        )

    @server.tool(
        title="Retry job",
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
        title="Cancel job",
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

    if filesystem_accessible:
        server.remove_tool("create_media_upload")
        server.remove_tool("get_media_upload")
    else:
        server.remove_tool("ingest_local_media")
        server.remove_tool("get_media_ingestion")
        if not browser_upload_available:
            server.remove_tool("create_media_upload")
            server.remove_tool("get_media_upload")
    return server


def create_remote_mcp(context: ControlPlaneContext) -> RemoteMCP:
    owns_authentication = context.settings.http_auth_mode == HttpAuthMode.oidc
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
        max_request_body_size=(context.settings.mcp_max_request_body_bytes),
        transport_security=transport_security,
        host=context.settings.http_bind_host,
    )
    return RemoteMCP(
        server=server,
        app=PrincipalBridge(app),
        owns_authentication=owns_authentication,
        transport_security=transport_security,
    )
