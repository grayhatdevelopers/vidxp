from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    DependencyCheckResult,
    ErrorCategory,
    IndexJobResult,
    IndexResult,
    Job,
    JobKind,
    JobProgress,
    JobQueue,
    JobState,
    ListMediaCommand,
    MediaAsset,
    MediaPage,
    MediaState,
    MediaStream,
)
from vidxp.bulk_indexing import (
    BulkIndexItemResult,
    BulkIndexSummary,
    _is_already_indexed,
    _resolve_all_media,
    run_bulk_index,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capability_service import CapabilityService
from vidxp.composition import LocalApplicationContext
from vidxp.core.snapshots import GenerationReference, IndexSnapshot
from vidxp.repositories import RepositoryConfig, RepositoryRegistry

MEDIA_ID_1 = "123456781234423481234567890abcde"
MEDIA_ID_2 = "223456781234423481234567890abcde"
MEDIA_ID_3 = "323456781234423481234567890abcde"
JOB_ID_1 = "423456781234423481234567890abcde"
JOB_ID_2 = "523456781234423481234567890abcde"
SNAPSHOT_ID = "623456781234423481234567890abcde"
GENERATION_ID = "723456781234423481234567890abcde"


def make_media(media_id: str, filename: str = "video.mp4") -> MediaAsset:
    return MediaAsset(
        schema_version=1,
        media_id=media_id,
        video_id=media_id,
        original_filename=filename,
        sha256="1" * 64,
        byte_size=1024,
        detected_mime_type="video/mp4",
        container="mp4",
        duration_seconds=10.0,
        streams=(
            MediaStream(
                index=0,
                kind="video",
                codec="h264",
                width=640,
                height=480,
            ),
        ),
        state=MediaState.ready,
        created_at=datetime.now(timezone.utc),
    )


def make_job(
    job_id: str,
    state: JobState = JobState.succeeded,
    media_id: str = MEDIA_ID_1,
) -> Job:
    result = None
    if state == JobState.succeeded:
        result = IndexJobResult(
            result=IndexResult(
                media_id=media_id,
                generation_id=GENERATION_ID,
                snapshot_id=SNAPSHOT_ID,
                active_media_count=1,
                record_counts={"scene": 1},
            )
        )
    return Job(
        job_id=job_id,
        kind=JobKind.index,
        state=state,
        queue=JobQueue.cpu,
        result=result,
        progress=JobProgress(
            stage="indexing",
            current=1,
            total=1,
            message="Done",
            updated_at=datetime.now(timezone.utc),
        ),
    )


def make_snapshot(generations: dict[str, tuple[str, ...]]) -> IndexSnapshot:
    gen_refs: dict[str, GenerationReference] = {}
    for mid, modalities in generations.items():
        gen_refs[mid] = GenerationReference(
            generation_id=GENERATION_ID,
            media_id=mid,
            manifest_sha256="1" * 64,
            input_sha256="2" * 64,
            config_fingerprint="3" * 64,
            modalities=modalities,
            record_counts={m: 1 for m in modalities},
            store_size_bytes_at_commit=1024,
        )
    return IndexSnapshot(
        schema_version=1,
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime.now(timezone.utc),
        config_fingerprint="0" * 64,
        configuration={},
        generations=gen_refs,
    )


class BulkIndexingHelperTests(unittest.TestCase):
    def test_resolve_all_media_paginates_cursor(self):
        media_1 = make_media(MEDIA_ID_1, "first.mp4")
        media_2 = make_media(MEDIA_ID_2, "second.mp4")
        media_3 = make_media(MEDIA_ID_3, "third.mp4")

        app = Mock()
        app.list_media.side_effect = [
            MediaPage(items=(media_1, media_2), total=3, next_cursor="c1"),
            MediaPage(items=(media_3,), total=3, next_cursor=None),
        ]

        result = _resolve_all_media(app)

        self.assertEqual(result, [media_1, media_2, media_3])
        self.assertEqual(app.list_media.call_count, 2)
        app.list_media.assert_any_call(
            ListMediaCommand(
                page_size=100,
                cursor=None,
                state=MediaState.ready,
            )
        )
        app.list_media.assert_any_call(
            ListMediaCommand(
                page_size=100,
                cursor="c1",
                state=MediaState.ready,
            )
        )

    def test_resolve_all_media_empty_catalog(self):
        app = Mock()
        app.list_media.return_value = MediaPage(
            items=(), total=0, next_cursor=None
        )

        result = _resolve_all_media(app)

        self.assertEqual(result, [])
        self.assertEqual(app.list_media.call_count, 1)

    def test_is_already_indexed_snapshot_none(self):
        self.assertFalse(_is_already_indexed(None, MEDIA_ID_1))

    def test_is_already_indexed_media_not_in_generations(self):
        snapshot = make_snapshot({MEDIA_ID_2: ("scene",)})
        self.assertFalse(_is_already_indexed(snapshot, MEDIA_ID_1))

    def test_is_already_indexed_no_modalities_requested(self):
        snapshot = make_snapshot({MEDIA_ID_1: ("scene",)})
        self.assertTrue(_is_already_indexed(snapshot, MEDIA_ID_1, None))

    def test_is_already_indexed_matching_modalities_subset(self):
        snapshot = make_snapshot({MEDIA_ID_1: ("scene", "speech", "actor")})
        self.assertTrue(
            _is_already_indexed(snapshot, MEDIA_ID_1, ("scene", "speech"))
        )

    def test_is_already_indexed_missing_requested_modality(self):
        snapshot = make_snapshot({MEDIA_ID_1: ("scene",)})
        self.assertFalse(
            _is_already_indexed(snapshot, MEDIA_ID_1, ("scene", "speech"))
        )


class RunBulkIndexTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.jobs = Mock()
        self.media_1 = make_media(MEDIA_ID_1, "one.mp4")
        self.media_2 = make_media(MEDIA_ID_2, "two.mp4")
        self.app.get_media.side_effect = lambda mid: (
            self.media_1 if mid == MEDIA_ID_1 else self.media_2
        )
        self.app._read_active_snapshot.return_value = None

    def test_bulk_index_multiple_media_ids_success(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        job_2 = make_job(JOB_ID_2, media_id=MEDIA_ID_2)
        self.jobs.submit_index.side_effect = [job_1, job_2]
        self.jobs.wait.side_effect = [job_1, job_2]

        started: list[tuple[str, str]] = []
        completed: list[BulkIndexItemResult] = []

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1, MEDIA_ID_2],
            modalities=["scene"],
            on_item_start=lambda mid, fn: started.append((mid, fn)),
            on_item_complete=lambda r: completed.append(r),
        )

        self.assertEqual(
            summary,
            BulkIndexSummary(
                total=2,
                indexed=2,
                skipped=0,
                failed=0,
                queued=0,
                results=(
                    BulkIndexItemResult(
                        media_id=MEDIA_ID_1,
                        filename="one.mp4",
                        status="indexed",
                        job_id=JOB_ID_1,
                    ),
                    BulkIndexItemResult(
                        media_id=MEDIA_ID_2,
                        filename="two.mp4",
                        status="indexed",
                        job_id=JOB_ID_2,
                    ),
                ),
            ),
        )
        self.assertEqual(
            started,
            [(MEDIA_ID_1, "one.mp4"), (MEDIA_ID_2, "two.mp4")],
        )
        self.assertEqual(len(completed), 2)
        self.assertEqual(self.jobs.submit_index.call_count, 2)
        self.assertEqual(self.jobs.wait.call_count, 2)

    def test_bulk_index_all_eligible_paginates(self):
        self.app.list_media.side_effect = [
            MediaPage(items=(self.media_1,), total=2, next_cursor="c1"),
            MediaPage(items=(self.media_2,), total=2, next_cursor=None),
        ]
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        job_2 = make_job(JOB_ID_2, media_id=MEDIA_ID_2)
        self.jobs.submit_index.side_effect = [job_1, job_2]
        self.jobs.wait.side_effect = [job_1, job_2]

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            all_eligible=True,
            modalities=["scene"],
        )

        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.indexed, 2)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.failed, 0)

    def test_bulk_index_skips_already_indexed(self):
        snapshot = make_snapshot({MEDIA_ID_1: ("scene",)})
        self.app._read_active_snapshot.return_value = snapshot

        job_2 = make_job(JOB_ID_2, media_id=MEDIA_ID_2)
        self.jobs.submit_index.return_value = job_2
        self.jobs.wait.return_value = job_2

        started: list[tuple[str, str]] = []
        completed: list[BulkIndexItemResult] = []

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1, MEDIA_ID_2],
            skip_indexed=True,
            modalities=["scene"],
            on_item_start=lambda mid, fn: started.append((mid, fn)),
            on_item_complete=lambda r: completed.append(r),
        )

        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.indexed, 1)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(summary.results[0].status, "skipped")
        self.assertEqual(summary.results[0].media_id, MEDIA_ID_1)
        self.assertEqual(summary.results[1].status, "indexed")
        self.assertEqual(summary.results[1].media_id, MEDIA_ID_2)
        # started should NOT include skipped item
        self.assertEqual(started, [(MEDIA_ID_2, "two.mp4")])
        self.assertEqual(len(completed), 2)
        self.jobs.submit_index.assert_called_once()

    def test_bulk_index_reindex_flag_overrides_skip(self):
        snapshot = make_snapshot({MEDIA_ID_1: ("scene",)})
        self.app._read_active_snapshot.return_value = snapshot

        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1
        self.jobs.wait.return_value = job_1

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1],
            skip_indexed=False,
            modalities=["scene"],
        )

        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.indexed, 1)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.results[0].status, "indexed")

    def test_bulk_index_detach_queues_jobs(self):
        job_1 = make_job(JOB_ID_1, state=JobState.queued, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1],
            detach=True,
            modalities=["scene"],
        )

        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.queued, 1)
        self.assertEqual(summary.indexed, 0)
        self.assertEqual(summary.results[0].status, "queued")
        self.assertEqual(summary.results[0].job_id, JOB_ID_1)
        self.jobs.wait.assert_not_called()

    def test_bulk_index_error_resilience(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        job_2 = make_job(JOB_ID_2, media_id=MEDIA_ID_2)
        self.jobs.submit_index.side_effect = [job_1, job_2]
        self.jobs.wait.side_effect = [
            ApplicationError(
                "transcription_failed",
                ErrorCategory.unavailable,
                "Model crashed during transcription.",
            ),
            job_2,
        ]

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1, MEDIA_ID_2],
            modalities=["scene"],
        )

        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.indexed, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.results[0].status, "failed")
        self.assertEqual(
            summary.results[0].error_code, "transcription_failed"
        )
        self.assertEqual(
            summary.results[0].error_message,
            "Model crashed during transcription.",
        )
        self.assertEqual(summary.results[1].status, "indexed")
        self.assertEqual(summary.results[1].job_id, JOB_ID_2)

    def test_bulk_index_generic_exception_resilience(self):
        self.jobs.submit_index.side_effect = RuntimeError("Disk IO error")

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1],
            modalities=["scene"],
        )

        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.results[0].status, "failed")
        self.assertEqual(summary.results[0].error_code, "unexpected_error")
        self.assertIn("Disk IO error", summary.results[0].error_message or "")


    def test_bulk_index_forwards_options_and_command_fields(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1
        self.jobs.wait.return_value = job_1

        progress_events: list[tuple[str, Any]] = []

        def fake_wait(job_id, progress=None):
            if progress:
                progress(job_1)
            return job_1

        self.jobs.wait.side_effect = fake_wait

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1],
            modalities=["scene"],
            frame_stride=3,
            scene_sample_fps=1.5,
            capability_options={"scene": {"threshold": 0.7}},
            on_item_progress=lambda mid, curr: progress_events.append((mid, curr)),
        )

        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.indexed, 1)
        submitted_command = self.jobs.submit_index.call_args.args[0]
        self.assertIsInstance(submitted_command, CreateIndexCommand)
        self.assertEqual(submitted_command.media_id, MEDIA_ID_1)
        self.assertEqual(submitted_command.modalities, ("scene",))
        self.assertEqual(submitted_command.frame_stride, 3)
        self.assertEqual(submitted_command.scene_sample_fps, 1.5)
        self.assertEqual(
            submitted_command.capability_options,
            {"scene": {"threshold": 0.7}},
        )
        self.assertEqual(len(progress_events), 1)
        self.assertEqual(progress_events[0][0], MEDIA_ID_1)

    def test_bulk_index_default_modalities_resolves_from_application(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1
        self.jobs.wait.return_value = job_1
        self.app.select_index_modalities.return_value = ("scene", "speech")

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1],
            modalities=None,
        )

        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.indexed, 1)
        submitted_command = self.jobs.submit_index.call_args.args[0]
        self.assertEqual(submitted_command.modalities, ("scene", "speech"))

    def test_bulk_index_default_modalities_resolves_from_list_capabilities(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1
        self.jobs.wait.return_value = job_1
        del self.app.select_index_modalities
        cap1 = Mock()
        cap1.name = "scene"
        cap1.supports_indexing = True
        cap2 = Mock()
        cap2.name = "summary"
        cap2.supports_indexing = False
        self.app.list_capabilities.return_value = [cap1, cap2]

        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[MEDIA_ID_1],
            modalities=None,
        )

        self.assertEqual(summary.total, 1)
        submitted_command = self.jobs.submit_index.call_args.args[0]
        self.assertEqual(submitted_command.modalities, ("scene",))

    def test_bulk_index_empty_items(self):
        summary = run_bulk_index(
            application=self.app,
            jobs=self.jobs,
            media_ids=[],
            modalities=["scene"],
        )
        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.indexed, 0)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(summary.queued, 0)
        self.assertEqual(summary.results, ())


class CliBulkIndexTests(unittest.TestCase):
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
        self.service._read_active_snapshot.return_value = None

        self.media_1 = make_media(MEDIA_ID_1, "one.mp4")
        self.media_2 = make_media(MEDIA_ID_2, "two.mp4")
        self.service.get_media.side_effect = lambda mid: (
            self.media_1 if mid == MEDIA_ID_1 else self.media_2
        )
        self.service.list_media.return_value = MediaPage(
            items=(self.media_1, self.media_2), total=2, next_cursor=None
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

    def test_cli_requires_media_ids_or_all(self):
        result = self.invoke(["index", "bulk"])
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("Provide either media IDs or pass --all.", result.output)

    def test_cli_rejects_both_media_ids_and_all(self):
        result = self.invoke(["index", "bulk", MEDIA_ID_1, "--all"])
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("Pass media IDs or --all, not both.", result.output)

    def test_cli_bulk_index_success_json(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        job_2 = make_job(JOB_ID_2, media_id=MEDIA_ID_2)
        self.jobs.submit_index.side_effect = [job_1, job_2]
        self.jobs.wait.side_effect = [job_1, job_2]

        result = self.invoke(
            [
                "index",
                "bulk",
                MEDIA_ID_1,
                MEDIA_ID_2,
                "--modality",
                "scene",
                "--json",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["indexed"], 2)
        self.assertEqual(payload["failed"], 0)
        self.assertEqual(payload["skipped"], 0)
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(payload["results"][0]["media_id"], MEDIA_ID_1)
        self.assertEqual(payload["results"][0]["status"], "indexed")
        self.assertEqual(payload["results"][0]["job_id"], JOB_ID_1)

    def test_cli_bulk_index_all_success_table(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        job_2 = make_job(JOB_ID_2, media_id=MEDIA_ID_2)
        self.jobs.submit_index.side_effect = [job_1, job_2]
        self.jobs.wait.side_effect = [job_1, job_2]

        result = self.invoke(["index", "bulk", "--all", "--modality", "scene"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Bulk indexing summary", result.output)
        self.assertIn("Total: 2, Indexed: 2, Skipped: 0, Failed: 0, Queued: 0.", result.output)

    def test_cli_bulk_index_forwards_options_and_flags(self):
        job_1 = make_job(JOB_ID_1, state=JobState.queued, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1

        result = self.invoke(
            [
                "index",
                "bulk",
                MEDIA_ID_1,
                "--modality",
                "scene",
                "--frame-stride",
                "4",
                "--scene-sample-fps",
                "2.5",
                "--option",
                "scene.threshold=0.8",
                "--detach",
                "--reindex",
                "--json",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["queued"], 1)
        submitted_command = self.jobs.submit_index.call_args.args[0]
        self.assertEqual(submitted_command.frame_stride, 4)
        self.assertEqual(submitted_command.scene_sample_fps, 2.5)
        self.assertEqual(
            submitted_command.capability_options,
            {"scene": {"threshold": 0.8}},
        )

    def test_cli_bulk_index_with_failure_exits_code_1(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1
        self.jobs.wait.side_effect = ApplicationError(
            "model_error",
            ErrorCategory.unavailable,
            "Failed to load model weights.",
        )

        result = self.invoke(
            ["index", "bulk", MEDIA_ID_1, "--modality", "scene", "--json"]
        )

        self.assertEqual(result.exit_code, 1, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(payload["results"][0]["status"], "failed")
        self.assertEqual(payload["results"][0]["error_code"], "model_error")

    def test_cli_bulk_index_all_json(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        job_2 = make_job(JOB_ID_2, media_id=MEDIA_ID_2)
        self.jobs.submit_index.side_effect = [job_1, job_2]
        self.jobs.wait.side_effect = [job_1, job_2]

        result = self.invoke(["index", "bulk", "--all", "--modality", "scene", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["indexed"], 2)

    def test_cli_bulk_index_with_failure_table_output(self):
        job_1 = make_job(JOB_ID_1, media_id=MEDIA_ID_1)
        self.jobs.submit_index.return_value = job_1
        self.jobs.wait.side_effect = ApplicationError(
            "model_error",
            ErrorCategory.unavailable,
            "Failed to load model weights.",
        )

        result = self.invoke(["index", "bulk", MEDIA_ID_1, "--modality", "scene"])

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("Bulk indexing summary", result.output)
        self.assertIn("model_error", result.output)
        self.assertIn("Failed: 1", result.output)
