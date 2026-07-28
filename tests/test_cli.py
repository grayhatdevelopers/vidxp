import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.composition import LocalApplicationContext, settings_for_repository
from vidxp.application_models import (
    CreateIndexCommand,
    DependencyCheckResult,
    IndexJobResult,
    IndexResult,
    IndexStatus,
    Job,
    JobKind,
    JobQueue,
    JobState,
    MediaAsset,
    PrepareModelsResult,
    PrepareModelsJobResult,
    RemoveIndexCommand,
    SearchCommand,
)
from vidxp.core.media import MediaState, MediaStream
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capabilities.schemas import SearchResult
from vidxp.repositories import RepositoryConfig, RepositoryRegistry


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
JOB_ID = "423456781234423481234567890abcde"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.service = Mock()
        self.service.registry = create_capability_registry()
        self.service.index_directory = Path("repo/indexes")
        self.service.layout.root = Path("repo")
        self.service.runtime.backends.requested = "cpu"
        self.jobs = Mock()
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
                jobs=self.jobs,
                repositories=self.registry,
                repository=self.repository,
            ),
        ):
            return self.runner.invoke(cli.app, arguments)

    def test_grouped_commands_are_exposed(self):
        result = self.invoke(["--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        for command in (
            "media",
            "index",
            "jobs",
            "search",
            "actors",
            "artifacts",
            "doctor",
            "prepare",
        ):
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
        result_value = IndexResult(
            media_id=MEDIA_ID,
            generation_id=GENERATION_ID,
            snapshot_id=SNAPSHOT_ID,
            active_media_count=1,
            record_counts={"scene": 1},
        )
        self.jobs.submit_index.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        self.jobs.wait.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.succeeded,
            queue=JobQueue.cpu,
            result=IndexJobResult(result=result_value),
        )

        result = self.invoke(
            [
                "--format",
                "json",
                "index",
                "create",
                MEDIA_ID,
                "--modality",
                "scene",
                "--frame-stride",
                "5",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        command = self.jobs.submit_index.call_args.args[0]
        self.assertIsInstance(command, CreateIndexCommand)
        self.assertEqual(command.modalities, ("scene",))
        self.assertEqual(command.frame_stride, 5)
        self.assertEqual(command.media_id, MEDIA_ID)

    def test_remove_uses_shared_media_id_command(self):
        self.service.remove_from_index.return_value = True
        removed = self.invoke(["index", "remove", MEDIA_ID, "--json"])
        self.assertEqual(removed.exit_code, 0, removed.output)
        self.service.remove_from_index.assert_called_once_with(
            RemoveIndexCommand(media_id=MEDIA_ID)
        )

    def test_media_import_uses_the_local_import_command(self):
        self.service.import_media.return_value = MediaAsset(
            schema_version=1,
            media_id=MEDIA_ID,
            video_id=MEDIA_ID,
            original_filename="video.mp4",
            sha256="1" * 64,
            byte_size=5,
            detected_mime_type="video/mp4",
            container="mp4",
            duration_seconds=1,
            streams=(
                MediaStream(
                    index=0,
                    kind="video",
                    codec="h264",
                    width=1,
                    height=1,
                ),
            ),
            state=MediaState.ready,
            created_at=datetime.now(timezone.utc),
        )
        with TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            result = self.invoke(["media", "import", str(video), "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["media_id"], MEDIA_ID)
        self.assertEqual(
            self.service.import_media.call_args.args[0].path,
            video.resolve(),
        )

    def test_status_serializes_shared_model(self):
        self.service.index_status.return_value = IndexStatus(
            schema_version=1,
            state="missing",
            stage="status",
            message="No index.",
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
        prepared_value = PrepareModelsResult(
            prepared=("scene-model",),
            modalities=("scene",),
            runtime={
                "requested": "cpu",
                "torch_device": "cpu",
                "transcription_device": "cpu",
            },
        )
        self.jobs.submit_prepare_models.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.prepare_models,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        self.jobs.wait.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.prepare_models,
            state=JobState.succeeded,
            queue=JobQueue.cpu,
            result=PrepareModelsJobResult(result=prepared_value),
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
            self.jobs.submit_prepare_models.call_args.args[0].modalities,
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
