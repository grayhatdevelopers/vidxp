import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from pydantic import ValidationError

from vidxp.application_models import (
    ActorOverlayJobRequest,
    ApplicationError,
    ErrorCategory,
    ErrorDetail,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    CreateActorOverlayCommand,
    CreateIndexCommand,
    Job,
    JobKind,
    JobProgress,
    JobQueue,
    JobState,
    InvalidRequestError,
    IndexSnapshotReference,
    ListJobsCommand,
    QueryJobRequest,
    QueryVideoCommand,
    SearchCommand,
    SearchJobRequest,
)
from vidxp.core.storage import BUNDLED_CHROMA_SERVER_URL
from vidxp.job_service import JobService
from vidxp.ports import InvalidJobBackendRequestError
from vidxp.execution import ExecutionContext
from vidxp.composition import _server_chroma_url
from vidxp.settings import ApplicationMode, VidXPSettings
from vidxp.workflow_runtime import (
    BUNDLED_POSTGRES_DATABASE_URL,
    workflow_database_url,
)


MEDIA_ID = "123456781234423481234567890abcde"
JOB_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
SNAPSHOT_SHA256 = "a" * 64


class JobContractTests(unittest.TestCase):
    def test_progress_is_typed_and_position_is_bounded(self):
        progress = JobProgress(
            stage="frames",
            message="Indexing frames.",
            current=2,
            total=3,
            updated_at=datetime.now(timezone.utc),
        )

        self.assertEqual(progress.schema_version, 1)
        with self.assertRaises(ValidationError):
            JobProgress(
                stage="frames",
                message="Invalid.",
                current=4,
                total=3,
                updated_at=datetime.now(timezone.utc),
            )

    def test_public_job_contract_has_no_path_or_storage_fields(self):
        job = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        schema = json.dumps(Job.model_json_schema())
        self.assertEqual(job.schema_version, 2)
        self.assertNotIn("storage_key", schema)
        self.assertNotIn('"path"', schema)
        self.assertNotIn("model_cache", schema)

    def test_job_summary_omits_result_and_ignores_in_stage_progress_noise(self):
        now = datetime.now(timezone.utc)
        first = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.running,
            queue=JobQueue.cpu,
            progress=JobProgress(
                stage="frames",
                message="Indexed 1 frame.",
                current=1,
                total=10,
                updated_at=now,
            ),
        )
        later = first.model_copy(
            update={
                "progress": first.progress.model_copy(
                    update={"current": 8, "message": "Indexed 8 frames."}
                )
            }
        )

        first_summary = JobService._summary(first)
        later_summary = JobService._summary(later)

        self.assertNotIn("result", first_summary.model_dump(mode="json"))
        self.assertNotIn(
            "poll_after_seconds",
            first_summary.model_dump(mode="json"),
        )
        self.assertFalse(first_summary.result_available)
        self.assertEqual(
            first_summary.observation_token,
            later_summary.observation_token,
        )
        self.assertEqual(later_summary.progress.current, 8)

    def test_wait_for_change_returns_on_stage_change_with_compact_status(self):
        now = datetime.now(timezone.utc)
        running = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.running,
            queue=JobQueue.cpu,
            progress=JobProgress(
                stage="frames",
                message="Indexing frames.",
                current=1,
                total=10,
                updated_at=now,
            ),
        )
        next_stage = running.model_copy(
            update={
                "progress": JobProgress(
                    stage="embeddings",
                    message="Writing embeddings.",
                    current=0,
                    total=10,
                    updated_at=now,
                )
            }
        )
        backend = Mock()
        backend.get.side_effect = [running, running, next_stage]
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
                workflow_poll_interval_seconds=0.001,
            ),
            backend=backend,
        )

        waited = service.wait_for_change(JOB_ID, timeout_seconds=1)

        self.assertTrue(waited.changed)
        self.assertFalse(waited.timed_out)
        self.assertEqual(waited.job.progress.stage, "embeddings")
        self.assertNotIn("result", waited.model_dump(mode="json")["job"])

    def test_job_summary_coalesces_evidence_artifact_stages(self):
        now = datetime.now(timezone.utc)
        summaries = [
            JobService._summary(
                Job(
                    job_id=JOB_ID,
                    kind=JobKind.search,
                    state=JobState.running,
                    queue=JobQueue.cpu,
                    progress=JobProgress(
                        stage=stage,
                        message=f"{stage} an evidence frame.",
                        updated_at=now,
                    ),
                )
            )
            for stage in ("rendering", "validating", "publishing")
        ]

        self.assertEqual(
            len({summary.observation_token for summary in summaries}),
            1,
        )
        self.assertEqual(
            [summary.progress.stage for summary in summaries],
            ["rendering", "validating", "publishing"],
        )

    def test_wait_for_change_times_out_without_fabricating_a_change(self):
        running = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.running,
            queue=JobQueue.cpu,
        )
        backend = Mock()
        backend.get.return_value = running
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
                workflow_poll_interval_seconds=0.001,
            ),
            backend=backend,
        )

        waited = service.wait_for_change(JOB_ID, timeout_seconds=0.003)

        self.assertFalse(waited.changed)
        self.assertTrue(waited.timed_out)
        self.assertEqual(waited.job.state, JobState.running)

    def test_job_service_routes_model_work_without_reimplementing_it(self):
        backend = Mock()
        preflight = Mock()
        backend.submit.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.index,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
            ),
            backend=backend,
            index_preflight=preflight,
        )

        job = service.submit_index(
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("scene",),
            )
        )

        self.assertEqual(job.job_id, JOB_ID)
        request = backend.submit.call_args.args[0]
        preflight.assert_called_once_with(request.command)
        self.assertEqual(request.kind, JobKind.index)
        self.assertEqual(request.command.media_id, MEDIA_ID)
        self.assertEqual(
            backend.submit.call_args.kwargs["queue"],
            JobQueue.cpu,
        )

    def test_index_preflight_failure_never_reaches_the_job_backend(self):
        backend = Mock()
        command = CreateIndexCommand(
            media_id=MEDIA_ID,
            modalities=("scene",),
        )
        preflight = Mock(
            side_effect=ApplicationError(
                "invalid_request",
                ErrorCategory.validation,
                "The capability is not usable.",
            )
        )
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
            ),
            backend=backend,
            index_preflight=preflight,
        )

        with self.assertRaises(ApplicationError):
            service.submit_index(command)

        preflight.assert_called_once_with(command)
        backend.submit.assert_not_called()

    def test_job_list_cursor_is_bounded(self):
        with self.assertRaises(ValidationError):
            ListJobsCommand(cursor="x" * 513)

    def test_search_uses_the_same_model_worker_queue(self):
        backend = Mock()
        planner = Mock()
        command = SearchCommand(
            modalities=("scene",),
            query="taxi",
            top_k=2,
        )
        planner.plan_search.return_value = SearchJobRequest(
            command=command,
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
        )
        backend.submit.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.search,
            state=JobState.queued,
            queue=JobQueue.gpu,
        )
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cuda:0",
            ),
            backend=backend,
            read_planner=planner,
        )

        service.submit_search(command)

        request = backend.submit.call_args.args[0]
        self.assertEqual(request.kind, JobKind.search)
        self.assertEqual(request.snapshot.snapshot_id, SNAPSHOT_ID)
        planner.plan_search.assert_called_once_with(command)
        self.assertEqual(
            backend.submit.call_args.kwargs["queue"],
            JobQueue.gpu,
        )

    def test_query_uses_the_same_pinned_model_worker_boundary(self):
        backend = Mock()
        planner = Mock()
        command = QueryVideoCommand(question="What happens next?")
        planner.plan_query.return_value = QueryJobRequest(
            command=command,
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
        )
        backend.submit.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.query,
            state=JobState.queued,
            queue=JobQueue.gpu,
        )
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cuda:0",
            ),
            backend=backend,
            read_planner=planner,
        )

        service.submit_query(command)

        request = backend.submit.call_args.args[0]
        self.assertEqual(request.kind, JobKind.query)
        self.assertEqual(request.snapshot.snapshot_id, SNAPSHOT_ID)
        planner.plan_query.assert_called_once_with(command)
        self.assertEqual(
            backend.submit.call_args.kwargs["queue"],
            JobQueue.gpu,
        )

    def test_actor_job_retains_pinned_snapshot_identity(self):
        backend = Mock()
        planner = Mock()
        command = CreateActorOverlayCommand(cluster_id="actor-1")
        planner.plan_actor_overlay.return_value = ActorOverlayJobRequest(
            command=command,
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
        )
        backend.submit.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.actor_overlay,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
            ),
            backend=backend,
            read_planner=planner,
        )

        service.submit_actor_overlay(command)

        request = backend.submit.call_args.args[0]
        self.assertEqual(request.snapshot.snapshot_id, SNAPSHOT_ID)
        planner.plan_actor_overlay.assert_called_once_with(command)

    def test_search_and_actor_identifiers_are_bounded(self):
        command = SearchCommand(
            modalities=(" scene ",),
            query="  a chef prepares pizza  ",
        )
        self.assertEqual(command.modalities, ("scene",))
        self.assertEqual(command.query, "a chef prepares pizza")

        for values in (
            {"modalities": ["scene/video"], "query": "taxi"},
            {"modalities": ["scene"], "query": "x" * 4097},
            {"modalities": ["scene"], "query": "   "},
        ):
            with self.assertRaises(ValidationError):
                SearchCommand(**values)

        with self.assertRaises(ValidationError):
            CreateActorOverlayCommand(cluster_id="../../video")
        encoded_cluster = CreateActorOverlayCommand(
            cluster_id=(
                "generation:run%20name:media:actor-cluster:1"
            )
        )
        self.assertIn("%20", encoded_cluster.cluster_id)

    def test_initial_evidence_stays_bounded_while_followup_allows_ten(self):
        followup = EvidenceDeliveryPolicy(
            mode=EvidenceDeliveryMode.keyframes,
            max_items=10,
        )
        self.assertEqual(followup.max_items, 10)

        with self.assertRaises(ValidationError):
            SearchCommand(query="taxi", evidence_delivery=followup)
        with self.assertRaises(ValidationError):
            QueryVideoCommand(question="Where is it?", evidence_delivery=followup)

    def test_canonical_dbos_job_id_has_a_hex_operation_identity(self):
        execution = ExecutionContext(
            job_id="22345678-1234-4234-8123-4567890abcde"
        )

        self.assertEqual(execution.operation_id, JOB_ID)

    def test_local_storage_uses_repository_services(self):
        settings = VidXPSettings(
            repository_root=Path("repository"),
        )

        self.assertEqual(
            workflow_database_url(settings),
            (
                "sqlite:///"
                f"{settings.layout.workflow_database.resolve().as_posix()}"
            ),
        )
        self.assertIsNone(_server_chroma_url(settings))

    def test_server_uses_bundled_storage_services(self):
        settings = VidXPSettings(
            mode=ApplicationMode.server,
            runtime_backend="cpu",
        )

        self.assertEqual(
            workflow_database_url(settings),
            BUNDLED_POSTGRES_DATABASE_URL,
        )
        self.assertEqual(
            _server_chroma_url(settings),
            BUNDLED_CHROMA_SERVER_URL,
        )

    def test_job_backend_errors_are_normalized_for_every_adapter(self):
        backend = Mock()
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
            ),
            backend=backend,
        )
        backend.get.side_effect = InvalidJobBackendRequestError(
            "invalid workflow id"
        )

        with self.assertRaises(InvalidRequestError):
            service.get("invalid")

        backend.get.side_effect = RuntimeError("database unavailable")
        with self.assertRaises(ApplicationError) as raised:
            service.get(JOB_ID)
        self.assertEqual(raised.exception.code, "job_backend_unavailable")

    def test_failed_model_preparation_error_round_trips_through_job_service(self):
        error = ErrorDetail(
            code="model_download_failed",
            category=ErrorCategory.unavailable,
            message="The model download failed after three attempts.",
            details={
                "capability": "dialogue.transcription",
                "model": "publisher/model",
                "attempts": 3,
                "reason": "ConnectionError",
                "partial_files_preserved": True,
                "remediation": "vidxp prepare --modalities dialogue",
            },
            retryable=True,
        )
        backend = Mock()
        backend.get.return_value = Job(
            job_id=JOB_ID,
            kind=JobKind.prepare_models,
            state=JobState.failed,
            queue=JobQueue.cpu,
            error=error,
        )
        service = JobService(
            settings=VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
            ),
            backend=backend,
        )

        with self.assertRaises(ApplicationError) as raised:
            service.result(JOB_ID)

        self.assertEqual(
            raised.exception.to_dict(),
            error.model_dump(mode="json"),
        )


if __name__ == "__main__":
    unittest.main()
