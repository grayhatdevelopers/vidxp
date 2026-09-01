import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from click import unstyle
from typer.testing import CliRunner

from vidxp import cli
from vidxp.entrypoint import startup_command
from vidxp.composition import LocalApplicationContext, settings_for_repository
from vidxp.media_runtime import (
    MediaRuntimeConfiguration,
    MediaRuntimeStatus,
    SystemInstallPlan,
)
from vidxp.settings import ApplicationMode
from vidxp.application_models import (
    ApplicationError,
    Artifact,
    ArtifactJobResult,
    CapabilityDependencyCheck,
    CreateIndexCommand,
    DependencyCheckResult,
    DependencyKind,
    ErrorCategory,
    ErrorDetail,
    FusedSearchResult,
    FusionProvenance,
    IndexJobResult,
    IndexResult,
    IndexStatus,
    IndexStatusSummary,
    Job,
    JobKind,
    JobProgress,
    JobQueue,
    JobState,
    MediaAsset,
    MediaPage,
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
from vidxp.core.artifacts import ArtifactKind, ArtifactState
from vidxp.core.media import MediaState, MediaStream
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capability_service import CapabilityService
from vidxp.repositories import RepositoryConfig, RepositoryRegistry
from vidxp.ports import LocalFileResource


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
JOB_ID = "423456781234423481234567890abcde"
ARTIFACT_ID = "523456781234423481234567890abcde"


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
        self.service.model_cache = Path("model-cache")
        self.service.runtime.backends.requested = "cpu"
        self.service.model_readiness.return_value = DependencyCheckResult(
            ok=True,
            modalities=(),
            checks=(),
        )
        self.jobs = Mock()
        self.registry = Mock(spec=RepositoryRegistry)
        self.registry.path = Path("repositories.json")
        self.repository = RepositoryConfig(
            "default",
            Path("repo"),
            device="cpu",
            configured=False,
        )

    def invoke(self, arguments, *, media_runtime_initialized=True):
        with (
            patch.object(
                cli,
                "create_local_application",
                return_value=LocalApplicationContext(
                    application=self.service,
                    jobs=self.jobs,
                    repositories=self.registry,
                    repository=self.repository,
                ),
            ) as create_local_application,
            patch(
                "vidxp.cli_support.media_runtime_is_initialized",
                return_value=media_runtime_initialized,
            ),
        ):
            result = self.runner.invoke(cli.app, arguments)
        self.create_local_application = create_local_application
        return result

    def test_data_directory_is_forwarded_to_local_composition(self):
        result = self.invoke(
            [
                "--data-dir",
                "custom-data",
                "repositories",
                "show",
                "--json",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.create_local_application.call_args.kwargs["data_directory"],
            Path("custom-data"),
        )

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
            "desktop-probe",
            "init",
            "doctor",
            "prepare",
            "mcp-config",
        ):
            self.assertIn(command, result.output)

    def test_failed_model_preparation_job_is_structured_in_cli_json(self):
        self.jobs.get.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.prepare_models,
            state=JobState.failed,
            queue=JobQueue.cpu,
            error=ErrorDetail(
                code="model_download_failed",
                category=ErrorCategory.unavailable,
                message="The model download failed after three attempts.",
                details={
                    "model": "publisher/model",
                    "partial_files_preserved": True,
                    "remediation": "vidxp prepare --modalities speech",
                },
                retryable=True,
            ),
        )

        result = self.invoke(["jobs", "show", JOB_ID])

        self.assertEqual(result.exit_code, 0, result.output)
        error = json.loads(result.output)["error"]
        self.assertEqual(error["code"], "model_download_failed")
        self.assertTrue(error["retryable"])
        self.assertTrue(error["details"]["partial_files_preserved"])

    def test_mcp_config_is_copy_paste_json_without_opening_repository(self):
        result = self.invoke(["mcp-config"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.create_local_application.assert_not_called()
        config = json.loads(result.output)
        server = config["mcpServers"]["vidxp"]
        self.assertIn(
            Path(server["command"]).name.lower(),
            {"vidxp-mcp", "vidxp-mcp.exe"},
        )
        self.assertEqual(server["args"], ["--repository", "default"])

    def test_startup_notice_targets_long_interactive_commands(self):
        self.assertEqual(startup_command(["init"]), "init")
        self.assertEqual(startup_command(["doctor"]), "doctor")
        self.assertEqual(
            startup_command(["media", "import", "video.mp4"]),
            "media import",
        )
        self.assertEqual(
            startup_command(["--data-dir", "custom-data", "ui"]),
            "ui",
        )
        self.assertIsNone(startup_command(["doctor", "--json"]))
        self.assertIsNone(startup_command(["--quiet", "prepare"]))
        self.assertIsNone(startup_command(["repositories", "list"]))

    def test_init_saves_verified_paths_without_opening_a_repository(self):
        ffmpeg = Path("tools/ffmpeg.exe").resolve()
        ffprobe = Path("tools/ffprobe.exe").resolve()
        status = MediaRuntimeStatus(
            ready=True,
            initialized=False,
            ffmpeg_executable=ffmpeg,
            ffprobe_executable=ffprobe,
        )
        configuration = MediaRuntimeConfiguration(
            ffmpeg_executable=ffmpeg,
            ffprobe_executable=ffprobe,
        )
        with (
            patch(
                "vidxp.cli_commands.runtime.inspect_media_runtime",
                return_value=status,
            ),
            patch(
                "vidxp.cli_commands.runtime.save_media_runtime_configuration",
                return_value=configuration,
            ) as save,
            patch(
                "vidxp.cli_commands.runtime.media_runtime_config_path",
                return_value=Path("config/media-runtime.json").resolve(),
            ),
        ):
            result = self.invoke(["init", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["initialized"])
        save.assert_called_once_with(status)
        self.create_local_application.assert_not_called()

    def test_noninteractive_init_reports_command_without_installing(self):
        plan = SystemInstallPlan(
            manager="Windows Package Manager",
            command=(
                "winget",
                "install",
                "--id",
                "Gyan.FFmpeg",
                "--exact",
            ),
            automatic=True,
        )
        status = MediaRuntimeStatus(
            ready=False,
            initialized=False,
            errors=("FFmpeg was not found.",),
            install_plan=plan,
        )
        with (
            patch(
                "vidxp.cli_commands.runtime.inspect_media_runtime",
                return_value=status,
            ),
            patch(
                "vidxp.cli_commands.runtime.install_media_runtime",
            ) as install,
        ):
            result = self.invoke(["init", "--json"])

        self.assertEqual(result.exit_code, 1, result.output)
        payload = json.loads(result.output)
        self.assertFalse(payload["ready"])
        self.assertIn("Gyan.FFmpeg", payload["install_command"])
        install.assert_not_called()
        self.create_local_application.assert_not_called()

    def test_yes_explicitly_runs_the_displayed_installer_and_verifies(self):
        plan = SystemInstallPlan(
            manager="Windows Package Manager",
            command=("winget", "install", "--id", "Gyan.FFmpeg"),
            automatic=True,
        )
        missing = MediaRuntimeStatus(
            ready=False,
            initialized=False,
            errors=("FFmpeg was not found.",),
            install_plan=plan,
        )
        ready = MediaRuntimeStatus(
            ready=True,
            initialized=False,
            ffmpeg_executable=Path("tools/ffmpeg.exe").resolve(),
            ffprobe_executable=Path("tools/ffprobe.exe").resolve(),
        )
        configuration = MediaRuntimeConfiguration(
            ffmpeg_executable=ready.ffmpeg_executable,
            ffprobe_executable=ready.ffprobe_executable,
        )
        with (
            patch(
                "vidxp.cli_commands.runtime.inspect_media_runtime",
                side_effect=(missing, ready),
            ),
            patch(
                "vidxp.cli_commands.runtime.install_media_runtime",
            ) as install,
            patch(
                "vidxp.cli_commands.runtime.save_media_runtime_configuration",
                return_value=configuration,
            ),
        ):
            result = self.invoke(["init", "--yes", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.output)["ready"])
        install.assert_called_once_with(plan, output_to_stderr=True)
        self.create_local_application.assert_not_called()

    def test_first_media_command_points_to_init_when_uninitialized(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            result = self.invoke(
                ["media", "import", str(video)],
                media_runtime_initialized=False,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.exception.code, "media_runtime_uninitialized")
        self.assertIn("vidxp init", str(result.exception))
        self.service.import_media.assert_not_called()

    def test_ui_shutdown_stops_its_local_worker(self):
        with patch(
            "vidxp.frontend.main",
            side_effect=SystemExit(0),
        ) as frontend:
            result = self.invoke(["ui"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "--server.address=127.0.0.1",
            frontend.call_args.args[0],
        )
        self.assertIn(
            "--server.showEmailPrompt=false",
            frontend.call_args.args[0],
        )
        self.assertIn(
            "--browser.gatherUsageStats=false",
            frontend.call_args.args[0],
        )
        self.jobs.stop_worker.assert_called_once_with()

    def test_worker_lifecycle_commands_use_the_existing_job_service(self):
        started = self.invoke(["jobs", "start-worker"])

        self.assertEqual(started.exit_code, 0, started.output)
        self.assertTrue(json.loads(started.output)["running"])
        self.jobs.start.assert_called_once_with()
        self.jobs.readiness.assert_called_once_with()

        self.jobs.reset_mock()
        status = self.invoke(["jobs", "worker-status"])
        self.assertEqual(status.exit_code, 0, status.output)
        self.assertTrue(json.loads(status.output)["running"])
        self.jobs.readiness.assert_called_once_with()

        self.jobs.reset_mock()
        self.jobs.readiness.side_effect = ApplicationError(
            "job_backend_unavailable",
            ErrorCategory.unavailable,
            "The worker is not running.",
        )
        stopped = self.invoke(["jobs", "worker-status"])
        self.assertEqual(stopped.exit_code, 0, stopped.output)
        self.assertFalse(json.loads(stopped.output)["running"])

        self.jobs.reset_mock()
        self.jobs.stop_worker.return_value = True
        stopped = self.invoke(["jobs", "stop-worker"])
        self.assertEqual(stopped.exit_code, 0, stopped.output)
        self.assertEqual(
            json.loads(stopped.output),
            {
                "running": False,
                "stopped": True,
                "detail": "Local video processing is stopped.",
            },
        )

    def test_media_list_shows_media_state(self):
        self.service.list_media.return_value = MediaPage(
        items=(
            MediaAsset(
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
            ),
        ),
        next_cursor=None,
        total =1,
    )

        result = self.invoke(["media", "list"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("State", result.output)
        self.assertIn("ready", result.output)

    def test_media_list_shows_pending_and_failed_states(self):
        failed_id = "223456781234423481234567890abcde"
        self.service.list_media.return_value = MediaPage(
            items=(
                MediaAsset(
                    schema_version=1,
                    media_id=MEDIA_ID,
                    video_id=MEDIA_ID,
                    original_filename="pending.mp4",
                    sha256="1" * 64,
                    byte_size=5,
                    state=MediaState.pending,
                    created_at=datetime.now(timezone.utc),
                ),
                MediaAsset(
                    schema_version=1,
                    media_id=failed_id,
                    video_id=failed_id,
                    original_filename="failed.mp4",
                    sha256="2" * 64,
                    byte_size=7,
                    state=MediaState.failed,
                    created_at=datetime.now(timezone.utc),
                ),
            ),
            next_cursor=None,
            total=2,
        )

        result = self.invoke(["media", "list"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("pending", result.output)
        self.assertIn("failed", result.output)
        self.assertIn("-", result.output)

    def test_media_list_passes_filters_to_service(self):
        self.service.list_media.return_value = MediaPage(items=(), total=0)

        result = self.invoke(
            [
                "media",
                "list",
                "--filename",
                "clip.mp4",
                "--state",
                "ready",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        command = self.service.list_media.call_args.args[0]
        self.assertEqual(command.filename, "clip.mp4")
        self.assertEqual(command.state, MediaState.ready)

    def test_ui_share_uses_streamlit_wildcard_bind_and_warns(self):
        with (
            patch(
                "vidxp.frontend.main",
                side_effect=SystemExit(0),
            ) as frontend,
            patch(
                "vidxp.network_share.primary_lan_address",
                return_value="192.168.100.131",
            ),
        ):
            result = self.invoke(["ui", "--share"])

        self.assertEqual(result.exit_code, 0, result.output)
        arguments = frontend.call_args.args[0]
        self.assertIn("--server.address=0.0.0.0", arguments)
        self.assertIn("--server.showEmailPrompt=false", arguments)
        self.assertIn("--browser.gatherUsageStats=false", arguments)
        self.assertIn("has no authentication", result.output)
        self.assertIn("Browser UI: http://192.168.100.131:8501", result.output)

    def test_snippet_rejects_an_inverted_time_range_before_submission(self):
        result = self.invoke(
            ["artifacts", "snippet", MEDIA_ID, "3", "2"]
        )

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("snippet end must be greater than its", result.output)
        self.assertIn("start.", result.output)
        self.jobs.submit_snippet.assert_not_called()

    def test_snippet_completion_points_to_the_download_command(self):
        artifact = Artifact(
            artifact_id=ARTIFACT_ID,
            media_id=MEDIA_ID,
            kind=ArtifactKind.snippet,
            profile="compatible_mp4",
            mime_type="video/mp4",
            byte_size=12,
            sha256="1" * 64,
            state=ArtifactState.ready,
            created_at=datetime.now(timezone.utc),
        )
        self.jobs.submit_snippet.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.snippet,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        self.jobs.wait.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.snippet,
            state=JobState.succeeded,
            queue=JobQueue.cpu,
            result=ArtifactJobResult(
                kind=JobKind.snippet,
                result=artifact,
            ),
        )

        result = self.invoke(
            ["artifacts", "snippet", MEDIA_ID, "9", "17"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"Clip ready: {ARTIFACT_ID}", result.output)
        self.assertIn(
            f"vidxp artifacts download {ARTIFACT_ID}",
            result.output,
        )

    def test_artifact_download_copies_authorized_content(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "managed.mp4"
            source.write_bytes(b"clip-content")
            destination = root / "exported.mp4"
            self.service.open_artifact_content.return_value = (
                LocalFileResource(
                    path=source,
                    filename=f"snippet-{ARTIFACT_ID}.mp4",
                    mime_type="video/mp4",
                    byte_size=12,
                    etag="1" * 64,
                )
            )

            result = self.invoke(
                [
                    "artifacts",
                    "download",
                    ARTIFACT_ID,
                    str(destination),
                    "--json",
                ]
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(destination.read_bytes(), b"clip-content")
            payload = json.loads(result.output)
            self.assertEqual(payload["artifact_id"], ARTIFACT_ID)
            self.assertEqual(payload["path"], str(destination.resolve()))

            refused = self.invoke(
                [
                    "artifacts",
                    "download",
                    ARTIFACT_ID,
                    str(destination),
                ]
            )

        self.assertEqual(refused.exit_code, 2, refused.output)
        self.assertIn("already exists", refused.output)

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
            [
                "search",
                "scene",
                "yellow taxi",
                "--media-id",
                MEDIA_ID,
                "--top-k",
                "7",
                "--json",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.jobs.submit_search.assert_called_once_with(
            SearchCommand(
                modalities=("scene",),
                query="yellow taxi",
                media_id=MEDIA_ID,
                top_k=7,
            )
        )
        self.assertEqual(json.loads(result.output)["query"], "yellow taxi")

    def test_search_help_explains_cross_media_default(self):
        result = self.invoke(["search", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        normalized = " ".join(unstyle(result.output).replace("│", " ").split())
        self.assertIn(
            "Omit to rank matches across every media item in the active index snapshot.",
            normalized,
        )

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

    def test_media_show_returns_registered_metadata(self):
        self.service.get_media.return_value = MediaAsset(
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

        result = self.invoke(["media", "show", MEDIA_ID, "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.service.get_media.assert_called_once_with(MEDIA_ID)
        self.assertEqual(json.loads(result.output)["media_id"], MEDIA_ID)

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

    def test_index_list_joins_active_ids_to_registered_metadata(self):
        self.service.index_status.return_value = IndexStatus(
            schema_version=1,
            state="ready",
            stage="status",
            message="Index ready.",
            summary=IndexStatusSummary(
                index_schema_version=1,
                snapshot_id=SNAPSHOT_ID,
                media_count=1,
                media_ids=(MEDIA_ID,),
                modalities=("scene", "speech"),
            ),
        )
        self.service.get_media.return_value = MediaAsset(
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

        result = self.invoke(["index", "list", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.service.get_media.assert_called_once_with(MEDIA_ID)
        payload = json.loads(result.output)
        self.assertEqual(payload["snapshot_id"], SNAPSHOT_ID)
        self.assertEqual(payload["media_count"], 1)
        self.assertEqual(payload["modalities"], ["scene", "speech"])
        self.assertEqual(payload["items"][0]["original_filename"], "video.mp4")

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
        self.assertTrue(
            self.service.check_dependencies.call_args.args[0].include_models
        )
        self.assertEqual(
            self.jobs.submit_prepare_models.call_args.args[0].modalities,
            ("scene",),
        )

    def test_doctor_accepts_repeated_modality_options(self):
        self.service.check_dependencies.return_value = DependencyCheckResult(
            ok=True,
            modalities=("speech", "scene"),
            checks=(),
        )
        result = self.invoke(
            [
                "doctor",
                "--modalities",
                "speech",
                "--modalities",
                "scene",
                "--json",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        command = self.service.check_dependencies.call_args.args[0]
        self.assertEqual(command.modalities, ("speech", "scene"))

    def test_doctor_can_skip_model_readiness_for_install_validation(self):
        self.service.check_dependencies.return_value = DependencyCheckResult(
            ok=True,
            modalities=("scene",),
            checks=(),
        )

        result = self.invoke(
            ["doctor", "--modalities", "scene", "--no-models", "--json"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        command = self.service.check_dependencies.call_args.args[0]
        self.assertFalse(command.include_models)

    def test_prepare_announces_start_and_writes_job_progress(self):
        self.service.model_readiness.return_value = DependencyCheckResult(
            ok=False,
            modalities=("scene",),
            checks=(
                CapabilityDependencyCheck(
                    capability="scene",
                    kind=DependencyKind.model,
                    name="google/siglip2-base-patch16-224",
                    download_size_bytes=1_539_458_338,
                    ok=False,
                    error="model artifacts are not prepared",
                ),
            ),
        )
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
        completed = Job(
            job_id=JOB_ID,
            kind=JobKind.prepare_models,
            state=JobState.succeeded,
            queue=JobQueue.cpu,
            result=PrepareModelsJobResult(result=prepared),
        )
        expected_progress = JobProgress(
            stage="scene_model",
            message="Preparing scene model.",
            updated_at=datetime.now(timezone.utc),
        )

        def wait(_job_id, **kwargs):
            kwargs["progress"](
                self.jobs.submit_prepare_models.return_value.model_copy(
                    update={"progress": expected_progress}
                )
            )
            return completed

        self.jobs.wait.side_effect = wait

        with TemporaryDirectory() as temporary_directory:
            progress_path = Path(temporary_directory) / "progress.json"
            result = self.invoke(
                [
                    "prepare",
                    "--modalities",
                    "scene",
                    "--yes",
                    "--progress-file",
                    str(progress_path),
                ]
            )
            written_progress = json.loads(progress_path.read_text())

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("1.43 GiB", result.output)
        self.assertRegex(
            result.output,
            r"\[\d{2}:\d{2}:\d{2}\] Downloading and validating models for "
            r"scene\.",
        )
        self.assertTrue(callable(self.jobs.wait.call_args.kwargs["progress"]))
        self.assertEqual(
            written_progress,
            expected_progress.model_dump(mode="json"),
        )

    def test_prepare_distinguishes_cached_model_verification(self):
        self.service.model_readiness.return_value = DependencyCheckResult(
            ok=True,
            modalities=("scene",),
            checks=(),
        )
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
            r"\[\d{2}:\d{2}:\d{2}\] Validating cached models for scene\.",
        )

    def test_prepare_discloses_size_and_requires_confirmation(self):
        self.service.model_readiness.return_value = DependencyCheckResult(
            ok=False,
            modalities=("scene",),
            checks=(
                CapabilityDependencyCheck(
                    capability="scene",
                    kind=DependencyKind.model,
                    name="google/siglip2-base-patch16-224",
                    download_size_bytes=1_539_458_338,
                    ok=False,
                    error="model artifacts are not prepared",
                ),
            ),
        )

        declined = self.invoke(
            ["prepare", "--modalities", "scene"],
        )

        self.assertNotEqual(declined.exit_code, 0)
        self.assertIn("1.43 GiB", declined.output)
        self.assertIn("Model cache: model-cache", declined.output)
        self.assertIn("Download these models?", declined.output)
        self.jobs.submit_prepare_models.assert_not_called()

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

    def test_doctor_reports_missing_models_and_prepare_command(self):
        self.service.check_dependencies.return_value = DependencyCheckResult(
            ok=False,
            modalities=("scene",),
            checks=(
                CapabilityDependencyCheck(
                    capability="scene",
                    kind=DependencyKind.model,
                    name="google/siglip2-base-patch16-224",
                    download_size_bytes=1_539_458_338,
                    ok=False,
                    error="model artifacts are not prepared",
                ),
            ),
        )

        result = self.invoke(["doctor", "--modalities", "scene"])

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(
            "vidxp prepare --modalities scene",
            result.output,
        )
        self.assertIn("1.43 GiB", result.output)
        self.assertNotIn("pip install", result.output)

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
