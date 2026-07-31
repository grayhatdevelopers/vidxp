from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vidxp.media_runtime import (
    MediaRuntimeStatus,
    inspect_media_runtime,
    load_media_runtime_configuration,
    media_runtime_is_initialized,
    save_media_runtime_configuration,
    system_install_plan,
)


class MediaRuntimeTests(unittest.TestCase):
    def test_inspection_verifies_both_executables_and_required_encoders(self):
        ffmpeg = Path("resolved/ffmpeg").resolve()
        ffprobe = Path("resolved/ffprobe").resolve()

        def resolve(value):
            return ffmpeg if str(value) == "ffmpeg" else ffprobe

        def output(arguments, **_kwargs):
            if "-encoders" in arguments:
                return " V....D libx264\n A....D aac\n"
            return "version"

        with TemporaryDirectory() as temporary_directory:
            with (
                patch("vidxp.media_runtime._resolve_executable", side_effect=resolve),
                patch("vidxp.media_runtime._command_output", side_effect=output),
                patch("vidxp.media_runtime.system_install_plan"),
            ):
                status = inspect_media_runtime(
                    config_directory=Path(temporary_directory)
                )

        self.assertTrue(status.ready)
        self.assertEqual(status.ffmpeg_executable, ffmpeg)
        self.assertEqual(status.ffprobe_executable, ffprobe)
        self.assertEqual(status.errors, ())

    def test_inspection_rejects_an_ffmpeg_build_without_required_codecs(self):
        executable = Path("resolved/tool").resolve()

        def output(arguments, **_kwargs):
            return " V....D libx264\n" if "-encoders" in arguments else "version"

        with (
            patch(
                "vidxp.media_runtime._resolve_executable",
                return_value=executable,
            ),
            patch("vidxp.media_runtime._command_output", side_effect=output),
            patch("vidxp.media_runtime.system_install_plan", return_value=None),
        ):
            status = inspect_media_runtime()

        self.assertFalse(status.ready)
        self.assertIn("aac", " ".join(status.errors))

    def test_verified_absolute_paths_are_persisted_and_reloaded(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            ffmpeg.touch()
            ffprobe.touch()
            status = MediaRuntimeStatus(
                ready=True,
                initialized=False,
                ffmpeg_executable=ffmpeg.resolve(),
                ffprobe_executable=ffprobe.resolve(),
            )

            saved = save_media_runtime_configuration(
                status,
                config_directory=root / "config",
            )
            loaded = load_media_runtime_configuration(root / "config")

        self.assertEqual(loaded, saved)
        self.assertTrue(saved.ffmpeg_executable.is_absolute())
        self.assertTrue(saved.ffprobe_executable.is_absolute())

    def test_initialized_accepts_saved_config_environment_or_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg = root / "ffmpeg"
            ffprobe = root / "ffprobe"
            ffmpeg.touch()
            ffprobe.touch()
            save_media_runtime_configuration(
                MediaRuntimeStatus(
                    ready=True,
                    initialized=False,
                    ffmpeg_executable=ffmpeg.resolve(),
                    ffprobe_executable=ffprobe.resolve(),
                ),
                config_directory=root,
            )
            self.assertTrue(media_runtime_is_initialized(root))

        with patch.dict(
            os.environ,
            {
                "VIDXP_FFMPEG_EXECUTABLE": "custom-ffmpeg",
                "VIDXP_FFPROBE_EXECUTABLE": "custom-ffprobe",
            },
            clear=False,
        ):
            self.assertTrue(media_runtime_is_initialized(Path("missing")))

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "vidxp.media_runtime.load_media_runtime_configuration",
                return_value=None,
            ),
            patch("vidxp.media_runtime.shutil.which", return_value=None),
        ):
            self.assertFalse(media_runtime_is_initialized())

    @unittest.skipUnless(sys.platform == "win32", "Windows package contract")
    def test_windows_plan_uses_the_approved_exact_winget_package(self):
        with patch("vidxp.media_runtime.shutil.which", return_value="winget"):
            plan = system_install_plan()

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.manager, "Windows Package Manager")
        self.assertTrue(plan.automatic)
        self.assertIn("Gyan.FFmpeg", plan.command)
        self.assertIn("--exact", plan.command)
        self.assertIn("--source", plan.command)


if __name__ == "__main__":
    unittest.main()
