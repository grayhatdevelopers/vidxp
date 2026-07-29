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


if __name__ == "__main__":
    unittest.main()
