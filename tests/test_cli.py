import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.composition import LocalApplicationContext, settings_for_repository
from vidxp.application_models import (
    CreateIndexCommand,
    DependencyCheckResult,
    IndexResult,
    IndexStatus,
    PrepareModelsResult,
    SearchCommand,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capabilities.schemas import SearchResult
from vidxp.repositories import RepositoryConfig, RepositoryRegistry


class CliTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.service = Mock()
        self.service.registry = create_capability_registry()
        self.service.index_directory = Path("repo/indexes/current")
        self.service.layout.root = Path("repo")
        self.service.runtime.backends.requested = "cpu"
        self.registry = Mock(spec=RepositoryRegistry)
        self.registry.path = Path("repositories.json")
        self.repository = RepositoryConfig(
            "default",
            Path("repo"),
            device="cpu",
            configured=False,
        )

    def invoke(self, arguments):
        with patch.object(
            cli,
            "create_local_application",
            return_value=LocalApplicationContext(
                application=self.service,
                repositories=self.registry,
                repository=self.repository,
            ),
        ):
            return self.runner.invoke(cli.app, arguments)

    def test_grouped_commands_are_exposed(self):
        result = self.invoke(["--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        for command in ("index", "search", "actors", "doctor", "prepare"):
            self.assertIn(command, result.output)

    def test_search_constructs_shared_command(self):
        self.service.search.return_value = SearchResult(
            query_id="scene:1",
            query="yellow taxi",
            modality="scene",
        )

        result = self.invoke(
            ["search", "scene", "yellow taxi", "--top-k", "7", "--json"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.service.search.assert_called_once_with(
            SearchCommand(
                modality="scene",
                query="yellow taxi",
                top_k=7,
            )
        )
        self.assertEqual(json.loads(result.output)["query"], "yellow taxi")

    def test_index_constructs_shared_command(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            self.service.create_index.return_value = IndexResult(
                summary={"scene_frames": 1}
            )

            result = self.invoke(
                [
                    "--format",
                    "json",
                    "index",
                    "create",
                    str(video),
                    "--modality",
                    "scene",
                    "--frame-stride",
                    "5",
                ]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        command = self.service.create_index.call_args.args[0]
        self.assertIsInstance(command, CreateIndexCommand)
        self.assertEqual(command.modalities, ("scene",))
        self.assertEqual(command.frame_stride, 5)

    def test_status_serializes_shared_model(self):
        self.service.index_status.return_value = IndexStatus(
            schema_version=1,
            state="missing",
            stage="status",
            message="No index.",
            repository_root=Path("repo"),
            index_directory=Path("repo/indexes/current"),
        )

        result = self.invoke(["index", "status", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["state"], "missing")

    def test_doctor_and_prepare_use_shared_models(self):
        self.service.check_dependencies.return_value = DependencyCheckResult(
            ok=True,
            modalities=("scene",),
            checks=(),
        )
        self.service.prepare_models.return_value = PrepareModelsResult(
            prepared=("scene-model",),
            modalities=("scene",),
            runtime={"torch_device": "cpu"},
        )

        checked = self.invoke(
            ["doctor", "--modalities", "scene", "--json"]
        )
        prepared = self.invoke(
            ["prepare", "--modalities", "scene", "--json"]
        )

        self.assertEqual(checked.exit_code, 0, checked.output)
        self.assertEqual(prepared.exit_code, 0, prepared.output)
        self.assertEqual(
            self.service.check_dependencies.call_args.args[0].modalities,
            ("scene",),
        )
        self.assertEqual(
            self.service.prepare_models.call_args.args[0].modalities,
            ("scene",),
        )

    def test_invalid_capability_is_a_cli_parameter_error(self):
        result = self.invoke(
            ["doctor", "--modalities", "unknown", "--json"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Unknown capability", result.output)

    def test_repository_without_device_preserves_runtime_environment(self):
        repository = RepositoryConfig(
            "default",
            Path("repo"),
            device=None,
            configured=False,
        )
        with patch.dict(
            os.environ,
            {"VIDXP_RUNTIME_BACKEND": "cpu"},
        ):
            settings = settings_for_repository(repository)

        self.assertEqual(settings.runtime_backend, "cpu")


if __name__ == "__main__":
    unittest.main()
