import unittest
from contextlib import redirect_stdout
from io import StringIO
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


if __name__ == "__main__":
    unittest.main()
