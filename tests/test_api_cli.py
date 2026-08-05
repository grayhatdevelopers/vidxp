import unittest
import asyncio
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vidxp.api_cli import _shared_settings, main
from vidxp.settings import HttpAuthMode, VidXPSettings


class ApiCliTests(unittest.TestCase):
    def test_help_exits_without_starting_the_service(self):
        output = StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--help"])

        self.assertEqual(caught.exception.code, 0)
        self.assertIn("VIDXP_* environment variables", output.getvalue())
        self.assertIn("--port", output.getvalue())
        self.assertIn("--share", output.getvalue())

    def test_share_mode_configures_managed_static_server(self):
        settings, token = _shared_settings(
            VidXPSettings(),
            host="192.168.100.131",
            token="x" * 43,
        )

        self.assertEqual(settings.http_bind_host, "192.168.100.131")
        self.assertEqual(settings.http_auth_mode, HttpAuthMode.static)
        self.assertEqual(token, "x" * 43)
        self.assertIn("192.168.100.131", settings.http_trusted_hosts)
        self.assertIn("192.168.100.131:*", settings.mcp_allowed_hosts)
        settings.validate_http_server()

    def test_share_mode_omits_unavailable_browser_upload_tools(self):
        from mcp.client import Client

        from vidxp.application_models import Principal
        from vidxp.composition import create_http_application
        from vidxp.mcp import create_mcp_server

        with TemporaryDirectory() as directory:
            settings, _token = _shared_settings(
                VidXPSettings(data_dir=Path(directory)),
                host="192.168.100.131",
                token="x" * 43,
            )
            context = create_http_application(settings)

            async def discover() -> set[str]:
                server = create_mcp_server(
                    context,
                    default_principal=Principal(
                        subject="lan-client",
                        scopes=frozenset({"*"}),
                    ),
                    artifact_delivery="streamable_http",
                )
                async with Client(server) as client:
                    return {
                        tool.name for tool in (await client.list_tools()).tools
                    }

            try:
                names = asyncio.run(discover())
            finally:
                context.close()
        self.assertNotIn("create_media_upload", names)
        self.assertNotIn("get_media_upload", names)

    def test_share_mode_reuses_an_explicit_static_token(self):
        settings, token = _shared_settings(
            VidXPSettings(
                http_auth_mode=HttpAuthMode.static,
                http_static_bearer_token="configured-token-1234567890123456",
            ),
            host="192.168.100.131",
            token="managed-token-123456789012345678",
        )

        self.assertEqual(token, "configured-token-1234567890123456")

    def test_main_shares_on_the_detected_address(self):
        import uvicorn
        from vidxp import api

        output = StringIO()
        with (
            patch.object(uvicorn, "run") as run,
            patch.object(
                api,
                "create_app",
            ) as create_app,
            patch(
                "vidxp.api_cli.primary_lan_address",
                return_value="192.168.100.131",
            ),
            patch(
                "vidxp.api_cli.load_or_create_api_share_token",
                return_value="x" * 43,
            ),
            redirect_stdout(output),
        ):
            main(["--share"])

        run.assert_called_once_with(
            create_app.return_value,
            host="192.168.100.131",
            port=32191,
        )
        self.assertIn(
            "MCP: http://192.168.100.131:32191/mcp",
            output.getvalue(),
        )
        self.assertIn("Bearer token:", output.getvalue())
        self.assertIn("Browser upload tools are omitted", output.getvalue())

    def test_main_accepts_an_explicit_port(self):
        import uvicorn
        from vidxp import api

        with (
            patch.object(uvicorn, "run") as run,
            patch.object(api, "create_app") as create_app,
        ):
            main(["--port", "32192"])

        run.assert_called_once_with(
            create_app.return_value,
            host="127.0.0.1",
            port=32192,
        )

    def test_share_details_can_be_resolved_without_starting_the_server(self):
        import uvicorn

        output = StringIO()
        with (
            patch.object(uvicorn, "run") as run,
            patch(
                "vidxp.api_cli.primary_lan_address",
                return_value="192.168.100.131",
            ),
            patch(
                "vidxp.api_cli.load_or_create_api_share_token",
                return_value="x" * 43,
            ),
            redirect_stdout(output),
        ):
            main(["--share", "--port", "32192", "--print-share-details"])

        run.assert_not_called()
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "origin": "http://192.168.100.131:32192",
                "host": "192.168.100.131",
                "port": 32192,
                "health_url": "http://192.168.100.131:32192/health",
                "mcp_url": "http://192.168.100.131:32192/mcp",
                "bearer_token": "x" * 43,
            },
        )


if __name__ == "__main__":
    unittest.main()
