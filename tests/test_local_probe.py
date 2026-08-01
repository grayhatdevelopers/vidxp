import json
import os
import platform
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.application_models import ApplicationError
from vidxp.local_probe import PRODUCT_ID, build_desktop_probe


class LocalProbeTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def build(self, **overrides):
        values = {
            "desktop_version": "0.4.0-b",
            "request_id": "challenge-123",
            "launcher": Path("bin/vidxp").resolve(),
            "data_root": Path("data").resolve(),
            "repository_root": Path("repository").resolve(),
        }
        values.update(overrides)
        with (
            patch("vidxp.local_probe.__version__", "0.4.0b0"),
            patch("vidxp.local_probe._module_available", return_value=True),
            patch(
                "vidxp.local_probe.media_runtime_is_initialized",
                return_value=True,
            ),
        ):
            return build_desktop_probe(**values)

    def test_probe_reports_stable_identity_and_normalized_compatibility(self):
        payload = self.build()

        self.assertEqual(payload["product"], PRODUCT_ID)
        self.assertEqual(payload["product_version"], "0.4.0b0")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["protocol_version"], 1)
        self.assertEqual(payload["request_id"], "challenge-123")
        self.assertEqual(payload["launcher"], str(Path("bin/vidxp").resolve()))
        self.assertEqual(
            payload["runtime"],
            {
                "python_executable": str(Path(sys.executable).resolve()),
                "python_version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "prefix": str(Path(sys.prefix).resolve()),
                "base_prefix": str(Path(sys.base_prefix).resolve()),
            },
        )
        self.assertTrue(payload["compatibility"]["compatible"])
        self.assertEqual(payload["compatibility"]["code"], "compatible")
        self.assertEqual(
            payload["compatibility"]["desktop_version_normalized"],
            "0.4.0b0",
        )
        self.assertTrue(payload["capabilities"]["frontend"]["launchable"])

    def test_incompatible_product_version_has_stable_public_code(self):
        payload = self.build(desktop_version="0.5.0")

        self.assertFalse(payload["compatibility"]["compatible"])
        self.assertEqual(
            payload["compatibility"]["code"],
            "desktop_version_incompatible",
        )

    def test_missing_optional_frontend_does_not_make_product_incompatible(self):
        with (
            patch("vidxp.local_probe.__version__", "0.4.0b0"),
            patch("vidxp.local_probe._module_available", return_value=False),
            patch(
                "vidxp.local_probe.media_runtime_is_initialized",
                return_value=False,
            ),
        ):
            payload = build_desktop_probe(
                desktop_version="0.4.0-b",
                request_id="challenge",
            )

        frontend = payload["capabilities"]["frontend"]
        self.assertTrue(payload["compatibility"]["compatible"])
        self.assertTrue(frontend["optional"])
        self.assertFalse(frontend["available"])
        self.assertFalse(frontend["launchable"])
        self.assertEqual(frontend["code"], "frontend_unavailable")

    def test_command_bypasses_composition_and_does_not_create_roots(self):
        with TemporaryDirectory() as directory:
            data_root = Path(directory) / "missing-data"
            repository_root = Path(directory) / "missing-repository"
            with (
                patch.object(cli, "create_local_application") as compose,
                patch("vidxp.local_probe.__version__", "0.4.0b0"),
                patch(
                    "vidxp.local_probe._module_available",
                    return_value=False,
                ),
                patch(
                    "vidxp.local_probe.media_runtime_is_initialized",
                    return_value=False,
                ),
            ):
                result = self.runner.invoke(
                    cli.app,
                    [
                        "--data-dir",
                        str(data_root),
                        "--index-dir",
                        str(repository_root),
                        "desktop-probe",
                        "--json",
                        "--desktop-version",
                        "0.4.0-b",
                        "--request-id",
                        "nonce",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            compose.assert_not_called()
            self.assertFalse(data_root.exists())
            self.assertFalse(repository_root.exists())
            payload = json.loads(result.output)
            self.assertEqual(payload["data_root"], str(data_root.resolve()))
            self.assertEqual(payload["repository_root"], str(repository_root.resolve()))

    def test_probe_does_not_expose_environment_secrets(self):
        secrets = {
            "VIDXP_HTTP_STATIC_BEARER_TOKEN": "do-not-print-token",
            "AWS_SECRET_ACCESS_KEY": "do-not-print-aws-secret",
        }
        with patch.dict(os.environ, secrets):
            serialized = json.dumps(self.build())

        for name, value in secrets.items():
            self.assertNotIn(name, serialized)
            self.assertNotIn(value, serialized)

    def test_invalid_desktop_version_has_stable_public_error(self):
        with self.assertRaises(ApplicationError) as raised:
            self.build(desktop_version="not a version")

        self.assertEqual(raised.exception.code, "desktop_version_invalid")


if __name__ == "__main__":
    unittest.main()
