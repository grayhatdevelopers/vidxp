from __future__ import annotations

import json
import io
import tarfile
import unittest
import zipfile
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.local_answers import (
    DEFAULT_OLLAMA_BASE_URL,
    LocalAnswerConfiguration,
    LocalAnswerError,
    LocalAnswerStatus,
    _extract_archive,
    load_local_answer_configuration,
    local_answer_spec,
    prepare_local_answers,
    save_local_answer_configuration,
)


class LocalAnswerTests(unittest.TestCase):
    def test_configuration_round_trip_uses_the_canonical_model_spec(self):
        spec = local_answer_spec()
        self.assertEqual(spec.model, "qwen3.5:4b-q4_K_M")
        self.assertIn("macos-aarch64", spec.managed_runtime.artifacts)

        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configuration = LocalAnswerConfiguration(
                base_url=DEFAULT_OLLAMA_BASE_URL,
                model=spec.model,
                executable=directory / "ollama",
                model_directory=directory / "models",
            )
            save_local_answer_configuration(
                configuration,
                config_directory=directory,
            )

            self.assertEqual(
                load_local_answer_configuration(directory),
                configuration,
            )

    def test_managed_archive_rejects_parent_traversal(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.zip"
            with zipfile.ZipFile(archive, "w") as opened:
                opened.writestr("../outside", "unsafe")

            with self.assertRaisesRegex(LocalAnswerError, "unsafe path"):
                _extract_archive(archive, root / "runtime", "zip")

            self.assertFalse((root / "outside").exists())

    def test_managed_tar_materializes_an_internal_symbolic_link(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.tgz"
            contents = b"runtime-library"
            with tarfile.open(archive, "w:gz") as opened:
                target = tarfile.TarInfo("lib/ollama/libbackend.1.dylib")
                target.size = len(contents)
                opened.addfile(target, io.BytesIO(contents))
                link = tarfile.TarInfo("lib/ollama/libbackend.dylib")
                link.type = tarfile.SYMTYPE
                link.linkname = "libbackend.1.dylib"
                opened.addfile(link)

            destination = root / "runtime"
            _extract_archive(archive, destination, "tar_gz")

            materialized = destination / "lib/ollama/libbackend.dylib"
            self.assertEqual(materialized.read_bytes(), contents)
            self.assertFalse(materialized.is_symlink())

    def test_managed_tar_rejects_a_link_outside_staging(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.tgz"
            with tarfile.open(archive, "w:gz") as opened:
                link = tarfile.TarInfo("lib/escape")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../outside"
                opened.addfile(link)

            with self.assertRaisesRegex(LocalAnswerError, "unsafe link"):
                _extract_archive(archive, root / "runtime", "tar_gz")

            self.assertFalse((root / "outside").exists())

    def test_prepare_pulls_missing_model_and_saves_runtime_identity(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "ollama"
            executable.touch()
            missing = LocalAnswerStatus(
                ready=False,
                service_ready=True,
                model_ready=False,
                configured=False,
                base_url=DEFAULT_OLLAMA_BASE_URL,
                model="qwen3.5:4b-q4_K_M",
            )
            unavailable = missing.model_copy(update={"service_ready": False})
            ready = missing.model_copy(
                update={"ready": True, "model_ready": True}
            )
            with (
                patch("vidxp.local_answers._platform_error", return_value=None),
                patch("vidxp.local_answers._system_ollama", return_value=executable),
                patch(
                    "vidxp.local_answers._available_service",
                    return_value=nullcontext(),
                ),
                patch(
                    "vidxp.local_answers.inspect_local_answers",
                    side_effect=(unavailable, missing, ready),
                ),
                patch("vidxp.local_answers._pull_model") as pull,
                patch("vidxp.local_answers.save_local_answer_configuration") as save,
            ):
                configuration = prepare_local_answers(data_directory=root)

            pull.assert_called_once()
            save.assert_called_once_with(configuration)
            self.assertEqual(configuration.executable, executable.resolve())
            self.assertEqual(configuration.model_directory, root / "models" / "ollama")

    def test_prepare_reuses_a_running_service_without_installing_a_runtime(self):
        missing = LocalAnswerStatus(
            ready=False,
            service_ready=True,
            model_ready=False,
            configured=False,
            base_url=DEFAULT_OLLAMA_BASE_URL,
            model="qwen3.5:4b-q4_K_M",
        )
        ready = missing.model_copy(update={"ready": True, "model_ready": True})
        with (
            patch("vidxp.local_answers._platform_error", return_value=None),
            patch(
                "vidxp.local_answers.inspect_local_answers",
                side_effect=(missing, missing, ready),
            ),
            patch("vidxp.local_answers.install_managed_ollama") as install,
            patch("vidxp.local_answers._pull_model") as pull,
            patch("vidxp.local_answers.save_local_answer_configuration"),
        ):
            configuration = prepare_local_answers()

        install.assert_not_called()
        pull.assert_called_once()
        self.assertIsNone(configuration.executable)
        self.assertIsNone(configuration.model_directory)

    def test_cli_prepares_local_answers_without_opening_a_repository(self):
        configuration = LocalAnswerConfiguration(
            base_url=DEFAULT_OLLAMA_BASE_URL,
            model="qwen3.5:4b-q4_K_M",
            executable=Path("/runtime/ollama"),
            model_directory=Path("/models/ollama"),
        )
        current = LocalAnswerStatus(
            ready=True,
            service_ready=True,
            model_ready=True,
            configured=False,
            base_url=configuration.base_url,
            model=configuration.model,
        )
        with (
            patch(
                "vidxp.cli_commands.local_answers.inspect_local_answers",
                return_value=current,
            ),
            patch(
                "vidxp.cli_commands.local_answers.prepare_local_answers",
                return_value=configuration,
            ) as prepare,
            patch.object(cli, "create_local_application") as compose,
        ):
            result = CliRunner().invoke(
                cli.app,
                ["local-answers", "prepare", "--yes", "--json"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.output)["ready"])
        prepare.assert_called_once()
        compose.assert_not_called()

    def test_cli_collapses_repeated_local_answer_progress_updates(self):
        configuration = LocalAnswerConfiguration(
            base_url=DEFAULT_OLLAMA_BASE_URL,
            model="qwen3.5:4b-q4_K_M",
            executable=Path("/runtime/ollama"),
            model_directory=Path("/models/ollama"),
        )
        current = LocalAnswerStatus(
            ready=True,
            service_ready=True,
            model_ready=True,
            configured=False,
            base_url=configuration.base_url,
            model=configuration.model,
        )

        def prepare_with_progress(*_args, progress, **_kwargs):
            progress(
                {
                    "stage": "downloading_runtime",
                    "message": "Downloading managed runtime.",
                    "current": 1,
                    "total": 2,
                }
            )
            progress(
                {
                    "stage": "downloading_runtime",
                    "message": "Downloading managed runtime.",
                    "current": 2,
                    "total": 2,
                }
            )
            return configuration

        with (
            patch(
                "vidxp.cli_commands.local_answers.inspect_local_answers",
                return_value=current,
            ),
            patch(
                "vidxp.cli_commands.local_answers.prepare_local_answers",
                side_effect=prepare_with_progress,
            ),
            patch.object(cli, "create_local_application"),
        ):
            result = CliRunner().invoke(
                cli.app,
                ["local-answers", "prepare", "--yes"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.count("Downloading managed runtime."), 1)


if __name__ == "__main__":
    unittest.main()
