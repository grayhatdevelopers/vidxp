import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydantic import ValidationError

from vidxp.app_paths import (
    default_data_directory,
    default_model_directory,
    default_repository_directory,
)
from vidxp.settings import (
    ApplicationMode,
    LocalExecutionSettings,
    VidXPSettings,
)


class SettingsTests(unittest.TestCase):
    def test_artifact_download_settings_require_safe_complete_configuration(self):
        with self.assertRaises(ValidationError):
            VidXPSettings(artifact_download_ttl_seconds=59)
        with self.assertRaises(ValidationError):
            VidXPSettings(artifact_download_ttl_seconds=86_401)

        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            VidXPSettings(
                artifact_download_public_url=(
                    "http://downloads.example/artifact-download"
                ),
                artifact_download_secret="d" * 32,
            )
        with self.assertRaisesRegex(ValueError, "ending in /artifact-download"):
            VidXPSettings(
                artifact_download_public_url="https://downloads.example/files",
                artifact_download_secret="d" * 32,
            )
        with self.assertRaisesRegex(ValueError, "dedicated secret"):
            VidXPSettings(
                artifact_download_public_url=(
                    "https://downloads.example/artifact-download"
                ),
                artifact_download_secret="short",
            )

        settings = VidXPSettings(
            artifact_download_public_url=(
                "https://downloads.example/artifact-download/"
            ),
            artifact_download_secret="d" * 32,
            artifact_download_ttl_seconds=900,
            mcp_stdio_filesystem_accessible=False,
        )

        self.assertEqual(
            settings.artifact_download_public_url,
            "https://downloads.example/artifact-download",
        )
        self.assertEqual(settings.artifact_download_ttl_seconds, 900)
        self.assertFalse(settings.mcp_stdio_filesystem_accessible)

    def test_default_storage_is_under_the_per_user_data_directory(self):
        with TemporaryDirectory() as directory:
            unrelated_working_directory = Path(directory)
            original_working_directory = Path.cwd()
            try:
                os.chdir(unrelated_working_directory)
                settings = VidXPSettings()
            finally:
                os.chdir(original_working_directory)

        self.assertEqual(settings.data_dir, default_data_directory())
        self.assertEqual(
            settings.repository_root,
            default_repository_directory(settings.data_dir),
        )
        self.assertEqual(
            settings.model_cache,
            default_model_directory(settings.data_dir),
        )
        self.assertTrue(settings.data_dir.is_absolute())

    def test_data_directory_environment_derives_storage_defaults(self):
        with TemporaryDirectory() as directory:
            data_directory = Path(directory) / "vidxp-data"
            with patch.dict(
                os.environ,
                {"VIDXP_DATA_DIR": str(data_directory)},
                clear=False,
            ):
                settings = VidXPSettings()

        self.assertEqual(settings.data_dir, data_directory)
        self.assertEqual(
            settings.repository_root,
            data_directory / "repositories" / "default",
        )
        self.assertEqual(settings.model_cache, data_directory / "models")

    def test_explicit_storage_paths_override_derived_defaults(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_directory = root / "data"
            repository_root = root / "repository"
            model_cache = root / "model-cache"
            with patch.dict(
                os.environ,
                {"VIDXP_DATA_DIR": str(root / "environment-data")},
                clear=False,
            ):
                settings = VidXPSettings(
                    data_dir=data_directory,
                    repository_root=repository_root,
                    model_cache=model_cache,
                )

        self.assertEqual(settings.data_dir, data_directory)
        self.assertEqual(settings.repository_root, repository_root)
        self.assertEqual(settings.model_cache, model_cache)

    def test_saved_media_runtime_paths_become_settings_defaults(self):
        ffmpeg = Path("tools/ffmpeg").resolve()
        ffprobe = Path("tools/ffprobe").resolve()

        def executable(name):
            return str(ffmpeg if name == "ffmpeg" else ffprobe)

        with patch(
            "vidxp.settings.default_media_executable",
            side_effect=executable,
        ):
            settings = VidXPSettings()

        self.assertEqual(settings.ffmpeg_executable, str(ffmpeg))
        self.assertEqual(settings.ffprobe_executable, str(ffprobe))

    def test_local_execution_settings_preserve_the_data_directory(self):
        with TemporaryDirectory() as directory:
            expected = VidXPSettings(data_dir=Path(directory) / "data")

            reconstructed = LocalExecutionSettings.from_settings(
                expected
            ).application_settings()

        self.assertEqual(reconstructed.data_dir, expected.data_dir)
        self.assertEqual(
            reconstructed.repository_root,
            expected.repository_root,
        )
        self.assertEqual(reconstructed.model_cache, expected.model_cache)

    def test_unimplemented_remote_mode_is_rejected_explicitly(self):
        with self.assertRaisesRegex(
            ValidationError,
            "Remote client mode is not available",
        ):
            VidXPSettings(mode=ApplicationMode.remote)

    def test_upload_handoff_requires_https_url_and_dedicated_secret(self):
        base = {
            "upload_public_endpoint": "https://uploads.example/uploads/",
            "upload_internal_endpoint": "http://tusd:8080/uploads/",
            "upload_cleanup_token": "c" * 32,
            "upload_cors_origin_regex": r"^(https://vidxp\.example)$",
        }
        with self.assertRaisesRegex(ValidationError, "HTTPS outside loopback"):
            VidXPSettings(
                **base,
                upload_handoff_public_url=("http://vidxp.example/upload-handoff"),
                upload_handoff_secret="h" * 32,
            )
        with self.assertRaisesRegex(ValidationError, "dedicated secret"):
            VidXPSettings(
                **base,
                upload_handoff_public_url=("https://vidxp.example/upload-handoff"),
            )
        with self.assertRaisesRegex(ValidationError, "allow the handoff origin"):
            VidXPSettings(
                **{
                    **base,
                    "upload_cors_origin_regex": r"^(https://app\.example)$",
                },
                upload_handoff_public_url=(
                    "https://vidxp.example/upload-handoff"
                ),
                upload_handoff_secret="h" * 32,
            )

        settings = VidXPSettings(
            **base,
            upload_handoff_public_url=("https://vidxp.example/upload-handoff/"),
            upload_handoff_secret="h" * 32,
        )
        self.assertEqual(
            settings.upload_handoff_public_url,
            "https://vidxp.example/upload-handoff",
        )
        self.assertEqual(settings.mcp_max_request_body_bytes, 4 * 1024 * 1024)
        self.assertEqual(settings.upload_session_max_files, 10)
        self.assertEqual(
            settings.upload_session_max_bytes,
            100 * 1024 * 1024 * 1024,
        )
        self.assertEqual(settings.upload_session_ttl_seconds, 24 * 60 * 60)

    def test_upload_cors_regex_uses_re2_safe_exact_origins(self):
        base = {
            "upload_public_endpoint": "https://uploads.example/uploads/",
            "upload_internal_endpoint": "http://tusd:8080/uploads/",
            "upload_cleanup_token": "c" * 32,
            "upload_handoff_public_url": (
                "https://vidxp.example/upload-handoff"
            ),
            "upload_handoff_secret": "h" * 32,
        }
        accepted = VidXPSettings(
            **base,
            upload_cors_origin_regex=(
                r"^(https://api\.example|https://vidxp\.example)$"
            ),
        )
        self.assertEqual(
            accepted.upload_cors_origin_regex,
            r"^(https://api\.example|https://vidxp\.example)$",
        )
        loopback = VidXPSettings(
            **{
                **base,
                "upload_handoff_public_url": (
                    "http://127.0.0.1:32191/upload-handoff"
                ),
            },
            upload_cors_origin_regex=r"^(http://127\.0\.0\.1:32191)$",
        )
        self.assertEqual(
            loopback.upload_handoff_public_url,
            "http://127.0.0.1:32191/upload-handoff",
        )

        invalid_patterns = (
            r"^https://api\.example|https://vidxp\.example$",
            r"^(?=https://)https://vidxp\.example$",
            r"^(https://(api|vidxp)\.example)$",
            r"^(https://vidxp.example)$",
            r"^(https://vidxp\.example:65536)$",
            r"^(http://api\.example)$",
        )
        for pattern in invalid_patterns:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(
                ValidationError,
                "CORS origin regex",
            ):
                VidXPSettings(
                    **base,
                    upload_cors_origin_regex=pattern,
                )

    def test_upload_session_limits_are_bounded_and_coherent(self):
        handoff = {
            "upload_public_endpoint": "https://uploads.example/uploads/",
            "upload_internal_endpoint": "http://tusd:8080/uploads/",
            "upload_cleanup_token": "c" * 32,
            "upload_handoff_public_url": (
                "https://vidxp.example/upload-handoff"
            ),
            "upload_handoff_secret": "h" * 32,
            "upload_cors_origin_regex": r"^(https://vidxp\.example)$",
        }
        for maximum_files in (0, 101):
            with self.subTest(maximum_files=maximum_files), self.assertRaises(
                ValidationError
            ):
                VidXPSettings(
                    **handoff,
                    upload_session_max_files=maximum_files,
                )
        for ttl in (299, 7 * 24 * 60 * 60 + 1):
            with self.subTest(ttl=ttl), self.assertRaises(ValidationError):
                VidXPSettings(
                    **handoff,
                    upload_session_ttl_seconds=ttl,
                )
        with self.assertRaisesRegex(ValidationError, "aggregate limit"):
            VidXPSettings(
                **handoff,
                upload_max_bytes=1024,
                upload_session_max_bytes=512,
            )

    def test_compose_oidc_profile_accepts_blank_static_credentials(self):
        settings = VidXPSettings(
            http_auth_mode="oidc",
            http_static_bearer_token="",
            http_oidc_issuer="https://identity.example/",
            http_oidc_audience="vidxp-api",
            http_oidc_jwks_url="https://identity.example/jwks.json",
            http_required_scopes=("vidxp.read",),
            mcp_public_url="https://api.example/mcp",
        )

        self.assertIsNone(settings.http_static_bearer_token)
        settings.validate_http_server()


if __name__ == "__main__":
    unittest.main()
