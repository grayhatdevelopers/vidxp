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
from vidxp.local_probe import (
    DESKTOP_LAUNCH_PROTOCOL_VERSION,
    PRODUCT_ID,
    build_desktop_probe,
    desktop_model_cache_catalog,
    _resolved_launcher_path,
)


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
                "vidxp.local_probe._installed_search_capabilities",
                return_value=["actor", "dialogue", "scene"],
            ),
            patch(
                "vidxp.local_probe.media_runtime_is_initialized",
                return_value=True,
            ),
        ):
            return build_desktop_probe(**values)

    def test_probe_reports_stable_identity_and_contract_compatibility(self):
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
        self.assertEqual(
            payload["compatibility"]["code"],
            "contract_compatible",
        )
        self.assertEqual(
            payload["launch_contract"],
            {
                "protocol_version": DESKTOP_LAUNCH_PROTOCOL_VERSION,
                "surface": "browser",
                "command": "ui",
            },
        )
        self.assertTrue(payload["capabilities"]["frontend"]["launchable"])
        self.assertEqual(
            set(payload["surfaces"]),
            {"worker", "browser", "mcp", "server"},
        )
        self.assertEqual(
            payload["search_capabilities"],
            ["actor", "dialogue", "scene"],
        )
        self.assertTrue(all(surface["launchable"] for surface in payload["surfaces"].values()))

    def test_differing_package_versions_remain_contract_compatible(self):
        payload = self.build(desktop_version="0.5.0")

        self.assertEqual(payload["product_version"], "0.4.0b0")
        self.assertEqual(payload["compatibility"]["desktop_version"], "0.5.0")
        self.assertTrue(payload["compatibility"]["compatible"])
        self.assertEqual(
            payload["compatibility"]["code"],
            "contract_compatible",
        )

    def test_missing_optional_frontend_does_not_make_product_incompatible(self):
        with (
            patch("vidxp.local_probe.__version__", "0.4.0b0"),
            patch("vidxp.local_probe._module_available", return_value=False),
            patch(
                "vidxp.local_probe._installed_search_capabilities",
                return_value=[],
            ),
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
        self.assertIn("command-line installation is usable", frontend["message"])
        self.assertIn("package manager", frontend["remediation"])
        self.assertIn("'frontend' extra", frontend["remediation"])
        self.assertFalse(payload["surfaces"]["mcp"]["launchable"])
        self.assertFalse(payload["surfaces"]["server"]["launchable"])
        self.assertFalse(payload["surfaces"]["worker"]["launchable"])

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
                    "vidxp.local_probe._installed_search_capabilities",
                    return_value=[],
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

    def test_desktop_version_is_informational_not_a_compatibility_gate(self):
        payload = self.build(desktop_version="desktop-development-build")

        self.assertTrue(payload["compatibility"]["compatible"])
        self.assertEqual(
            payload["compatibility"]["desktop_version"],
            "desktop-development-build",
        )

    def test_windows_extensionless_console_script_preserves_raw_identity(self):
        with TemporaryDirectory() as directory:
            launcher = Path(directory) / "vidxp"
            executable = launcher.with_suffix(".exe")
            executable.write_bytes(b"shim")
            launcher.with_suffix(".com").write_bytes(b"different shim")

            resolved = _resolved_launcher_path(launcher, windows=True)

        self.assertEqual(resolved, str(launcher.resolve(strict=False)))

    def test_exact_launcher_and_symlink_keep_canonical_identity(self):
        with TemporaryDirectory() as directory:
            executable = Path(directory) / "vidxp.exe"
            executable.write_bytes(b"shim")
            self.assertEqual(
                _resolved_launcher_path(executable, windows=True),
                str(executable.resolve()),
            )
            link = Path(directory) / "linked-vidxp.exe"
            try:
                link.symlink_to(executable)
            except OSError:
                self.skipTest("Creating symlinks is unavailable")
            self.assertEqual(
                _resolved_launcher_path(link, windows=True),
                str(executable.resolve()),
            )

    def test_windows_launcher_does_not_resolve_unrelated_or_similar_siblings(self):
        with TemporaryDirectory() as directory:
            launcher = Path(directory) / "vidxp"
            (Path(directory) / "vidxp-helper.exe").write_bytes(b"other")
            (Path(directory) / "other.exe").write_bytes(b"other")

            resolved = _resolved_launcher_path(launcher, windows=True)

        self.assertEqual(resolved, str(launcher.resolve(strict=False)))

    def test_non_windows_launcher_resolution_does_not_add_executable_suffix(self):
        with TemporaryDirectory() as directory:
            launcher = Path(directory) / "vidxp"
            launcher.with_suffix(".exe").write_bytes(b"shim")

            resolved = _resolved_launcher_path(launcher, windows=False)

        self.assertEqual(resolved, str(launcher.resolve(strict=False)))

    def test_desktop_model_catalog_is_derived_from_canonical_specs(self):
        catalog = desktop_model_cache_catalog()

        self.assertEqual(len(catalog), 5)
        self.assertEqual(
            {item["id"] for item in catalog},
            {
                "google/siglip2-base-patch16-224",
                "Qwen/Qwen3-Embedding-0.6B",
                "dropbox-dash/faster-whisper-large-v3-turbo",
                "yunet",
                "sface",
            },
        )


if __name__ == "__main__":
    unittest.main()
