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
    EvidenceArtifact,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    Identifier,
    IndexStatus,
    Job,
    JobId,
    JobKind,
    JobState,
    JobPage,
    ListJobsCommand,
    ListMediaCommand,
    LocalMediaIngestionCommand,
    MediaAsset,
    MediaId,
    MediaPage,
    MediaUploadSessionStatus,
    Principal,
    PrepareModelsCommand,
    QueryVideoCommand,
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
from vidxp.core.identifiers import ArtifactId
from vidxp.evidence_projection import project_job_artifacts
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
            "Media ingestion is unavailable on this transport.",
        )
    return context.uploads


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
            "artifacts are missing, submit prepare_models and poll get_job "
            "until it completes. "
            f"{ingestion_instructions}"
            "Automatic indexing uses every indexable capability exposed by "
            "the repository runtime unless modalities are supplied; set "
            "index_after_import=false only for advanced registration-only "
            "workflows. get_index_status identifies the "
            "media included in the active index snapshot. For search_moments "
            "and query_video, provide command.media_id to restrict work to one "
            "video, or omit it to search/query across every media item in that "
            "snapshot. MCP defaults to three ranked evidence frames; request "
            "keyframes_and_clips to receive bounded ready clips in the same "
            "completed job. The ordinary flow is submit search/query, then poll "
            "that job. create_evidence_clip, create_clip, and "
            "get_artifact_download remain advanced fallbacks. Use list_jobs to "
            "recover job IDs across agent sessions."
        ),
        version=__version__,
        token_verifier=token_verifier,
        auth=auth,
        lifespan=lifecycle,
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
                    "Use the verified local_path returned by "
                    "get_artifact_download."
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
        title="Search moments",
        description=(
            "Submit a durable ranked moment search. Set command.media_id to "
            "search one registered video; omit it to search across every media "
            "item in the active index snapshot. MCP defaults to the strongest "
            "three keyframes. Request keyframes_and_clips for same-job clips, "
            "then poll only this job for results and evidence."
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
                        "evidence_delivery": EvidenceDeliveryPolicy(
                            mode=EvidenceDeliveryMode.keyframes
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
            "moments and actor evidence. Set command.media_id for one video, "
            "or omit it to query across every media item in the active index "
            "snapshot. MCP defaults to the strongest three keyframes. Request "
            "keyframes_and_clips for same-job clips, then poll only this job "
            "for the grounded answer and inspectable evidence."
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
                        "evidence_delivery": EvidenceDeliveryPolicy(
                            mode=EvidenceDeliveryMode.keyframes
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
            source = context.jobs.get(source_job_id)
            if (
                source.state != JobState.succeeded
                or source.result is None
                or source.kind not in {JobKind.search, JobKind.query}
            ):
                raise ApplicationError(
                    "evidence_source_job_not_complete",
                    ErrorCategory.conflict,
                    "Evidence clips require a completed search or query job.",
                )
            result = source.result.result
            candidate, resolved = (
                context.application.evidence_delivery.resolve_job_evidence(
                    result,
                    evidence_id,
                    padding_before=padding_before_seconds,
                    padding_after=padding_after_seconds,
                )
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
            "Use get_job for transport-projected evidence and ResourceLinks."
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
            "Poll a durable VidXP job and its typed result. Completed search "
            "and query jobs include ranked evidence frames/resources and any "
            "requested ready clips in this same response."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_job(job_id: JobId) -> Annotated[CallToolResult, Job]:
        job = await _invoke_async(
            context,
            default_principal=default_principal,
            permission=RepositoryPermission.read,
            operation=lambda _actor: context.jobs.get(job_id),
        )
        blocks: list[ImageContent | ResourceLink | TextContent] = []
        projected = job
        if (
            job.state == JobState.succeeded
            and job.result is not None
            and job.kind in {JobKind.search, JobKind.query}
        ):
            result = job.result.result
            delivery = result.evidence_delivery
            if delivery is not None:
                projected_artifacts: dict[str, EvidenceArtifact] = {}
                for item in delivery.items:
                    keyframe = item.keyframe
                    clip = item.clip
                    label = (
                        f"evidence {item.evidence_id} rank {item.rank}; "
                        f"media {item.media_id}; "
                        f"{','.join(item.modalities)}"
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
                        projected_artifacts[
                            keyframe.artifact.artifact.artifact_id
                        ] = keyframe.artifact.model_copy(
                            update={
                                "resource_uri": frame_delivery.resource_uri,
                                "delivery": frame_delivery,
                            }
                        )
                        if (
                            keyframe.artifact.artifact.byte_size <= 512_000
                            and keyframe.artifact.artifact.byte_size
                            <= settings.mcp_max_resource_bytes
                            and keyframe.width <= 1280
                            and keyframe.height <= 1280
                        ):
                            image_bytes = await artifact_bytes(
                                keyframe.artifact.artifact.artifact_id,
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
                        projected_artifacts[clip.artifact.artifact_id] = clip.model_copy(
                            update={
                                "resource_uri": clip_delivery.resource_uri,
                                "delivery": clip_delivery,
                            }
                        )
                        if clip_link is not None:
                            blocks.append(clip_link)
                projected = project_job_artifacts(
                    job,
                    project_artifact=lambda evidence: projected_artifacts.get(
                        evidence.artifact.artifact_id,
                        evidence,
                    ),
                )
        if not blocks:
            blocks.append(
                TextContent(
                    type="text",
                    text=(f"VidXP job {job.job_id} is {job.state.value}."),
                )
            )
        return CallToolResult(
            content=blocks,
            structured_content=projected.model_dump(mode="json"),
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
