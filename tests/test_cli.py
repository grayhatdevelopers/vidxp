import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.entrypoint import startup_command
from vidxp.composition import LocalApplicationContext, settings_for_repository
from vidxp.settings import ApplicationMode
from vidxp.application_models import (
    CapabilityDependencyCheck,
    CreateIndexCommand,
    DependencyCheckResult,
    DependencyKind,
    FusedSearchResult,
    FusionProvenance,
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
    QueryAnswer,
    QueryAnswerMode,
    QueryJobResult,
    QueryPlan,
    QueryVideoCommand,
    RemoveIndexCommand,
    SearchCommand,
    SearchJobResult,
    SearchMomentsPlanStep,
)
from vidxp.core.media import MediaState, MediaStream
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capability_service import CapabilityService
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
        self.service.list_capabilities.return_value = CapabilityService(
            self.service.registry
        ).list()
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

    def test_startup_notice_targets_long_interactive_commands(self):
        self.assertEqual(startup_command(["doctor"]), "doctor")
        self.assertEqual(
            startup_command(["media", "import", "video.mp4"]),
            "media import",
        )
        self.assertIsNone(startup_command(["doctor", "--json"]))
        self.assertIsNone(startup_command(["--quiet", "prepare"]))
        self.assertIsNone(startup_command(["repositories", "list"]))

    def test_snippet_rejects_an_inverted_time_range_before_submission(self):
        result = self.invoke(
            ["artifacts", "snippet", MEDIA_ID, "3", "2"]
        )

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("snippet end must be greater than its", result.output)
        self.assertIn("start.", result.output)
        self.jobs.submit_snippet.assert_not_called()

    def test_search_constructs_shared_command(self):
        search_result = FusedSearchResult(
            query_id="fused:1",
            query="yellow taxi",
            modalities=("scene",),
            fusion=FusionProvenance(
                requested_modalities=("scene",),
                searched_modalities=("scene",),
            ),
        )
        self.jobs.submit_search.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.search,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        self.jobs.wait.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.search,
            state=JobState.succeeded,
            queue=JobQueue.cpu,
            result=SearchJobResult(result=search_result),
        )

        result = self.invoke(
            ["search", "scene", "yellow taxi", "--top-k", "7", "--json"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.jobs.submit_search.assert_called_once_with(
            SearchCommand(
                modalities=("scene",),
                query="yellow taxi",
                top_k=7,
            )
        )
        self.assertEqual(json.loads(result.output)["query"], "yellow taxi")

    def test_query_constructs_shared_command_and_emits_typed_answer(self):
        answer = QueryAnswer(
            question="What happens?",
            mode=QueryAnswerMode.no_evidence,
            plan=QueryPlan(
                steps=(
                    SearchMomentsPlanStep(
                        modality="scene",
                        query="What happens?",
                    ),
                )
            ),
            fusion=FusionProvenance(
                requested_modalities=("scene",),
                searched_modalities=("scene",),
            ),
            fallback_reason="no_evidence",
        )
        self.jobs.submit_query.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.query,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        self.jobs.wait.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.query,
            state=JobState.succeeded,
            queue=JobQueue.cpu,
            result=QueryJobResult(result=answer),
        )

        result = self.invoke(
            [
                "query",
                "What happens?",
                "--media-id",
                MEDIA_ID,
                "--modality",
                "scene",
                "--json",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.jobs.submit_query.assert_called_once_with(
            QueryVideoCommand(
                question="What happens?",
                media_id=MEDIA_ID,
                modalities=("scene",),
            )
        )
        self.assertEqual(json.loads(result.output)["mode"], "no_evidence")

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
                "--scene-sample-fps",
                "2",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        command = self.jobs.submit_index.call_args.args[0]
        self.assertIsInstance(command, CreateIndexCommand)
        self.assertEqual(command.modalities, ("scene",))
        self.assertEqual(command.frame_stride, 5)
        self.assertEqual(command.scene_sample_fps, 2.0)
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

    def test_doctor_accepts_repeated_modality_options(self):
        self.service.check_dependencies.return_value = DependencyCheckResult(
            ok=True,
            modalities=("dialogue", "scene"),
            checks=(),
        )
        result = self.invoke(
            [
                "doctor",
                "--modalities",
                "dialogue",
                "--modalities",
                "scene",
                "--json",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        command = self.service.check_dependencies.call_args.args[0]
        self.assertEqual(command.modalities, ("dialogue", "scene"))

    def test_prepare_announces_start_and_subscribes_to_job_progress(self):
        prepared = PrepareModelsResult(
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
            result=PrepareModelsJobResult(result=prepared),
        )

        result = self.invoke(["prepare", "--modalities", "scene"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(
            result.output,
            r"\[\d{2}:\d{2}:\d{2}\] Starting model preparation for scene\.",
        )
        self.assertTrue(callable(self.jobs.wait.call_args.kwargs["progress"]))

    def test_doctor_streams_timestamped_runtime_check_progress(self):
        def check_dependencies(
            _command,
            *,
            on_check_start,
            on_check_complete,
        ):
            on_check_start(
                "scene",
                DependencyKind.distribution,
                "torch",
            )
            on_check_complete(
                CapabilityDependencyCheck(
                    capability="scene",
                    kind=DependencyKind.distribution,
                    name="torch",
                    requirement="torch==2.13.0",
                    installed_version="2.13.0",
                    ok=True,
                ),
                0.01,
            )
            on_check_start(
                "scene",
                DependencyKind.runtime,
                "Torch import",
            )
            on_check_complete(
                CapabilityDependencyCheck(
                    capability="scene",
                    kind=DependencyKind.runtime,
                    name="Torch import",
                    ok=True,
                ),
                1.25,
            )
            return DependencyCheckResult(
                ok=True,
                modalities=("scene",),
                checks=(
                    CapabilityDependencyCheck(
                        capability="scene",
                        kind=DependencyKind.distribution,
                        name="torch",
                        requirement="torch==2.13.0",
                        installed_version="2.13.0",
                        ok=True,
                    ),
                    CapabilityDependencyCheck(
                        capability="scene",
                        kind=DependencyKind.runtime,
                        name="Torch import",
                        ok=True,
                    ),
                ),
            )

        self.service.check_dependencies.side_effect = check_dependencies

        result = self.invoke(["doctor", "--modalities", "scene"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(
            result.output,
            r"\[\d{2}:\d{2}:\d{2}\] Checking \[scene\] Torch import\.\.\.",
        )
        self.assertIn("OK (1.2s)", result.output)
        self.assertIn("OK (version 2.13.0, 0.0s)", result.output)
        self.assertEqual(result.output.count("package torch"), 1)

    def test_doctor_prints_install_remedy_for_python_failures(self):
        self.service.check_dependencies.return_value = DependencyCheckResult(
            ok=False,
            modalities=("scene",),
            checks=(
                CapabilityDependencyCheck(
                    capability="scene",
                    kind=DependencyKind.distribution,
                    name="transformers",
                    requirement="transformers>=5,<6",
                    ok=False,
                    error="distribution is not installed",
                ),
            ),
        )

        result = self.invoke(["doctor", "--modalities", "scene"])

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(
            "include --extra local-worker",
            result.output,
        )
        self.assertIn(
            'pip install "vidxp[scene]"',
            result.output,
        )

    def test_invalid_capability_is_a_cli_parameter_error(self):
        result = self.invoke(
            ["doctor", "--modalities", "unknown", "--json"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Unknown or unsupported capabilities", result.output)

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

    def test_local_repository_ignores_server_mode_environment(self):
        repository = RepositoryConfig(
            "default",
            Path("repo"),
            device=None,
            configured=False,
        )
        with patch.dict(os.environ, {"VIDXP_MODE": "server"}):
            settings = settings_for_repository(repository)

        self.assertEqual(settings.mode, ApplicationMode.local)


if __name__ == "__main__":
    unittest.main()
