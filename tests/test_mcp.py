import asyncio
import base64
import contextlib
import hashlib
import io
import json
import shutil
import socket
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx2
import uvicorn
from fastapi.testclient import TestClient
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import ImageContent, ResourceLink

from vidxp.application_models import (
    ApplicationError,
    Artifact,
    EvidenceArtifact,
    EvidenceBoardCandidate,
    EvidenceBoardJobRequest,
    EvidenceBoardPage,
    EvidenceBoardResult,
    EvidenceBoardTile,
    EvidenceDeliveryItem,
    EvidenceDeliveryResult,
    EvidenceDeliveryState,
    EvidenceFrameMatch,
    EvidenceKeyframe,
    EvidenceRangeResolution,
    FusedMoment,
    FusedSearchResult,
    FusionProvenance,
    GroundedClaim,
    ErrorCategory,
    ErrorDetail,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    IndexStatus,
    InitialEvidenceDeliveryPolicy,
    Job,
    JobKind,
    JobPage,
    JobQueue,
    JobState,
    JobWaitResult,
    SearchHit,
    SearchJobResult,
    MediaPage,
    MediaUploadSessionStatus,
    MediaUploadStatus,
    MomentEvidence,
    Principal,
    QueryVideoCommand,
    QueryAnswer,
    QueryAnswerMode,
    QueryJobResult,
    QueryPlan,
    SearchMomentsPlanStep,
    WorkspaceOverview,
)
from vidxp.mcp_app import MCP_APP_MIME_TYPE, MCP_APP_RESOURCE_URI
from vidxp.authentication import (
    AuthenticatedBearer,
    OIDCBearerAuthenticator,
    create_authenticator,
)
from vidxp.api import create_app
from vidxp.authorization import AuthorizationPolicy
from vidxp.branding import (
    ICON_MIME_TYPE,
    ICON_SIZE,
    PROJECT_URL,
    icon_bytes,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.composition import HttpApplicationContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.core.artifacts import ArtifactKind, ArtifactState
from vidxp.core.media import MediaState
from vidxp.core.uploads import UploadSessionState, UploadState
from vidxp.job_service import JobService
from vidxp.mcp import VidXPTokenVerifier, create_mcp_server, create_remote_mcp
from vidxp.mcp_cli import main as mcp_main
from vidxp.mcp_cli import stdio_client_config
from vidxp.ports import LocalFileResource
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService, UploadSessionLink


MEDIA_ID = "123456781234423481234567890abcde"
JOB_ID = "223456781234423481234567890abcde"
ARTIFACT_ID = "323456781234423481234567890abcde"
UPLOAD_SESSION_ID = "423456781234423481234567890abcde"
MCP_TOOL_NAMES = [
    "get_workspace",
    "list_capabilities",
    "get_capability",
    "get_runtime_readiness",
    "list_media",
    "get_media",
    "create_media_upload",
    "get_media_upload",
    "get_index_status",
    "start_indexing",
    "prepare_models",
    "search_moments",
    "query_video",
    "create_clip",
    "create_evidence_clip",
    "materialize_job_evidence",
    "create_evidence_board",
    "get_artifact_download",
    "list_jobs",
    "get_job",
    "get_job_evidence",
    "get_job_status",
    "wait_job",
    "retry_job",
    "cancel_job",
]
STDIO_MCP_TOOL_NAMES = [
    *MCP_TOOL_NAMES[:6],
    "ingest_local_media",
    "get_media_ingestion",
    *MCP_TOOL_NAMES[8:],
]


def queued_job() -> Job:
    return Job(
        job_id=JOB_ID,
        kind=JobKind.index,
        state=JobState.queued,
        queue=JobQueue.cpu,
    )


def search_evidence_job(frame: Artifact, clip: Artifact | None = None) -> Job:
    evidence_id = "e" * 64
    hit = SearchHit(
        rank=1,
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        generation_id="523456781234423481234567890abcde",
        start=1.0,
        end=2.0,
        score=0.9,
        raw_distance=0.1,
        modality="scene",
        source_id="scene:frame:7",
        metadata={"frame_index": 7, "timestamp": 1.4, "fps": 5.0},
    )
    resolved = EvidenceRangeResolution(
        source_start_seconds=1.0,
        source_end_seconds=2.0,
        representative_timestamp_seconds=1.4,
        clip_start_seconds=0.0,
        clip_end_seconds=4.0,
        requested_padding_before_seconds=2.0,
        requested_padding_after_seconds=2.0,
        applied_padding_before_seconds=1.0,
        applied_padding_after_seconds=2.0,
        start_clamped=True,
    )
    delivery = EvidenceDeliveryResult(
        policy=EvidenceDeliveryPolicy(
            mode=(
                EvidenceDeliveryMode.keyframes_and_clips
                if clip is not None
                else EvidenceDeliveryMode.keyframes
            ),
            max_items=1,
        ),
        items=(
            EvidenceDeliveryItem(
                evidence_id=evidence_id,
                rank=1,
                media_id=MEDIA_ID,
                generation_id=hit.generation_id,
                modalities=("scene",),
                score=0.9,
                state=EvidenceDeliveryState.ready,
                range=resolved,
                keyframe=EvidenceKeyframe(
                    match=EvidenceFrameMatch.exact_indexed_frame,
                    timestamp_seconds=1.4,
                    frame_index=7,
                    width=1,
                    height=1,
                    artifact=EvidenceArtifact(
                        artifact=frame,
                    ),
                ),
                clip=(
                    EvidenceArtifact(
                        artifact=clip,
                    )
                    if clip is not None
                    else None
                ),
            ),
        ),
    )
    result = FusedSearchResult(
        query_id="fused:known",
        query="green frame",
        modalities=("scene",),
        moments=(
            FusedMoment(
                moment_id=evidence_id,
                rank=1,
                score=0.1,
                media_id=MEDIA_ID,
                start=1.0,
                end=2.0,
                modalities=("scene",),
                hits=(hit,),
            ),
        ),
        fusion=FusionProvenance(
            requested_modalities=("scene",),
            searched_modalities=("scene",),
        ),
        evidence_delivery=delivery,
    )
    return Job(
        job_id=JOB_ID,
        kind=JobKind.search,
        state=JobState.succeeded,
        queue=JobQueue.cpu,
        result=SearchJobResult(result=result),
    )


def query_evidence_job(frame: Artifact, clip: Artifact) -> Job:
    search_job = search_evidence_job(frame, clip)
    search = search_job.result.result
    item = search.evidence_delivery.items[0]
    hit = search.moments[0].hits[0]
    evidence = MomentEvidence(
        evidence_id=item.evidence_id,
        snapshot_id="623456781234423481234567890abcde",
        media_id=hit.media_id,
        generation_id=hit.generation_id,
        modality=hit.modality,
        source_id=hit.source_id,
        start=hit.start,
        end=hit.end,
        hit=hit,
    )
    answer = QueryAnswer(
        question="Which frame is green?",
        mode=QueryAnswerMode.generated,
        plan=QueryPlan(
            steps=(SearchMomentsPlanStep(modality="scene", query="green frame"),)
        ),
        claims=(
            GroundedClaim(
                text="The indexed evidence contains the green frame.",
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
        evidence=(evidence,),
        moments=search.moments,
        fusion=search.fusion,
        evidence_delivery=search.evidence_delivery,
    )
    return Job(
        job_id=JOB_ID,
        kind=JobKind.query,
        state=JobState.succeeded,
        queue=JobQueue.cpu,
        result=QueryJobResult(result=answer),
    )


def upload_session_status(
    *,
    state: UploadSessionState = UploadSessionState.open,
    items: tuple[MediaUploadStatus, ...] = (),
) -> MediaUploadSessionStatus:
    now = datetime.now(timezone.utc)
    total_bytes = sum(item.byte_size for item in items)
    return MediaUploadSessionStatus(
        session_id=UPLOAD_SESSION_ID,
        session_state=state,
        aggregate_state="empty" if not items else "uploading",
        expires_at=now + timedelta(hours=24),
        maximum_files=10,
        maximum_file_bytes=50 * 1024 * 1024 * 1024,
        maximum_aggregate_bytes=100 * 1024 * 1024 * 1024,
        file_count=len(items),
        total_bytes=total_bytes,
        reserved_file_count=len(items),
        reserved_bytes=total_bytes,
        uploaded_file_count=0,
        uploaded_bytes=0,
        ready_file_count=0,
        failed_file_count=0,
        items=items,
        status="No files selected yet." if not items else "Uploads are in progress.",
        next_action="Open the upload session and select one or more videos.",
    )


class MCPTests(unittest.IsolatedAsyncioTestCase):
    def context(
        self,
        root: Path,
        *,
        static_token: str | None = None,
        http_trusted_hosts: tuple[str, ...] = (
            "127.0.0.1",
            "testserver",
        ),
        mcp_allowed_hosts: tuple[str, ...] = ("127.0.0.1:*",),
        mcp_allowed_origins: tuple[str, ...] = (),
        artifact_download_public_url: str | None = None,
        artifact_download_secret: str | None = None,
        mcp_stdio_filesystem_accessible: bool = True,
        mcp_max_resource_bytes: int = 16 * 1024 * 1024,
    ) -> HttpApplicationContext:
        settings = VidXPSettings(
            repository_root=root,
            runtime_backend="cpu",
            http_auth_mode="static" if static_token is not None else "none",
            http_static_bearer_token=static_token,
            http_trusted_hosts=http_trusted_hosts,
            mcp_allowed_hosts=mcp_allowed_hosts,
            mcp_allowed_origins=mcp_allowed_origins,
            artifact_download_public_url=artifact_download_public_url,
            artifact_download_secret=artifact_download_secret,
            mcp_stdio_filesystem_accessible=mcp_stdio_filesystem_accessible,
            mcp_max_resource_bytes=mcp_max_resource_bytes,
        )

        application = Mock(spec=ControlPlaneApplication)
        application.list_capabilities.return_value = ()
        application.index_status.return_value = IndexStatus(
            schema_version=2,
            state="missing",
            stage="status",
            message="No index.",
        )
        application.workspace.return_value = WorkspaceOverview(
            media_total=0,
            index=application.index_status.return_value,
            next_actions=("register_media",),
        )
        jobs = Mock(spec=JobService)
        readiness = Mock()
        readiness.ready.return_value = True
        return HttpApplicationContext(
            application=application,
            jobs=jobs,
            readiness=readiness,
            authenticator=create_authenticator(settings),
            authorization=AuthorizationPolicy(),
            settings=settings,
        )

    def test_job_contract_declares_terminal_state_and_poll_cadence(self):
        active = queued_job()
        terminal = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.cancelled,
            queue=JobQueue.cpu,
        )

        self.assertFalse(active.terminal)
        self.assertEqual(active.poll_after_seconds, 1)
        self.assertTrue(terminal.terminal)
        self.assertEqual(terminal.poll_after_seconds, 0)

    def upload_context(
        self,
        root: Path,
        *,
        static_token: str | None = None,
        oidc_mcp_url: str | None = None,
    ) -> tuple[HttpApplicationContext, Mock]:
        context = self.context(root)
        oidc = oidc_mcp_url is not None
        settings = VidXPSettings(
            repository_root=root,
            runtime_backend="cpu",
            http_auth_mode=(
                "oidc" if oidc else "static" if static_token is not None else "none"
            ),
            http_static_bearer_token=static_token,
            http_oidc_issuer="https://identity.example" if oidc else None,
            http_oidc_audience="vidxp-api" if oidc else None,
            http_oidc_jwks_url=("https://identity.example/jwks" if oidc else None),
            http_required_scopes=("vidxp.write",) if oidc else (),
            mcp_public_url=oidc_mcp_url,
            http_trusted_hosts=("127.0.0.1", "testserver"),
            mcp_allowed_hosts=("127.0.0.1:*",),
            upload_public_endpoint="https://uploads.example/uploads/",
            upload_internal_endpoint="http://tusd:8080/uploads/",
            upload_cleanup_token="c" * 32,
            upload_handoff_public_url=("https://vidxp.example/upload-handoff"),
            upload_handoff_secret="h" * 32,
            upload_cors_origin_regex=r"^(https://vidxp\.example)$",
        )
        uploads = Mock(spec=RemoteUploadService)
        return (
            replace(
                context,
                settings=settings,
                authenticator=create_authenticator(settings),
                uploads=uploads,
            ),
            uploads,
        )

    async def test_curated_tools_publish_their_intended_output_contracts(self):
        with TemporaryDirectory() as directory:
            context, _uploads = self.upload_context(Path(directory))
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="local",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                discovered = await client.list_tools()
                result = await client.call_tool("list_capabilities", {})
                app_resource = await client.read_resource(MCP_APP_RESOURCE_URI)

        self.assertEqual(
            [tool.name for tool in discovered.tools],
            MCP_TOOL_NAMES,
        )
        tools = {tool.name: tool for tool in discovered.tools}
        for name in ("create_media_upload", "get_job_evidence"):
            self.assertEqual(
                tools[name].meta["ui"]["resourceUri"],
                MCP_APP_RESOURCE_URI,
            )
            self.assertEqual(
                tools[name].meta["openai/outputTemplate"],
                MCP_APP_RESOURCE_URI,
            )
        app_contents = app_resource.contents[0]
        self.assertEqual(app_contents.mime_type, MCP_APP_MIME_TYPE)
        self.assertEqual(
            app_contents.meta["ui"]["csp"],
            {"connectDomains": [], "resourceDomains": []},
        )
        self.assertIn("ui/notifications/tool-result", app_contents.text)
        self.assertIn('request("ui/initialize"', app_contents.text)
        self.assertIn('notify("ui/notifications/initialized"', app_contents.text)
        self.assertIn('request("tools/call"', app_contents.text)
        self.assertIn('request("ui/open-link"', app_contents.text)
        self.assertIn('request("ui/request-display-mode"', app_contents.text)
        self.assertIn('request("ui/update-model-context"', app_contents.text)
        self.assertIn("window.openai?.requestDisplayMode", app_contents.text)
        self.assertIn("materialize_job_evidence", app_contents.text)
        self.assertNotIn("<script src=", app_contents.text)
        self.assertIsNone(tools["get_job_evidence"].output_schema)
        self.assertIsNone(tools["materialize_job_evidence"].output_schema)
        self.assertTrue(
            all(
                tool.output_schema is not None
                for tool in discovered.tools
                if tool.name not in {"get_job_evidence", "materialize_job_evidence"}
            )
        )
        self.assertTrue(all(tool.title for tool in discovered.tools))
        self.assertEqual(
            tools["wait_job"].input_schema["properties"]["timeout_seconds"]["default"],
            30,
        )
        for name in ("get_job_status", "wait_job"):
            self.assertNotIn(
                '"poll_after_seconds"',
                json.dumps(tools[name].output_schema),
            )
        self.assertFalse(tools["create_media_upload"].annotations.read_only_hint)
        self.assertTrue(tools["create_media_upload"].annotations.idempotent_hint)
        self.assertTrue(tools["get_media_upload"].annotations.read_only_hint)
        upload_properties = tools["create_media_upload"].input_schema["properties"]
        self.assertEqual(
            set(upload_properties),
            {"idempotency_key", "index_after_import", "modalities"},
        )
        self.assertEqual(
            upload_properties["index_after_import"]["default"],
            True,
        )
        serialized_upload_schema = json.dumps(
            tools["create_media_upload"].input_schema
        ).lower()
        for forbidden in (
            '"base64"',
            '"blob"',
            '"chunk"',
            '"chunks"',
            '"content"',
            '"data"',
            '"original_filename"',
            '"byte_size"',
            '"declared_mime_type"',
        ):
            self.assertNotIn(forbidden, serialized_upload_schema)
        artifact_output = tools["get_artifact_download"].output_schema
        self.assertEqual(
            set(artifact_output["properties"]),
            {
                "artifact_id",
                "filename",
                "mime_type",
                "byte_size",
                "sha256",
                "etag",
                "state",
                "resource_uri",
                "delivery_mode",
                "local_path",
                "file_uri",
                "download_url",
                "download_expires_at",
                "delivery_error",
            },
        )
        serialized_artifact_output = json.dumps(artifact_output).lower()
        for forbidden in ('"base64"', '"blob"', '"bytes"', '"content"'):
            self.assertNotIn(forbidden, serialized_artifact_output)
        for name in ("search_moments", "query_video"):
            schema = tools[name].input_schema
            command_ref = schema["properties"]["command"]["$ref"]
            command_name = command_ref.rsplit("/", 1)[-1]
            media_description = schema["$defs"][command_name]["properties"]["media_id"][
                "description"
            ]
            self.assertIn("omit it", media_description)
            self.assertIn("active index snapshot", media_description)
        self.assertEqual(result.structured_content, {"items": []})
        self.assertFalse(result.is_error)

    async def test_media_upload_tools_return_idempotent_session_link(self):
        with TemporaryDirectory() as directory:
            context, uploads = self.upload_context(Path(directory))
            status = upload_session_status()
            uploads.create_upload_session.return_value = UploadSessionLink(
                status=status,
                capability="v1.selector.signature",
            )
            uploads.get_status.return_value = status
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"*"}),
                ),
            )
            arguments = {"idempotency_key": "agent-upload-0001"}
            async with Client(server) as client:
                first = await client.call_tool(
                    "create_media_upload",
                    arguments,
                )
                second = await client.call_tool(
                    "create_media_upload",
                    arguments,
                )
                current = await client.call_tool(
                    "get_media_upload",
                    {"upload_session_id": UPLOAD_SESSION_ID},
                )

            elicited = []

            async def record_url(_context, params):
                elicited.append(params)
                raise AssertionError("URL elicitation must not be used")

            async with Client(
                server,
                elicitation_callback=record_url,
                mode="legacy",
            ) as client:
                disabled = await client.call_tool(
                    "create_media_upload",
                    arguments,
                )

        self.assertEqual(first.structured_content, second.structured_content)
        page_url = urlsplit(first.structured_content["upload_session_url"])
        self.assertEqual(page_url.scheme, "https")
        self.assertEqual(page_url.query, "")
        self.assertEqual(
            parse_qs(page_url.fragment)["capability"],
            ["v1.selector.signature"],
        )
        self.assertEqual(current.structured_content["aggregate_state"], "empty")
        self.assertEqual(disabled.structured_content, first.structured_content)
        self.assertEqual(elicited, [])
        self.assertIn(
            first.structured_content["upload_session_url"],
            first.content[0].text,
        )
        request_keys = [
            call.kwargs["request_key"]
            for call in uploads.create_upload_session.call_args_list
        ]
        self.assertEqual(len(request_keys), 3)
        self.assertEqual(len(set(request_keys)), 1)
        self.assertRegex(request_keys[0], r"^[0-9a-f]{64}$")

    async def test_media_upload_rejects_unavailable_index_capabilities(self):
        with TemporaryDirectory() as directory:
            context, uploads = self.upload_context(Path(directory))
            context.application.capabilities = SimpleNamespace(
                registry=create_capability_registry()
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "create_media_upload",
                    {
                        "idempotency_key": "invalid-index-capability-0001",
                        "modalities": ["not-installed"],
                    },
                )

        self.assertTrue(result.is_error)
        self.assertIn(
            '"code":"ingestion_capabilities_unavailable"',
            result.content[0].text,
        )
        self.assertIn("get_workspace", result.content[0].text)
        uploads.create_upload_session.assert_not_called()

    async def test_index_failed_upload_media_can_use_normal_index_workflow(self):
        with TemporaryDirectory() as directory:
            context, uploads = self.upload_context(Path(directory))
            now = datetime.now(timezone.utc)
            item = MediaUploadStatus(
                intent_id="523456781234423481234567890abcde",
                client_file_key="index-failed-file",
                state=UploadState.ready,
                original_filename="registered.mp4",
                byte_size=20,
                declared_mime_type="video/mp4",
                expires_at=now + timedelta(hours=1),
                phase="index_failed",
                job_id="623456781234423481234567890abcde",
                import_job_id="623456781234423481234567890abcde",
                index_job_id="723456781234423481234567890abcde",
                media_id=MEDIA_ID,
                error=ErrorDetail(
                    code="model_unavailable",
                    category=ErrorCategory.unavailable,
                    message="Prepare the configured scene model.",
                ),
                terminal=True,
                poll_after_seconds=0,
                status="The video is registered but automatic indexing failed.",
                next_action="Fix the error and use start_indexing with media_id.",
            )
            uploads.get_status.return_value = upload_session_status(
                items=(item,)
            ).model_copy(
                update={
                    "aggregate_state": "index_failed",
                    "ready_file_count": 1,
                    "index_failed_file_count": 1,
                    "terminal": True,
                    "poll_after_seconds": 0,
                }
            )
            uploads.start_indexing.return_value = queued_job()
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                status = await client.call_tool(
                    "get_media_upload",
                    {"upload_session_id": UPLOAD_SESSION_ID},
                )
                preserved_media_id = status.structured_content["items"][0]["media_id"]
                retried = await client.call_tool(
                    "start_indexing",
                    {
                        "command": {
                            "media_id": preserved_media_id,
                            "modalities": ["scene"],
                        },
                        "idempotency_key": "retry-index-failed-media-0001",
                    },
                )

        self.assertFalse(retried.is_error)
        command = uploads.start_indexing.call_args.args[0]
        self.assertEqual(command.media_id, MEDIA_ID)
        context.jobs.submit_index.assert_not_called()

    async def test_oidc_upload_returns_capability_without_url_elicitation(self):
        seen = []

        async def decline_url(_context, params):
            seen.append(params)
            raise AssertionError("URL elicitation must not be used")

        with TemporaryDirectory() as directory:
            context, uploads = self.upload_context(
                Path(directory),
                oidc_mcp_url="https://vidxp.example/mcp",
            )
            status = upload_session_status()
            uploads.create_upload_session.return_value = UploadSessionLink(
                status=status,
                capability="ordinary-result-capability",
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    client_id="mcp-client",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(
                server,
                elicitation_callback=decline_url,
                mode="legacy",
            ) as client:
                result = await client.call_tool(
                    "create_media_upload",
                    {"idempotency_key": "oidc-upload-0001"},
                )

        self.assertFalse(result.is_error)
        self.assertEqual(seen, [])
        self.assertIn(
            "#capability=ordinary-result-capability",
            result.structured_content["upload_session_url"],
        )

    async def test_stdio_upload_returns_plain_session_link(self):
        seen = []

        async def accept_url(_context, params):
            seen.append(params)
            raise AssertionError("URL elicitation must not be used")

        fixture = Path(__file__).parent / "fixtures" / "mcp_upload_server.py"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(fixture)],
            cwd=Path(__file__).parents[1],
        )
        arguments = {"idempotency_key": "stdio-upload-0001"}
        async with Client(
            stdio_client(parameters),
            elicitation_callback=accept_url,
            mode="legacy",
        ) as client:
            result = await client.call_tool("create_media_upload", arguments)

        self.assertFalse(result.is_error)
        self.assertEqual(seen, [])
        self.assertIn(
            "#capability=fixture-capability",
            result.structured_content["upload_session_url"],
        )

    async def test_real_stdio_artifact_delivery_returns_local_file_and_resource(self):
        with TemporaryDirectory() as directory:
            artifact_path = Path(directory) / "rendered clip.mp4"
            content = b"real-stdio-clip-content"
            artifact_path.write_bytes(content)
            fixture = Path(__file__).parent / "fixtures" / "mcp_upload_server.py"
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(fixture)],
                cwd=Path(__file__).parents[1],
                env={
                    "VIDXP_TEST_ARTIFACT_PATH": str(artifact_path),
                    "VIDXP_TEST_FORBID_HELPERS": "1",
                },
            )
            async with Client(stdio_client(parameters)) as client:
                result = await client.call_tool(
                    "get_artifact_download",
                    {"artifact_id": ARTIFACT_ID},
                )
                link = result.content[0]
                downloaded = await client.read_resource(str(link.uri))
                local_bytes = Path(result.structured_content["local_path"]).read_bytes()

        self.assertFalse(result.is_error)
        self.assertIsInstance(link, ResourceLink)
        self.assertEqual(result.structured_content["delivery_mode"], "local_file")
        self.assertEqual(
            local_bytes,
            content,
        )
        self.assertIn("rendered%20clip.mp4", result.structured_content["file_uri"])
        self.assertIsNone(result.structured_content["download_url"])
        self.assertEqual(
            downloaded.contents[0].blob, base64.b64encode(content).decode()
        )

    def test_remote_mcp_is_stateless(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            remote = create_remote_mcp(context)

        self.assertTrue(remote.server.session_manager.stateless)

    async def test_streamable_http_returns_plain_session_link(self):
        token = "s" * 32
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        seen = []

        async def accept_url(_context, params):
            seen.append(params)
            raise AssertionError("URL elicitation must not be used")

        with TemporaryDirectory() as directory:
            context, uploads = self.upload_context(
                Path(directory),
                oidc_mcp_url=f"http://127.0.0.1:{port}/mcp",
            )
            status = upload_session_status()
            uploads.create_upload_session.return_value = UploadSessionLink(
                status=status,
                capability="http-capability",
            )
            verified = AuthenticatedBearer(
                principal=Principal(
                    subject="oidc-user",
                    client_id="mcp-client",
                    scopes=frozenset({"vidxp.write"}),
                ),
                expires_at=None,
                resource=f"http://127.0.0.1:{port}/mcp",
                claims={"sub": "oidc-user", "client_id": "mcp-client"},
            )
            authentication = patch.object(
                OIDCBearerAuthenticator,
                "authenticate_bearer",
                return_value=verified,
            )
            authentication.start()
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(context=context),
                    host="127.0.0.1",
                    port=port,
                    log_level="critical",
                )
            )
            serving = asyncio.create_task(server.serve())
            try:
                for _attempt in range(200):
                    if server.started:
                        break
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.01)
                else:
                    self.fail("The MCP HTTP fixture did not start.")
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {token}"}
                ) as http_client:
                    transport = streamable_http_client(
                        f"http://127.0.0.1:{port}/mcp",
                        http_client=http_client,
                    )
                    async with Client(
                        transport,
                        elicitation_callback=accept_url,
                        mode="legacy",
                    ) as client:
                        result = await client.call_tool(
                            "create_media_upload",
                            {"idempotency_key": "http-upload-0001"},
                        )
            finally:
                server.should_exit = True
                await serving
                authentication.stop()

        self.assertFalse(result.is_error)
        self.assertEqual(seen, [])
        self.assertIn(
            "#capability=http-capability",
            result.structured_content["upload_session_url"],
        )

    async def _real_streamable_http_artifact_delivery(
        self,
        *,
        configure_download: bool,
    ):
        token = "s" * 32
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "remote-clip.mkv"
            content = b"real-http-matroska-content"
            clip.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            download_settings = (
                {
                    "artifact_download_public_url": (
                        "https://public.example/artifact-download"
                    ),
                    "artifact_download_secret": "d" * 32,
                }
                if configure_download
                else {}
            )
            context = self.context(root, static_token=token, **download_settings)
            context.application.get_artifact.return_value = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                kind=ArtifactKind.snippet,
                profile="source_mkv",
                mime_type="video/x-matroska",
                byte_size=len(content),
                sha256=digest,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            context.application.open_artifact_content.return_value = LocalFileResource(
                path=clip,
                filename=f"snippet-{ARTIFACT_ID}.mkv",
                mime_type="video/x-matroska",
                byte_size=len(content),
                etag=digest,
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(context=context),
                    host="127.0.0.1",
                    port=port,
                    log_level="critical",
                )
            )
            serving = asyncio.create_task(server.serve())
            try:
                for _attempt in range(200):
                    if server.started:
                        break
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.01)
                else:
                    self.fail("The MCP HTTP fixture did not start.")
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {token}"}
                ) as http_client:
                    transport = streamable_http_client(
                        f"http://127.0.0.1:{port}/mcp",
                        http_client=http_client,
                    )
                    async with Client(transport) as client:
                        result = await client.call_tool(
                            "get_artifact_download",
                            {"artifact_id": ARTIFACT_ID},
                        )
                        link = result.content[0]
                        downloaded = await client.read_resource(str(link.uri))
            finally:
                server.should_exit = True
                await serving

        return result, link, downloaded, content

    async def test_real_streamable_http_artifact_delivery_hides_local_path(self):
        (
            result,
            link,
            downloaded,
            content,
        ) = await self._real_streamable_http_artifact_delivery(configure_download=True)

        self.assertFalse(result.is_error)
        self.assertIsInstance(link, ResourceLink)
        self.assertEqual(result.structured_content["delivery_mode"], "https_download")
        self.assertIsNone(result.structured_content["local_path"])
        self.assertIsNone(result.structured_content["file_uri"])
        public = urlsplit(result.structured_content["download_url"])
        self.assertEqual(public.scheme, "https")
        self.assertEqual(public.netloc, "public.example")
        self.assertEqual(public.query, "")
        self.assertTrue(public.fragment.startswith("capability="))
        self.assertEqual(
            downloaded.contents[0].blob, base64.b64encode(content).decode()
        )

    async def test_real_streamable_http_preserves_resource_without_public_download(
        self,
    ):
        (
            result,
            link,
            downloaded,
            content,
        ) = await self._real_streamable_http_artifact_delivery(configure_download=False)

        self.assertFalse(result.is_error)
        self.assertIsInstance(link, ResourceLink)
        self.assertEqual(result.structured_content["delivery_mode"], "mcp_resource")
        self.assertIsNone(result.structured_content["local_path"])
        self.assertIsNone(result.structured_content["file_uri"])
        self.assertIsNone(result.structured_content["download_url"])
        self.assertIsNone(result.structured_content["download_expires_at"])
        self.assertEqual(
            result.structured_content["delivery_error"]["code"],
            "public_download_origin_unavailable",
        )
        self.assertEqual(
            downloaded.contents[0].blob,
            base64.b64encode(content).decode(),
        )

    async def test_media_upload_tools_enforce_permissions_and_availability(self):
        with TemporaryDirectory() as directory:
            context, _uploads = self.upload_context(Path(directory))
            arguments = {"idempotency_key": "agent-upload-0001"}
            write_server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="reader",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(write_server) as client:
                denied = await client.call_tool(
                    "create_media_upload",
                    arguments,
                )
            unavailable_context = self.context(Path(directory))
            unavailable_server = create_mcp_server(
                unavailable_context,
                default_principal=Principal(
                    subject="writer",
                    scopes=frozenset({"vidxp.write"}),
                ),
            )
            async with Client(unavailable_server) as client:
                unavailable_tools = {
                    tool.name for tool in (await client.list_tools()).tools
                }

        self.assertIn('"required_scope":"vidxp.write"', denied.content[0].text)
        self.assertNotIn("create_media_upload", unavailable_tools)
        self.assertNotIn("get_media_upload", unavailable_tools)

    async def test_workspace_tool_projects_actionable_repository_state(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="local",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool("get_workspace", {})

        self.assertEqual(result.structured_content["media_total"], 0)
        self.assertEqual(
            result.structured_content["next_actions"],
            ["register_media"],
        )
        context.application.workspace.assert_called_once()

    def test_stdio_help_and_config_are_ready_to_copy(self):
        config = stdio_client_config(
            command=r"C:\VidXP\vidxp-mcp.exe",
            repository="library",
            environment={},
        )
        self.assertEqual(
            config,
            {
                "mcpServers": {
                    "vidxp": {
                        "command": r"C:\VidXP\vidxp-mcp.exe",
                        "args": ["--repository", "library"],
                    }
                }
            },
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                mcp_main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("CLAUDE DESKTOP / COMPATIBLE STDIO CONFIG", output.getvalue())
        self.assertIn('"mcpServers"', output.getvalue())
        self.assertIn("codex mcp add vidxp", output.getvalue())
        self.assertIn("ChatGPT web", output.getvalue())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            mcp_main(["--print-config", "--repository", "library"])
        rendered = json.loads(output.getvalue())
        self.assertEqual(
            rendered["mcpServers"]["vidxp"]["args"],
            ["--repository", "library"],
        )

    def test_stdio_config_carries_only_local_query_runtime_environment(self):
        config = stdio_client_config(
            command="vidxp-mcp",
            environment={
                "VIDXP_SLM_BASE_URL": "http://127.0.0.1:11434/v1",
                "VIDXP_SLM_MODEL": "qwen3.5:4b-q4_K_M",
                "VIDXP_HTTP_STATIC_BEARER_TOKEN": "must-not-leak",
            },
        )

        self.assertEqual(
            config["mcpServers"]["vidxp"]["env"],
            {
                "VIDXP_SLM_BASE_URL": "http://127.0.0.1:11434/v1",
                "VIDXP_SLM_MODEL": "qwen3.5:4b-q4_K_M",
            },
        )

    def test_stdio_check_performs_handshake_and_tool_probe(self):
        output = io.StringIO()
        with TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(output):
                mcp_main(
                    [
                        "--check",
                        "--data-dir",
                        directory,
                        "--device",
                        "cpu",
                    ]
                )

        rendered = output.getvalue()
        self.assertIn("OK VidXP MCP", rendered)
        self.assertIn("Index state: missing", rendered)
        self.assertIn("Tools: 25", rendered)
        self.assertIn("get_index_status", rendered)

    async def test_server_info_exposes_vidxp_branding(self):
        with TemporaryDirectory() as directory:
            server = create_mcp_server(
                self.context(Path(directory)),
                default_principal=Principal(
                    subject="local",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                server_info = client.server_info

        self.assertIsNotNone(server_info)
        self.assertEqual(server_info.title, "VidXP")
        self.assertEqual(server_info.website_url, PROJECT_URL)
        self.assertEqual(len(server_info.icons or ()), 1)
        icon = server_info.icons[0]
        self.assertEqual(icon.mime_type, ICON_MIME_TYPE)
        self.assertEqual(icon.sizes, [ICON_SIZE])
        prefix = f"data:{ICON_MIME_TYPE};base64,"
        self.assertTrue(icon.src.startswith(prefix))
        self.assertEqual(
            base64.b64decode(icon.src.removeprefix(prefix)),
            icon_bytes(),
        )

    async def test_index_submission_uses_shared_stable_idempotency(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_index.return_value = queued_job()
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.write"}),
                ),
            )
            arguments = {
                "command": {
                    "media_id": MEDIA_ID,
                    "modalities": ["scene"],
                    "scene_sample_fps": 2.0,
                },
                "idempotency_key": "agent-request-0001",
            }
            async with Client(server) as client:
                first = await client.call_tool("start_indexing", arguments)
                second = await client.call_tool("start_indexing", arguments)

        self.assertEqual(
            first.structured_content,
            second.structured_content,
        )
        calls = context.jobs.submit_index.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0].scene_sample_fps, 2.0)
        self.assertEqual(
            calls[0].kwargs["job_id"],
            calls[1].kwargs["job_id"],
        )
        self.assertEqual(calls[0].args[0].modalities, ("scene",))

    async def test_index_retry_routes_through_upload_relink_boundary(self):
        with TemporaryDirectory() as directory:
            context, uploads = self.upload_context(Path(directory))
            uploads.start_indexing.return_value = queued_job()
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.write"}),
                ),
            )
            arguments = {
                "command": {
                    "media_id": MEDIA_ID,
                    "modalities": ["scene"],
                },
                "idempotency_key": "agent-index-retry-0001",
            }
            async with Client(server) as client:
                result = await client.call_tool("start_indexing", arguments)

        self.assertFalse(result.is_error)
        uploads.start_indexing.assert_called_once()
        context.jobs.submit_index.assert_not_called()

    async def test_missing_models_fail_before_index_submission(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_index.side_effect = ApplicationError(
                "model_unavailable",
                ErrorCategory.unavailable,
                "Run vidxp prepare --modalities scene.",
                details={
                    "capability": "scene",
                    "remediation": "vidxp prepare --modalities scene",
                },
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.write"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "start_indexing",
                    {
                        "command": {
                            "media_id": MEDIA_ID,
                            "modalities": ["scene"],
                        },
                        "idempotency_key": "agent-request-0002",
                    },
                )

        self.assertTrue(result.is_error)
        self.assertIn(
            '"remediation":"vidxp prepare --modalities scene"',
            result.content[0].text,
        )
        context.jobs.submit_index.assert_called_once()

    async def test_query_video_submits_the_shared_durable_command(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_query.return_value = queued_job().model_copy(
                update={"kind": JobKind.query}
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "query_video",
                    {
                        "command": {
                            "question": "What happens after the taxi arrives?",
                            "media_id": MEDIA_ID,
                            "modalities": ["scene", "speech"],
                        },
                        "idempotency_key": "agent-query-0001",
                    },
                )

        self.assertFalse(result.is_error)
        command = context.jobs.submit_query.call_args.args[0]
        self.assertEqual(
            command,
            QueryVideoCommand(
                question="What happens after the taxi arrives?",
                media_id=MEDIA_ID,
                modalities=("scene", "speech"),
                evidence_delivery=InitialEvidenceDeliveryPolicy(
                    mode=EvidenceDeliveryMode.none
                ),
            ),
        )
        self.assertIn(
            "job_id",
            context.jobs.submit_query.call_args.kwargs,
        )

    async def test_discovery_tools_use_shared_media_and_job_pages(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.list_media.return_value = MediaPage(total=0)
            context.jobs.list.return_value = JobPage()
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                media = await client.call_tool(
                    "list_media",
                    {"page_size": 7},
                )
                jobs = await client.call_tool(
                    "list_jobs",
                    {"page_size": 9},
                )

        self.assertFalse(media.is_error)
        self.assertFalse(jobs.is_error)
        self.assertEqual(
            media.structured_content,
            {"items": [], "total": 0, "next_cursor": None},
        )
        self.assertEqual(
            jobs.structured_content,
            {"items": [], "next_cursor": None},
        )
        self.assertEqual(
            context.application.list_media.call_args.args[0].page_size,
            7,
        )
        self.assertEqual(context.jobs.list.call_args.args[0].page_size, 9)

    async def test_list_media_passes_filters_to_application(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.list_media.return_value = MediaPage(total=0)
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                media = await client.call_tool(
                    "list_media",
                    {
                        "page_size": 5,
                        "filename": "clip.mp4",
                        "state": "ready",
                    },
                )

        self.assertFalse(media.is_error)
        command = context.application.list_media.call_args.args[0]
        self.assertEqual(command.page_size, 5)
        self.assertEqual(command.filename, "clip.mp4")
        self.assertEqual(command.state, MediaState.ready)

    async def test_failed_model_preparation_job_is_structured_over_mcp(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.get.return_value = Job(
                job_id=JOB_ID,
                kind=JobKind.prepare_models,
                state=JobState.failed,
                queue=JobQueue.cpu,
                error=ErrorDetail(
                    code="model_download_failed",
                    category=ErrorCategory.unavailable,
                    message="The model download failed after three attempts.",
                    details={
                        "model": "publisher/model",
                        "partial_files_preserved": True,
                        "remediation": "vidxp prepare --modalities speech",
                    },
                    retryable=True,
                ),
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool("get_job", {"job_id": JOB_ID})

        self.assertFalse(result.is_error)
        error = result.structured_content["error"]
        self.assertEqual(error["code"], "model_download_failed")
        self.assertTrue(error["retryable"])
        self.assertTrue(error["details"]["partial_files_preserved"])

    async def test_get_job_returns_machine_record_without_loading_evidence_bytes(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            frame = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                job_id=JOB_ID,
                kind=ArtifactKind.evidence_frame,
                profile="png",
                mime_type="image/png",
                byte_size=1,
                sha256="a" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            context.jobs.get.return_value = search_evidence_job(frame)
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool("get_job", {"job_id": JOB_ID})

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["job_id"], JOB_ID)
        self.assertFalse(
            any(
                isinstance(block, (ImageContent, ResourceLink))
                for block in result.content
            )
        )
        context.application.open_artifact_content.assert_not_called()

    async def test_compact_job_status_and_wait_avoid_full_result_projection(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            compact = JobService._summary(queued_job())
            context.jobs.summary.return_value = compact
            context.jobs.wait_for_change.return_value = JobWaitResult(
                job=compact,
                changed=False,
                timed_out=True,
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                summary = await client.call_tool(
                    "get_job_status",
                    {"job_id": JOB_ID},
                )
                waited = await client.call_tool(
                    "wait_job",
                    {
                        "job_id": JOB_ID,
                        "after_observation_token": compact.observation_token,
                        "timeout_seconds": 5,
                    },
                )

        self.assertFalse(summary.is_error)
        self.assertNotIn("result", summary.structured_content)
        self.assertNotIn("poll_after_seconds", summary.structured_content)
        self.assertEqual(
            summary.structured_content["observation_token"],
            compact.observation_token,
        )
        self.assertFalse(waited.is_error)
        self.assertTrue(waited.structured_content["timed_out"])
        self.assertNotIn(
            "poll_after_seconds",
            waited.structured_content["job"],
        )
        context.jobs.get.assert_not_called()

    async def test_completed_search_projects_inline_frame_and_readable_resource(self):
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNg"
            "YAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frame_path = root / "frame.png"
            frame_path.write_bytes(png_bytes)
            frame = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                job_id=JOB_ID,
                kind=ArtifactKind.evidence_frame,
                profile="png",
                mime_type="image/png",
                byte_size=len(png_bytes),
                sha256=hashlib.sha256(png_bytes).hexdigest(),
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            cases = (
                ("local_stdio", False),
                ("streamable_http", False),
                ("streamable_http", True),
            )
            for transport, configured in cases:
                with self.subTest(transport=transport, configured=configured):
                    context = self.context(
                        root,
                        artifact_download_public_url=(
                            "https://public.example/artifact-download"
                            if configured
                            else None
                        ),
                        artifact_download_secret="d" * 32 if configured else None,
                    )
                    durable = search_evidence_job(frame)
                    self.assertIsNone(
                        durable.result.result.evidence_delivery.items[
                            0
                        ].keyframe.artifact.resource_uri
                    )
                    context.jobs.get.return_value = durable
                    context.application.open_artifact_content.return_value = (
                        LocalFileResource(
                            path=frame_path,
                            filename=f"evidence_frame-{ARTIFACT_ID}.png",
                            mime_type="image/png",
                            byte_size=len(png_bytes),
                            etag=frame.sha256,
                        )
                    )
                    server = create_mcp_server(
                        context,
                        default_principal=Principal(
                            subject="agent",
                            scopes=frozenset({"vidxp.read"}),
                        ),
                        artifact_delivery=transport,
                    )
                    async with Client(server) as client:
                        result = await client.call_tool(
                            "get_job_evidence", {"job_id": JOB_ID}
                        )
                        uri = f"vidxp://artifacts/{ARTIFACT_ID}/content.png"
                        resource = await client.read_resource(uri)

                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content["view"], "evidence")
                    self.assertEqual(
                        result.structured_content["source_job_id"],
                        JOB_ID,
                    )
                    self.assertEqual(
                        result.structured_content["board"]["tiles"][0][
                            "evidence_id"
                        ],
                        "e" * 64,
                    )
                    self.assertIn(evidence_id := "e" * 64, result.content[0].text)
                    self.assertIn("1.000-2.000", result.content[0].text)
                    if transport == "local_stdio":
                        self.assertIn(str(frame_path.resolve()), result.content[0].text)
                    elif configured:
                        self.assertIn(
                            "https://public.example/artifact-download",
                            result.content[0].text,
                        )
                    else:
                        self.assertIn(uri, result.content[0].text)
                    self.assertTrue(
                        any(isinstance(block, ImageContent) for block in result.content)
                    )
                    self.assertTrue(
                        any(isinstance(block, ResourceLink) for block in result.content)
                    )
                    self.assertEqual(len(resource.contents), 1)
                    self.assertEqual(
                        evidence_id, durable.result.result.moments[0].moment_id
                    )

    async def test_inline_evidence_respects_configured_resource_byte_limit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            maximum = 64
            for byte_size, expect_inline in ((32, True), (128, False)):
                with self.subTest(byte_size=byte_size):
                    content = b"p" * byte_size
                    frame_path = root / f"frame-{byte_size}.png"
                    frame_path.write_bytes(content)
                    frame = Artifact(
                        artifact_id=ARTIFACT_ID,
                        media_id=MEDIA_ID,
                        generation_id="523456781234423481234567890abcde",
                        job_id=JOB_ID,
                        kind=ArtifactKind.evidence_frame,
                        profile="png",
                        mime_type="image/png",
                        byte_size=byte_size,
                        sha256=hashlib.sha256(content).hexdigest(),
                        state=ArtifactState.ready,
                        created_at=datetime.now(timezone.utc),
                    )
                    context = self.context(
                        root,
                        mcp_max_resource_bytes=maximum,
                        artifact_download_public_url=(
                            "https://public.example/artifact-download"
                        ),
                        artifact_download_secret="d" * 32,
                    )
                    context.jobs.get.return_value = search_evidence_job(frame)
                    context.application.open_artifact_content.return_value = (
                        LocalFileResource(
                            path=frame_path,
                            filename=f"evidence_frame-{ARTIFACT_ID}.png",
                            mime_type="image/png",
                            byte_size=byte_size,
                            etag=frame.sha256,
                        )
                    )
                    server = create_mcp_server(
                        context,
                        default_principal=Principal(
                            subject="agent",
                            scopes=frozenset({"vidxp.read"}),
                        ),
                        artifact_delivery="streamable_http",
                    )
                    async with Client(server) as client:
                        result = await client.call_tool(
                            "get_job_evidence", {"job_id": JOB_ID}
                        )
                        self.assertFalse(result.is_error)
                        self.assertEqual(
                            result.structured_content["view"],
                            "evidence",
                        )
                        self.assertEqual(
                            any(
                                isinstance(block, ImageContent)
                                for block in result.content
                            ),
                            expect_inline,
                        )
                        self.assertEqual(
                            any(
                                isinstance(block, ResourceLink)
                                for block in result.content
                            ),
                            byte_size <= maximum,
                        )
                        if not expect_inline:
                            context.application.open_artifact_content.assert_not_called()

    async def test_create_evidence_clip_derives_range_from_source_job(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            frame = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                job_id=JOB_ID,
                kind=ArtifactKind.evidence_frame,
                profile="png",
                mime_type="image/png",
                byte_size=1,
                sha256="a" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            source = search_evidence_job(frame)
            context.jobs.get.return_value = source
            context.jobs.submit_snippet.return_value = queued_job().model_copy(
                update={"kind": JobKind.snippet}
            )
            evidence_delivery = Mock()
            context = replace(context, evidence_delivery=evidence_delivery)
            resolved = source.result.result.evidence_delivery.items[0].range
            evidence_delivery.resolve_job_evidence.return_value = (
                SimpleNamespace(media_id=MEDIA_ID),
                resolved,
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.write"}),
                ),
            )
            arguments = {
                "source_job_id": JOB_ID,
                "evidence_id": "e" * 64,
                "idempotency_key": "evidence-clip-0001",
                "padding_before_seconds": 2,
                "padding_after_seconds": 2,
            }
            async with Client(server) as client:
                first = await client.call_tool("create_evidence_clip", arguments)
                second = await client.call_tool("create_evidence_clip", arguments)

            self.assertFalse(first.is_error)
            self.assertFalse(second.is_error)
            command = context.jobs.submit_snippet.call_args.args[0]
            self.assertEqual(command.media_id, MEDIA_ID)
            self.assertEqual(command.start_seconds, resolved.clip_start_seconds)
            self.assertEqual(command.end_seconds, resolved.clip_end_seconds)
            calls = context.jobs.submit_snippet.call_args_list
            self.assertEqual(calls[0].kwargs["job_id"], calls[1].kwargs["job_id"])
            resolver = evidence_delivery.resolve_job_evidence
            resolver.assert_called_with(
                source.result.result,
                "e" * 64,
                padding_before=2.0,
                padding_after=2.0,
            )

    async def test_materialize_job_evidence_returns_selected_frames_and_clips(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frame_path = root / "frame.png"
            frame_path.write_bytes(b"x")
            context = self.context(root, mcp_stdio_filesystem_accessible=False)
            frame = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                job_id=JOB_ID,
                kind=ArtifactKind.evidence_frame,
                profile="png",
                mime_type="image/png",
                byte_size=1,
                sha256="a" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            clip = Artifact(
                artifact_id="423456781234423481234567890abcde",
                media_id=MEDIA_ID,
                job_id=JOB_ID,
                kind=ArtifactKind.snippet,
                profile="compatible_mp4",
                mime_type="video/mp4",
                byte_size=4,
                sha256="b" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            source = search_evidence_job(frame, clip)
            delivery = source.result.result.evidence_delivery
            evidence_delivery = Mock()
            evidence_delivery.deliver_selected.return_value = delivery
            context = replace(context, evidence_delivery=evidence_delivery)
            context.jobs.get.return_value = source
            context.application.open_artifact_content.return_value = LocalFileResource(
                path=frame_path,
                filename="frame.png",
                mime_type="image/png",
                byte_size=1,
                etag="a" * 64,
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read", "vidxp.write"}),
                ),
            )

            async with Client(server) as client:
                result = await client.call_tool(
                    "materialize_job_evidence",
                    {
                        "source_job_id": JOB_ID,
                        "evidence_ids": ["e" * 64],
                        "mode": "keyframes_and_clips",
                    },
                )

            self.assertFalse(result.is_error, result.content)
            self.assertTrue(
                any(isinstance(block, ImageContent) for block in result.content)
            )
            self.assertEqual(
                sum(isinstance(block, ResourceLink) for block in result.content),
                2,
            )
            self.assertIsNone(result.structured_content)
            self.assertIn("1.000-2.000", result.content[0].text)
            self.assertIn("e" * 64, result.content[0].text)
            self.assertIn(
                f"vidxp://artifacts/{ARTIFACT_ID}/content.png",
                result.content[0].text,
            )
            call = evidence_delivery.deliver_selected.call_args
            self.assertEqual(call.args[0], source.result.result)
            self.assertEqual(call.args[1], ("e" * 64,))
            self.assertEqual(
                call.args[2].mode,
                EvidenceDeliveryMode.keyframes_and_clips,
            )

    async def test_create_evidence_board_submits_frozen_candidates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            frame = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                job_id=JOB_ID,
                kind=ArtifactKind.evidence_frame,
                profile="png",
                mime_type="image/png",
                byte_size=1,
                sha256="a" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            source = search_evidence_job(frame)
            candidate = EvidenceBoardCandidate(
                evidence_id="e" * 64,
                rank=1,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                modalities=("scene",),
                start=1.0,
                end=2.0,
                representative_timestamp=1.4,
                frame_index=7,
                frame_match=EvidenceFrameMatch.exact_indexed_frame,
            )
            prepared = EvidenceBoardJobRequest(
                source_job_id=JOB_ID,
                source_fingerprint="f" * 64,
                candidates=(candidate,),
            )
            delivery = Mock()
            delivery.prepare_board_request.return_value = prepared
            context = replace(context, evidence_delivery=delivery)
            context.jobs.get.return_value = source
            context.jobs.submit_evidence_board.return_value = queued_job().model_copy(
                update={"kind": JobKind.evidence_board}
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )

            async with Client(server) as client:
                result = await client.call_tool(
                    "create_evidence_board",
                    {
                        "source_job_id": JOB_ID,
                        "idempotency_key": "board-0001",
                    },
                )

            self.assertFalse(result.is_error, result.content)
            delivery.prepare_board_request.assert_called_once()
            context.jobs.submit_evidence_board.assert_called_once_with(
                prepared,
                job_id=context.jobs.submit_evidence_board.call_args.kwargs["job_id"],
            )

    async def test_get_job_projects_default_evidence_board_with_search(self):
        try:
            from PIL import Image
        except ModuleNotFoundError:
            self.skipTest("Pillow is unavailable in this test profile")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            page_path = root / "board.jpg"
            Image.new("RGB", (320, 286), "purple").save(page_path, format="JPEG")
            page_bytes = page_path.read_bytes()
            page_artifact = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                job_id=JOB_ID,
                kind=ArtifactKind.evidence_board,
                profile="overview-jpeg-v1",
                mime_type="image/jpeg",
                byte_size=len(page_bytes),
                sha256=hashlib.sha256(page_bytes).hexdigest(),
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            tile = EvidenceBoardTile(
                tile_id="d" * 64,
                evidence_id="e" * 64,
                page_number=1,
                position=1,
                rank=1,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                modalities=("scene",),
                start=1.0,
                end=2.0,
                representative_timestamp=1.4,
                frame_match=EvidenceFrameMatch.representative,
                state=EvidenceDeliveryState.ready,
            )
            board = EvidenceBoardResult(
                source_job_id=JOB_ID,
                source_fingerprint="f" * 64,
                requested_count=1,
                rendered_count=1,
                failed_count=0,
                pages=(
                    EvidenceBoardPage(
                        page_number=1,
                        media_id=MEDIA_ID,
                        generation_id="523456781234423481234567890abcde",
                        artifact=EvidenceArtifact(artifact=page_artifact),
                        width=320,
                        height=286,
                        columns=1,
                        rows=1,
                        tile_ids=(tile.tile_id,),
                    ),
                ),
                tiles=(tile,),
            )
            context = self.context(root, mcp_stdio_filesystem_accessible=False)
            source = search_evidence_job(page_artifact)
            search = source.result.result
            context.jobs.get.return_value = source.model_copy(
                update={
                    "result": SearchJobResult(
                        result=search.model_copy(
                            update={
                                "evidence_delivery": EvidenceDeliveryResult(
                                    policy=InitialEvidenceDeliveryPolicy(
                                        mode=EvidenceDeliveryMode.none,
                                    ),
                                    items=(),
                                    board=board,
                                )
                            }
                        )
                    )
                }
            )
            context.application.open_artifact_content.return_value = LocalFileResource(
                path=page_path,
                filename=f"evidence_board-{ARTIFACT_ID}.jpg",
                mime_type="image/jpeg",
                byte_size=len(page_bytes),
                etag=page_artifact.sha256,
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )

            async with Client(server) as client:
                result = await client.call_tool("get_job_evidence", {"job_id": JOB_ID})

            self.assertFalse(result.is_error, result.content)
            self.assertEqual(result.structured_content["view"], "evidence")
            self.assertEqual(result.structured_content["source_job_id"], JOB_ID)
            self.assertEqual(
                result.structured_content["board"]["rendered_count"],
                1,
            )
            self.assertEqual(
                result.structured_content["board"]["pages"][0]["resource_uri"],
                f"vidxp://artifacts/{ARTIFACT_ID}/content.jpg",
            )
            self.assertEqual(
                result.structured_content["board"]["tiles"][0]["evidence_id"],
                "e" * 64,
            )
            serialized = json.dumps(result.structured_content)
            self.assertNotIn("source_fingerprint", serialized)
            self.assertIn("Board: 1/1 candidates", result.content[0].text)
            self.assertIn("1.000-2.000", result.content[0].text)
            self.assertIn("e" * 64, result.content[0].text)
            self.assertIn(
                f"vidxp://artifacts/{ARTIFACT_ID}/content.jpg",
                result.content[0].text,
            )
            self.assertTrue(
                any(isinstance(block, ImageContent) for block in result.content)
            )
            self.assertTrue(
                any(isinstance(block, ResourceLink) for block in result.content)
            )

    async def test_one_job_search_returns_frame_and_ready_clip(self):
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            self.skipTest("ffmpeg and ffprobe are required for the real MCP proof")
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNg"
            "YAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )
        clip_id = "423456781234423481234567890abcde"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frame_path = root / "frame.png"
            clip_path = root / "clip.mp4"
            frame_path.write_bytes(png_bytes)
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=green:s=64x48:d=4:r=2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(clip_path),
                ],
                check=True,
            )
            clip_bytes = clip_path.read_bytes()
            now = datetime.now(timezone.utc)
            frame = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                generation_id="523456781234423481234567890abcde",
                job_id=JOB_ID,
                kind=ArtifactKind.evidence_frame,
                profile="png",
                mime_type="image/png",
                byte_size=len(png_bytes),
                sha256=hashlib.sha256(png_bytes).hexdigest(),
                state=ArtifactState.ready,
                created_at=now,
            )
            clip = Artifact(
                artifact_id=clip_id,
                media_id=MEDIA_ID,
                job_id=JOB_ID,
                kind=ArtifactKind.snippet,
                profile="compatible_mp4",
                mime_type="video/mp4",
                byte_size=len(clip_bytes),
                sha256=hashlib.sha256(clip_bytes).hexdigest(),
                state=ArtifactState.ready,
                created_at=now,
            )
            context = self.context(root)
            context.jobs.submit_search.return_value = queued_job().model_copy(
                update={"kind": JobKind.search}
            )
            context.jobs.submit_query.return_value = queued_job().model_copy(
                update={"kind": JobKind.query}
            )
            context.jobs.get.side_effect = (
                search_evidence_job(frame, clip),
                search_evidence_job(frame, clip),
                query_evidence_job(frame, clip),
                query_evidence_job(frame, clip),
            )

            def resource(artifact_id):
                if artifact_id == ARTIFACT_ID:
                    return LocalFileResource(
                        path=frame_path,
                        filename=f"evidence_frame-{ARTIFACT_ID}.png",
                        mime_type="image/png",
                        byte_size=len(png_bytes),
                        etag=frame.sha256,
                    )
                return LocalFileResource(
                    path=clip_path,
                    filename=f"snippet-{clip_id}.mp4",
                    mime_type="video/mp4",
                    byte_size=len(clip_bytes),
                    etag=clip.sha256,
                )

            context.application.open_artifact_content.side_effect = resource
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read", "vidxp.write"}),
                ),
            )
            async with Client(server) as client:
                submitted = await client.call_tool(
                    "search_moments",
                    {
                        "command": {
                            "query": "green frame",
                            "modalities": ["scene"],
                            "evidence_delivery": {
                                "mode": "keyframes_and_clips",
                                "max_items": 1,
                            },
                        },
                        "idempotency_key": "one-job-search-0001",
                    },
                )
                completed = await client.call_tool("get_job", {"job_id": JOB_ID})
                presented = await client.call_tool(
                    "get_job_evidence", {"job_id": JOB_ID}
                )
                clip_resource = await client.read_resource(
                    f"vidxp://artifacts/{clip_id}/content.mp4"
                )
                submitted_query = await client.call_tool(
                    "query_video",
                    {
                        "command": {
                            "question": "Which frame is green?",
                            "modalities": ["scene"],
                            "evidence_delivery": {
                                "mode": "keyframes_and_clips",
                                "max_items": 1,
                            },
                        },
                        "idempotency_key": "one-job-query-0001",
                    },
                )
                completed_query = await client.call_tool("get_job", {"job_id": JOB_ID})
                presented_query = await client.call_tool(
                    "get_job_evidence", {"job_id": JOB_ID}
                )

            self.assertFalse(submitted.is_error)
            self.assertFalse(completed.is_error)
            self.assertFalse(presented.is_error)
            self.assertFalse(submitted_query.is_error)
            self.assertFalse(completed_query.is_error)
            self.assertFalse(presented_query.is_error)
            item = completed.structured_content["result"]["result"][
                "evidence_delivery"
            ]["items"][0]
            self.assertIsNotNone(item["keyframe"])
            self.assertIsNotNone(item["clip"])
            links = [
                block for block in presented.content if isinstance(block, ResourceLink)
            ]
            self.assertEqual(len(links), 2)
            self.assertEqual(presented.structured_content["view"], "evidence")
            self.assertIsNone(presented.structured_content["answer"])
            self.assertTrue(
                any(isinstance(block, ImageContent) for block in presented.content)
            )
            self.assertEqual(len(clip_resource.contents), 1)
            duration = float(
                subprocess.check_output(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        str(clip_path),
                    ],
                    text=True,
                ).strip()
            )
            self.assertAlmostEqual(duration, 4.0, places=2)
            self.assertEqual(
                hashlib.sha256(clip_path.read_bytes()).hexdigest(), clip.sha256
            )
            query_result = completed_query.structured_content["result"]["result"]
            cited = query_result["claims"][0]["evidence_ids"][0]
            self.assertEqual(
                presented_query.structured_content["answer"]["claims"][0][
                    "evidence_ids"
                ],
                [cited],
            )
            self.assertIn("Grounded answer:", presented_query.content[0].text)
            self.assertIn(cited, presented_query.content[0].text)
            self.assertEqual(cited, query_result["evidence"][0]["evidence_id"])
            self.assertEqual(
                cited,
                query_result["evidence_delivery"]["items"][0]["evidence_id"],
            )
            context.jobs.submit_search.assert_called_once()
            context.jobs.submit_query.assert_called_once()
            context.jobs.submit_snippet.assert_not_called()
            context.application.get_artifact.assert_not_called()

    async def test_retry_job_uses_shared_stable_idempotency(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.retry.return_value = queued_job()
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.write"}),
                ),
            )
            arguments = {
                "job_id": JOB_ID,
                "idempotency_key": "agent-retry-0001",
            }
            async with Client(server) as client:
                first = await client.call_tool("retry_job", arguments)
                second = await client.call_tool("retry_job", arguments)

        self.assertFalse(first.is_error)
        self.assertEqual(
            first.structured_content,
            second.structured_content,
        )
        calls = context.jobs.retry.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0].kwargs["retry_id"],
            calls[1].kwargs["retry_id"],
        )

    async def test_clip_submission_and_lazy_artifact_download(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "clip.mp4"
            clip.write_bytes(b"clip-content")
            context = self.context(root)
            context.jobs.submit_snippet.return_value = queued_job().model_copy(
                update={"kind": JobKind.snippet}
            )
            context.application.get_artifact.return_value = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                kind=ArtifactKind.snippet,
                profile="compatible_mp4",
                mime_type="video/mp4",
                byte_size=12,
                sha256="1" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            context.application.open_artifact_content.return_value = LocalFileResource(
                path=clip,
                filename=f"snippet-{ARTIFACT_ID}.mp4",
                mime_type="video/mp4",
                byte_size=12,
                etag="1" * 64,
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                submitted = await client.call_tool(
                    "create_clip",
                    {
                        "command": {
                            "media_id": MEDIA_ID,
                            "start_seconds": 9,
                            "end_seconds": 17,
                        },
                        "idempotency_key": "agent-clip-0001",
                    },
                )
                linked = await client.call_tool(
                    "get_artifact_download",
                    {"artifact_id": ARTIFACT_ID},
                )
                link = linked.content[0]
                downloaded = await client.read_resource(str(link.uri))

        self.assertFalse(submitted.is_error)
        submitted_command = context.jobs.submit_snippet.call_args.args[0]
        self.assertEqual(submitted_command.media_id, MEDIA_ID)
        self.assertEqual(submitted_command.start_seconds, 9)
        self.assertEqual(submitted_command.end_seconds, 17)
        self.assertIsInstance(link, ResourceLink)
        self.assertEqual(link.mime_type, "video/mp4")
        self.assertEqual(link.size, 12)
        self.assertEqual(link.name, f"snippet-{ARTIFACT_ID}.mp4")
        self.assertEqual(linked.structured_content["artifact_id"], ARTIFACT_ID)
        self.assertEqual(linked.structured_content["filename"], link.name)
        self.assertEqual(linked.structured_content["mime_type"], "video/mp4")
        self.assertEqual(linked.structured_content["byte_size"], 12)
        self.assertEqual(linked.structured_content["sha256"], "1" * 64)
        self.assertEqual(linked.structured_content["etag"], f'"{"1" * 64}"')
        self.assertEqual(linked.structured_content["state"], "ready")
        self.assertEqual(linked.structured_content["delivery_mode"], "local_file")
        self.assertEqual(Path(linked.structured_content["local_path"]), clip)
        self.assertIsNone(linked.structured_content["download_url"])
        self.assertEqual(downloaded.contents[0].blob, "Y2xpcC1jb250ZW50")

    async def test_artifact_resource_read_enforces_exact_configured_boundary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"12345678"
            clip = root / "boundary.mp4"
            clip.write_bytes(content)
            context = self.context(root, mcp_max_resource_bytes=len(content))
            context.application.open_artifact_content.return_value = LocalFileResource(
                path=clip,
                filename=f"snippet-{ARTIFACT_ID}.mp4",
                mime_type="video/mp4",
                byte_size=len(content),
                etag=hashlib.sha256(content).hexdigest(),
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(subject="agent", scopes=frozenset({"*"})),
            )
            async with Client(server) as client:
                resource = await client.read_resource(
                    f"vidxp://artifacts/{ARTIFACT_ID}/content.mp4"
                )

        self.assertEqual(
            resource.contents[0].blob,
            base64.b64encode(content).decode(),
        )

    async def test_artifact_resource_read_rejects_oversize_for_stdio_and_http(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"123456789"
            clip = root / "oversize.mp4"
            clip.write_bytes(content)
            for transport in ("local_stdio", "streamable_http"):
                with self.subTest(transport=transport):
                    options = (
                        {
                            "artifact_download_public_url": (
                                "https://public.example/artifact-download"
                            ),
                            "artifact_download_secret": "d" * 32,
                        }
                        if transport == "streamable_http"
                        else {}
                    )
                    context = self.context(
                        root,
                        mcp_max_resource_bytes=len(content) - 1,
                        **options,
                    )
                    context.application.open_artifact_content.return_value = (
                        LocalFileResource(
                            path=clip,
                            filename=f"snippet-{ARTIFACT_ID}.mp4",
                            mime_type="video/mp4",
                            byte_size=len(content),
                            etag=hashlib.sha256(content).hexdigest(),
                        )
                    )
                    server = create_mcp_server(
                        context,
                        default_principal=Principal(
                            subject="agent", scopes=frozenset({"*"})
                        ),
                        artifact_delivery=transport,
                    )
                    async with Client(server) as client:
                        with self.assertRaises(MCPError) as caught:
                            await client.read_resource(
                                f"vidxp://artifacts/{ARTIFACT_ID}/content.mp4"
                            )

                    self.assertEqual(
                        caught.exception.data["code"],
                        "artifact_resource_too_large",
                    )
                    self.assertEqual(caught.exception.data["maximum_bytes"], 8)
                    remediation = caught.exception.data["remediation"]
                    self.assertIn(
                        "local_path" if transport == "local_stdio" else "download_url",
                        remediation,
                    )

    async def test_artifact_resource_read_rejects_tampered_actual_size(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "tampered.mp4"
            clip.write_bytes(b"changed-after-registration")
            context = self.context(root, mcp_max_resource_bytes=1024)
            context.application.open_artifact_content.return_value = LocalFileResource(
                path=clip,
                filename=f"snippet-{ARTIFACT_ID}.mp4",
                mime_type="video/mp4",
                byte_size=8,
                etag="1" * 64,
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(subject="agent", scopes=frozenset({"*"})),
            )
            async with Client(server) as client:
                with self.assertRaises(MCPError) as caught:
                    await client.read_resource(
                        f"vidxp://artifacts/{ARTIFACT_ID}/content.mp4"
                    )

        self.assertEqual(
            caught.exception.data["code"],
            "artifact_resource_size_mismatch",
        )

    async def test_isolated_stdio_preserves_resource_without_misleading_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(
                root,
                mcp_stdio_filesystem_accessible=False,
            )
            context.application.get_artifact.return_value = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                kind=ArtifactKind.snippet,
                profile="compatible_mp4",
                mime_type="video/mp4",
                byte_size=12,
                sha256="1" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="isolated-agent",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "get_artifact_download",
                    {"artifact_id": ARTIFACT_ID},
                )

        self.assertFalse(result.is_error)
        self.assertIsInstance(result.content[0], ResourceLink)
        self.assertEqual(result.structured_content["delivery_mode"], "mcp_resource")
        self.assertIsNone(result.structured_content["local_path"])
        self.assertIsNone(result.structured_content["file_uri"])
        self.assertEqual(
            result.structured_content["delivery_error"]["code"],
            "local_path_unavailable",
        )
        context.application.open_artifact_content.assert_not_called()

    async def test_isolated_stdio_uses_configured_public_download(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                mcp_stdio_filesystem_accessible=False,
                artifact_download_public_url=(
                    "https://public.example/artifact-download"
                ),
                artifact_download_secret="d" * 32,
            )
            context.application.get_artifact.return_value = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                kind=ArtifactKind.snippet,
                profile="compatible_mp4",
                mime_type="video/mp4",
                byte_size=12,
                sha256="1" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="isolated-agent",
                    scopes=frozenset({"*"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "get_artifact_download",
                    {"artifact_id": ARTIFACT_ID},
                )

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["delivery_mode"], "https_download")
        self.assertIsNone(result.structured_content["local_path"])
        self.assertTrue(
            result.structured_content["download_url"].startswith(
                f"https://public.example/artifact-download/{ARTIFACT_ID}#"
            )
        )

    async def test_remote_artifact_delivery_preserves_resource_without_public_origin(
        self,
    ):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.get_artifact.return_value = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                kind=ArtifactKind.snippet,
                profile="compatible_mp4",
                mime_type="video/mp4",
                byte_size=12,
                sha256="1" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="remote-agent",
                    scopes=frozenset({"*"}),
                ),
                artifact_delivery="streamable_http",
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "get_artifact_download",
                    {"artifact_id": ARTIFACT_ID},
                )

        self.assertFalse(result.is_error)
        self.assertIsInstance(result.content[0], ResourceLink)
        self.assertEqual(result.structured_content["delivery_mode"], "mcp_resource")
        self.assertIsNone(result.structured_content["local_path"])
        self.assertIsNone(result.structured_content["file_uri"])
        self.assertIsNone(result.structured_content["download_url"])
        self.assertIsNone(result.structured_content["download_expires_at"])
        self.assertEqual(
            result.structured_content["delivery_error"]["code"],
            "public_download_origin_unavailable",
        )

    async def test_remote_oversize_artifact_reports_unavailable_without_link(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory), mcp_max_resource_bytes=8)
            context.application.get_artifact.return_value = Artifact(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                kind=ArtifactKind.snippet,
                profile="compatible_mp4",
                mime_type="video/mp4",
                byte_size=12,
                sha256="1" * 64,
                state=ArtifactState.ready,
                created_at=datetime.now(timezone.utc),
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="remote-agent",
                    scopes=frozenset({"*"}),
                ),
                artifact_delivery="streamable_http",
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "get_artifact_download",
                    {"artifact_id": ARTIFACT_ID},
                )

        self.assertFalse(result.is_error)
        self.assertFalse(
            any(isinstance(block, ResourceLink) for block in result.content)
        )
        self.assertEqual(result.structured_content["delivery_mode"], "unavailable")
        self.assertIsNone(result.structured_content["resource_uri"])
        self.assertEqual(
            result.structured_content["delivery_error"]["code"],
            "artifact_delivery_unavailable",
        )
        self.assertIn(
            "VIDXP_ARTIFACT_DOWNLOAD_PUBLIC_URL",
            result.structured_content["delivery_error"]["message"],
        )

    async def test_application_errors_are_machine_readable_and_safe(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.index_status.side_effect = ApplicationError(
                "index_busy",
                ErrorCategory.conflict,
                "The index is busy.",
                retryable=True,
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool("get_index_status", {})

        self.assertTrue(result.is_error)
        error_text = result.content[0].text
        self.assertIn('"code":"index_busy"', error_text)
        self.assertIn('"protocol_code":-32009', error_text)
        self.assertIn('"retryable":true', error_text)

    async def test_unexpected_errors_do_not_leak_exception_text(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.index_status.side_effect = RuntimeError(
                "database-password"
            )
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="agent",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                with self.assertRaises(MCPError) as caught:
                    await client.call_tool("get_index_status", {})

        self.assertEqual(caught.exception.code, -32603)
        self.assertNotIn("database-password", caught.exception.message)
        self.assertEqual(caught.exception.data["code"], "internal_error")

    async def test_write_tools_enforce_repository_scope(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            server = create_mcp_server(
                context,
                default_principal=Principal(
                    subject="reader",
                    scopes=frozenset({"vidxp.read"}),
                ),
            )
            async with Client(server) as client:
                result = await client.call_tool(
                    "start_indexing",
                    {
                        "command": {
                            "media_id": MEDIA_ID,
                            "modalities": ["scene"],
                        },
                        "idempotency_key": "agent-request-0001",
                    },
                )
                evidence_clip = await client.call_tool(
                    "search_moments",
                    {
                        "command": {
                            "query": "green frame",
                            "evidence_delivery": {"mode": "keyframes_and_clips"},
                        },
                        "idempotency_key": "agent-request-0002",
                    },
                )

        self.assertTrue(result.is_error)
        self.assertIn('"protocol_code":-32003', result.content[0].text)
        self.assertIn('"required_scope":"vidxp.write"', result.content[0].text)
        self.assertTrue(evidence_clip.is_error)
        self.assertIn('"required_scope":"vidxp.write"', evidence_clip.content[0].text)
        context.jobs.submit_index.assert_not_called()
        context.jobs.submit_search.assert_not_called()

    async def test_stdio_entrypoint_serves_the_filesystem_aware_surface(self):
        with TemporaryDirectory() as directory:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "vidxp.mcp_cli",
                    "--index-directory",
                    directory,
                    "--device",
                    "cpu",
                ],
                cwd=Path(__file__).parents[1],
            )
            async with Client(stdio_client(parameters)) as client:
                discovered = await client.list_tools()
                result = await client.call_tool("list_capabilities", {})

        self.assertEqual(
            [tool.name for tool in discovered.tools],
            STDIO_MCP_TOOL_NAMES,
        )
        self.assertNotIn(
            "create_media_upload",
            [tool.name for tool in discovered.tools],
        )
        self.assertEqual(
            [item["name"] for item in result.structured_content["items"]],
            ["speech", "sound", "scene", "actor", "action"],
        )

    async def test_streamable_http_works_with_the_official_remote_client(self):
        token = "s" * 32
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                static_token=token,
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(context=context),
                    host="127.0.0.1",
                    port=port,
                    log_level="critical",
                )
            )
            serving = asyncio.create_task(server.serve())
            try:
                for _attempt in range(200):
                    if server.started:
                        break
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.01)
                else:
                    self.fail("The MCP HTTP fixture did not start.")
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {token}"}
                ) as http_client:
                    transport = streamable_http_client(
                        f"http://127.0.0.1:{port}/mcp",
                        http_client=http_client,
                    )
                    async with Client(transport) as client:
                        discovered = await client.list_tools()
                        result = await client.call_tool(
                            "list_capabilities",
                            {},
                        )
            finally:
                server.should_exit = True
                await serving

        self.assertEqual(len(discovered.tools), 23)
        self.assertNotIn(
            "create_media_upload",
            {tool.name for tool in discovered.tools},
        )
        self.assertIn(
            "create_evidence_board",
            {tool.name for tool in discovered.tools},
        )
        self.assertEqual(result.structured_content, {"items": []})

    async def test_oidc_verifier_projects_the_shared_validated_token(self):
        authenticator = Mock(spec=OIDCBearerAuthenticator)
        authenticator.authenticate_bearer.return_value = AuthenticatedBearer(
            principal=Principal(
                subject="user-1",
                client_id="client-1",
                scopes=frozenset({"vidxp.read"}),
            ),
            expires_at=1_800_000_000,
            resource="https://api.example/mcp",
            claims={"iss": "https://issuer.example"},
        )

        token = await VidXPTokenVerifier(authenticator).verify_token("token")

        self.assertEqual(token.subject, "user-1")
        self.assertEqual(token.client_id, "client-1")
        self.assertEqual(token.scopes, ["vidxp.read"])
        self.assertEqual(token.expires_at, 1_800_000_000)
        self.assertEqual(token.resource, "https://api.example/mcp")

    def test_sdk_transport_security_rejects_host_and_origin(self):
        token = "s" * 32
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                static_token=token,
                http_trusted_hosts=("*",),
                mcp_allowed_hosts=("mcp.example",),
                mcp_allowed_origins=("https://client.example",),
            )
            with TestClient(create_app(context=context)) as client:
                bad_host = client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Host": "other.example",
                    },
                    json={},
                )
                bad_origin = client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Host": "mcp.example",
                        "Origin": "https://other.example",
                    },
                    json={},
                )
                unauthenticated_bad_origin = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Host": "mcp.example",
                        "Origin": "https://other.example",
                    },
                    json={},
                )

        self.assertEqual(bad_host.status_code, 421)
        self.assertEqual(bad_origin.status_code, 403)
        self.assertEqual(unauthenticated_bad_origin.status_code, 403)


if __name__ == "__main__":
    unittest.main()
