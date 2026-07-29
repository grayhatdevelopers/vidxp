import unittest
import sys
import asyncio
import socket
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

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    IndexStatus,
    Job,
    JobKind,
    JobQueue,
    JobState,
    Principal,
    QueryVideoCommand,
)
from vidxp.authentication import (
    AuthenticatedBearer,
    OIDCBearerAuthenticator,
    create_authenticator,
)
from vidxp.api import create_app
from vidxp.authorization import AuthorizationPolicy
from vidxp.composition import HttpApplicationContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.job_service import JobService
from vidxp.mcp import VidXPTokenVerifier, create_mcp_server
from vidxp.settings import VidXPSettings


MEDIA_ID = "123456781234423481234567890abcde"
JOB_ID = "223456781234423481234567890abcde"


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
            minimum_available_memory_mb=0,
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
                "list_capabilities",
                "get_capability",
                "get_index_status",
                "start_indexing",
                "search_moments",
                "query_video",
                "get_job",
                "cancel_job",
            ],
        )
        self.assertTrue(
            all(tool.output_schema is not None for tool in discovered.tools)
        )
        self.assertEqual(result.structured_content, {"items": []})
        self.assertFalse(result.is_error)

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
        self.assertEqual(
            calls[0].kwargs["job_id"],
            calls[1].kwargs["job_id"],
        )

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
                "list_capabilities",
                "get_capability",
                "get_index_status",
                "start_indexing",
                "search_moments",
                "query_video",
                "get_job",
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

        self.assertEqual(len(discovered.tools), 8)
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
