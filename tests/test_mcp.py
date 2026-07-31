import asyncio
import base64
import contextlib
import io
import json
import socket
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import httpx2
import uvicorn
from fastapi.testclient import TestClient
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import ResourceLink

from vidxp.application_models import (
    ApplicationError,
    Artifact,
    ErrorCategory,
    ErrorDetail,
    IndexStatus,
    Job,
    JobKind,
    JobPage,
    JobQueue,
    JobState,
    MediaPage,
    Principal,
    QueryVideoCommand,
    WorkspaceOverview,
)
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
from vidxp.composition import HttpApplicationContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.core.artifacts import ArtifactKind, ArtifactState
from vidxp.job_service import JobService
from vidxp.mcp import VidXPTokenVerifier, create_mcp_server
from vidxp.mcp_cli import main as mcp_main
from vidxp.mcp_cli import stdio_client_config
from vidxp.ports import LocalFileResource
from vidxp.settings import VidXPSettings


MEDIA_ID = "123456781234423481234567890abcde"
JOB_ID = "223456781234423481234567890abcde"
ARTIFACT_ID = "323456781234423481234567890abcde"


def queued_job() -> Job:
    return Job(
        job_id=JOB_ID,
        kind=JobKind.index,
        state=JobState.queued,
        queue=JobQueue.cpu,
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
    ) -> HttpApplicationContext:
        settings = VidXPSettings(
            repository_root=root,
            runtime_backend="cpu",
            http_auth_mode="static" if static_token is not None else "none",
            http_static_bearer_token=static_token,
            http_trusted_hosts=http_trusted_hosts,
            mcp_allowed_hosts=mcp_allowed_hosts,
            mcp_allowed_origins=mcp_allowed_origins,
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

    async def test_curated_tools_have_structured_output_schemas(self):
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
                discovered = await client.list_tools()
                result = await client.call_tool("list_capabilities", {})

        self.assertEqual(
            [tool.name for tool in discovered.tools],
            [
                "get_workspace",
                "list_capabilities",
                "get_capability",
                "get_runtime_readiness",
                "list_media",
                "get_media",
                "get_index_status",
                "start_indexing",
                "prepare_models",
                "search_moments",
                "query_video",
                "create_clip",
                "get_artifact_download",
                "list_jobs",
                "get_job",
                "retry_job",
                "cancel_job",
            ],
        )
        self.assertTrue(
            all(tool.output_schema is not None for tool in discovered.tools)
        )
        tools = {tool.name: tool for tool in discovered.tools}
        for name in ("search_moments", "query_video"):
            schema = tools[name].input_schema
            command_ref = schema["properties"]["command"]["$ref"]
            command_name = command_ref.rsplit("/", 1)[-1]
            media_description = schema["$defs"][command_name]["properties"][
                "media_id"
            ]["description"]
            self.assertIn("omit it", media_description)
            self.assertIn("active index snapshot", media_description)
        self.assertEqual(result.structured_content, {"items": []})
        self.assertFalse(result.is_error)

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
        self.assertIn("COPY/PASTE MCP CLIENT CONFIG", output.getvalue())
        self.assertIn('"mcpServers"', output.getvalue())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            mcp_main(["--print-config", "--repository", "library"])
        rendered = json.loads(output.getvalue())
        self.assertEqual(
            rendered["mcpServers"]["vidxp"]["args"],
            ["--repository", "library"],
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
        self.assertIn("Tools: 17", rendered)
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
                            "modalities": ["scene", "dialogue"],
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
                modalities=("scene", "dialogue"),
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
                        "remediation": "vidxp prepare --modalities dialogue",
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
            context.jobs.submit_snippet.return_value = (
                queued_job().model_copy(update={"kind": JobKind.snippet})
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
            context.application.open_artifact_content.return_value = (
                LocalFileResource(
                    path=clip,
                    filename=f"snippet-{ARTIFACT_ID}.mp4",
                    mime_type="video/mp4",
                    byte_size=12,
                    etag="1" * 64,
                )
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
        self.assertEqual(downloaded.contents[0].blob, "Y2xpcC1jb250ZW50")

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

        self.assertTrue(result.is_error)
        self.assertIn('"protocol_code":-32003', result.content[0].text)
        self.assertIn('"required_scope":"vidxp.write"', result.content[0].text)
        context.jobs.submit_index.assert_not_called()

    async def test_stdio_entrypoint_serves_the_same_curated_surface(self):
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
            [
                "get_workspace",
                "list_capabilities",
                "get_capability",
                "get_runtime_readiness",
                "list_media",
                "get_media",
                "get_index_status",
                "start_indexing",
                "prepare_models",
                "search_moments",
                "query_video",
                "create_clip",
                "get_artifact_download",
                "list_jobs",
                "get_job",
                "retry_job",
                "cancel_job",
            ],
        )
        self.assertEqual(
            [item["name"] for item in result.structured_content["items"]],
            ["dialogue", "scene", "actor"],
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

        self.assertEqual(len(discovered.tools), 17)
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
