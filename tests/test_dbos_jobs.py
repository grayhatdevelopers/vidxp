import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from unittest.mock import ANY, Mock

from dbos import DBOS

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    ErrorCategory,
    FusedSearchResult,
    FusionProvenance,
    IndexSnapshotReference,
    IndexResult,
    JobKind,
    JobQueue,
    JobState,
    ListJobsCommand,
    QueryAnswer,
    QueryAnswerMode,
    QueryJobRequest,
    QueryPlan,
    QueryVideoCommand,
    SearchCommand,
    SearchHit,
    SearchJobRequest,
    SearchMomentsPlanStep,
    SearchResult,
)
from vidxp.core.cursors import MAX_CURSOR_OFFSET, encode_cursor
from vidxp.infrastructure.dbos_jobs import (
    DBOSJobBackend,
    _decode_search_result,
)
from vidxp.job_service import JobService
from vidxp.ports import InvalidJobBackendRequestError
from vidxp.settings import VidXPSettings
from vidxp.workflow_contracts import (
    QUEUE_NAMES,
    WORKFLOW_CLASS_NAME,
    WORKFLOW_INSTANCE_NAME,
    WORKFLOW_KINDS,
    decode_workflow_request,
)


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
IDEMPOTENCY_KEY = "423456781234423481234567890abcde"
SNAPSHOT_SHA256 = "a" * 64


class DBOSJobIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vidxp.infrastructure.dbos_workflows import VidXPWorkerWorkflows

        cls.worker = VidXPWorkerWorkflows(Mock())

    def setUp(self):
        self.directory = TemporaryDirectory(ignore_cleanup_errors=True)
        database = Path(self.directory.name) / "jobs.sqlite3"
        self.database_url = f"sqlite:///{database.as_posix()}"
        DBOS.destroy()
        DBOS(
            config={
                "name": "vidxp",
                "system_database_url": self.database_url,
                "application_version": "test-v1",
                "executor_id": "test-worker",
                "use_listen_notify": False,
            }
        )
        self.application = Mock()
        self.application.create_index.return_value = IndexResult(
            media_id=MEDIA_ID,
            generation_id=GENERATION_ID,
            snapshot_id=SNAPSHOT_ID,
            active_media_count=1,
            record_counts={"scene": 1},
        )
        self.application.search.return_value = FusedSearchResult(
            query_id="fused:taxi",
            query="taxi",
            modalities=("scene",),
            fusion=FusionProvenance(
                requested_modalities=("scene",),
                searched_modalities=("scene",),
            ),
        )
        self.application.query_video.return_value = QueryAnswer(
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
        self.worker.application = self.application
        DBOS.reset_system_database()
        DBOS.listen_queues([QUEUE_NAMES[JobQueue.cpu]])
        DBOS.launch()
        DBOS.register_queue(
            QUEUE_NAMES[JobQueue.cpu],
            worker_concurrency=1,
        )
        self.backend = DBOSJobBackend(
            system_database_url=self.database_url,
            application_version="test-v1",
        )
        self.jobs = JobService(
            settings=VidXPSettings(
                repository_root=self.directory.name,
                runtime_backend="cpu",
                workflow_poll_interval_seconds=0.01,
            ),
            backend=self.backend,
            read_planner=Mock(
                plan_search=Mock(
                    side_effect=lambda command: SearchJobRequest(
                        command=command,
                        snapshot=IndexSnapshotReference(
                            snapshot_id=SNAPSHOT_ID,
                            snapshot_sha256=SNAPSHOT_SHA256,
                        ),
                    )
                ),
                plan_query=Mock(
                    side_effect=lambda command: QueryJobRequest(
                        command=command,
                        snapshot=IndexSnapshotReference(
                            snapshot_id=SNAPSHOT_ID,
                            snapshot_sha256=SNAPSHOT_SHA256,
                        ),
                    )
                ),
            ),
        )

    def tearDown(self):
        self.backend.close()
        DBOS.destroy()
        self.directory.cleanup()

    def test_sqlite_queue_persists_progress_and_typed_result(self):
        def create_index(command, *, execution):
            execution.report(
                {
                    "stage": "frames",
                    "message": "Indexed one frame.",
                    "current": 1,
                    "total": 1,
                }
            )
            return IndexResult(
                media_id=command.media_id,
                generation_id=GENERATION_ID,
                snapshot_id=SNAPSHOT_ID,
                active_media_count=1,
                record_counts={"scene": 1},
            )

        self.application.create_index.side_effect = create_index
        submitted = self.jobs.submit_index(
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("scene",),
            )
        )
        completed = self.jobs.wait(submitted.job_id)

        self.assertEqual(completed.state, JobState.succeeded)
        self.assertEqual(completed.kind, JobKind.index)
        self.assertEqual(completed.progress.stage, "complete")
        self.assertEqual(
            completed.result.result.generation_id,
            GENERATION_ID,
        )
        page = self.jobs.list(ListJobsCommand(page_size=1))
        self.assertEqual(page.items[0].job_id, submitted.job_id)
        self.application.create_index.assert_called_once()

    def test_health_requires_owned_executor_health(self):
        health_check = Mock(side_effect=RuntimeError("worker unavailable"))
        self.backend.health_check = health_check

        with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
            self.backend.health()

        health_check.assert_called_once_with()

    def test_model_search_runs_in_worker_and_returns_typed_result(self):
        submitted = self.jobs.submit_search(
            SearchCommand(
                modalities=("scene",),
                query="taxi",
                top_k=1,
            )
        )
        completed = self.jobs.wait(submitted.job_id)

        self.assertEqual(completed.state, JobState.succeeded)
        self.assertEqual(completed.kind, JobKind.search)
        self.assertEqual(completed.result.result.query, "taxi")
        self.application.search.assert_called_once_with(
            SearchCommand(
                modalities=("scene",),
                query="taxi",
                top_k=1,
            ),
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
            execution=ANY,
        )

    def test_grounded_query_runs_in_worker_and_returns_typed_result(self):
        command = QueryVideoCommand(
            question="What happens?",
            modalities=("scene",),
        )

        submitted = self.jobs.submit_query(command)
        completed = self.jobs.wait(submitted.job_id)

        self.assertEqual(completed.state, JobState.succeeded)
        self.assertEqual(completed.kind, JobKind.query)
        self.assertEqual(
            completed.result.result.mode,
            QueryAnswerMode.no_evidence,
        )
        self.application.query_video.assert_called_once_with(
            command,
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
            execution=ANY,
        )

    def test_legacy_atomic_search_output_remains_readable(self):
        legacy = SearchResult(
            query_id="scene:legacy",
            query="taxi",
            modality="scene",
            hits=(
                SearchHit(
                    rank=1,
                    media_id=MEDIA_ID,
                    video_id=MEDIA_ID,
                    generation_id=GENERATION_ID,
                    start=1,
                    end=2,
                    score=-0.1,
                    raw_distance=0.1,
                    modality="scene",
                    source_id="scene:1",
                ),
            ),
        )

        decoded = _decode_search_result(legacy.model_dump(mode="json"))

        self.assertEqual(decoded.query, "taxi")
        self.assertEqual(decoded.moments[0].hits[0].source_id, "scene:1")
        self.assertEqual(WORKFLOW_KINDS["vidxp.search.v1"], JobKind.search)

    def test_legacy_search_request_upgrades_to_the_v2_command(self):
        upgraded = decode_workflow_request(
            {
                "kind": "search",
                "command": {
                    "modality": "scene",
                    "query": "taxi",
                    "top_k": 3,
                },
                "snapshot": {
                    "snapshot_id": SNAPSHOT_ID,
                    "snapshot_sha256": SNAPSHOT_SHA256,
                },
            }
        )

        self.assertIsInstance(upgraded, SearchJobRequest)
        self.assertEqual(upgraded.command.modalities, ("scene",))
        self.assertEqual(upgraded.command.top_k, 3)
        self.assertTrue(hasattr(self.worker, "legacy_search_workflow"))
        self.assertTrue(hasattr(self.worker, "run_legacy_search_step"))

    def test_registered_legacy_search_workflow_executes_upgraded_request(self):
        legacy_id = "923456781234423481234567890abcde"
        self.backend.client.enqueue(
            {
                "workflow_name": "vidxp.search.v1",
                "queue_name": QUEUE_NAMES[JobQueue.cpu],
                "workflow_id": legacy_id,
                "app_version": "test-v1",
                "attributes": {"vidxp_queue": JobQueue.cpu.value},
                "class_name": WORKFLOW_CLASS_NAME,
                "instance_name": WORKFLOW_INSTANCE_NAME,
            },
            {
                "kind": "search",
                "command": {
                    "modality": "scene",
                    "query": "taxi",
                    "top_k": 1,
                },
                "snapshot": {
                    "snapshot_id": SNAPSHOT_ID,
                    "snapshot_sha256": SNAPSHOT_SHA256,
                },
            },
        )

        completed = self.jobs.wait(legacy_id)

        self.assertEqual(completed.state, JobState.succeeded)
        self.application.search.assert_called_once_with(
            SearchCommand(
                modalities=("scene",),
                query="taxi",
                top_k=1,
            ),
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
            execution=ANY,
        )

    def test_failed_legacy_search_retries_as_a_v2_workflow(self):
        legacy_id = "a23456781234423481234567890abcde"
        self.application.search.side_effect = (
            ApplicationError(
                "temporary_search_failure",
                ErrorCategory.unavailable,
                "The temporary search dependency is unavailable.",
                retryable=True,
            ),
            self.application.search.return_value,
        )
        payload = {
            "kind": "search",
            "command": {
                "modality": "scene",
                "query": "taxi",
                "top_k": 1,
            },
            "snapshot": {
                "snapshot_id": SNAPSHOT_ID,
                "snapshot_sha256": SNAPSHOT_SHA256,
            },
        }
        self.backend.client.enqueue(
            {
                "workflow_name": "vidxp.search.v1",
                "queue_name": QUEUE_NAMES[JobQueue.cpu],
                "workflow_id": legacy_id,
                "app_version": "test-v1",
                "attributes": {"vidxp_queue": JobQueue.cpu.value},
                "class_name": WORKFLOW_CLASS_NAME,
                "instance_name": WORKFLOW_INSTANCE_NAME,
            },
            payload,
        )
        with self.assertRaises(ApplicationError):
            self.jobs.wait(legacy_id)

        retried = self.jobs.retry(
            legacy_id,
            retry_id=IDEMPOTENCY_KEY,
        )
        completed = self.jobs.wait(retried.job_id)

        self.assertEqual(retried.job_id, IDEMPOTENCY_KEY)
        self.assertEqual(completed.state, JobState.succeeded)
        self.assertEqual(completed.result.result.query, "taxi")
        status = self.backend._status(retried.job_id)
        self.assertEqual(status.name, "vidxp.search.v2")

    def test_huge_cursor_offset_is_rejected_before_database_access(self):
        cursor = encode_cursor(
            "vidxp:jobs",
            {"offset": MAX_CURSOR_OFFSET + 1},
        )
        self.backend.client = Mock(wraps=self.backend.client)

        with self.assertRaises(InvalidJobBackendRequestError):
            self.backend.list(ListJobsCommand(page_size=1, cursor=cursor))

        self.backend.client.list_workflows.assert_not_called()

    def test_submission_idempotency_replays_only_the_same_request(self):
        command = CreateIndexCommand(
            media_id=MEDIA_ID,
            modalities=("scene",),
        )

        first = self.jobs.submit_index(command, job_id=IDEMPOTENCY_KEY)
        second = self.jobs.submit_index(command, job_id=IDEMPOTENCY_KEY)
        completed = self.jobs.wait(first.job_id)

        self.assertEqual(first.job_id, IDEMPOTENCY_KEY)
        self.assertEqual(second.job_id, IDEMPOTENCY_KEY)
        self.assertEqual(completed.state, JobState.succeeded)
        self.application.create_index.assert_called_once()

        with self.assertRaises(ApplicationError) as caught:
            self.jobs.submit_index(
                command.model_copy(update={"frame_stride": 2}),
                job_id=IDEMPOTENCY_KEY,
            )
        self.assertEqual(caught.exception.code, "idempotency_key_reused")
        self.assertEqual(
            caught.exception.category,
            ErrorCategory.validation,
        )

    def test_descending_pages_are_frozen_against_new_submissions(self):
        original_ids = (
            "523456781234423481234567890abcde",
            "623456781234423481234567890abcde",
            "723456781234423481234567890abcde",
        )
        command = CreateIndexCommand(
            media_id=MEDIA_ID,
            modalities=("scene",),
        )
        for job_id in original_ids:
            self.jobs.submit_index(command, job_id=job_id)
            sleep(0.01)

        first = self.jobs.list(ListJobsCommand(page_size=2))
        self.assertIsNotNone(first.next_cursor)

        later_id = "823456781234423481234567890abcde"
        sleep(0.01)
        self.jobs.submit_index(command, job_id=later_id)
        second = self.jobs.list(ListJobsCommand(page_size=2, cursor=first.next_cursor))

        paged_ids = {job.job_id for job in (*first.items, *second.items)}
        self.assertEqual(paged_ids, set(original_ids))
        self.assertNotIn(later_id, paged_ids)

    def test_cancellation_reaches_the_running_application_operation(self):
        started = Event()
        stopped = Event()

        def create_index(_command, *, execution):
            started.set()
            try:
                while True:
                    execution.checkpoint()
                    sleep(0.01)
            finally:
                stopped.set()

        self.application.create_index.side_effect = create_index
        submitted = self.jobs.submit_index(
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("scene",),
            )
        )

        self.assertTrue(started.wait(timeout=5))
        cancelled = self.jobs.cancel(submitted.job_id)

        self.assertEqual(cancelled.state, JobState.cancelled)
        self.assertTrue(stopped.wait(timeout=5))

    def test_failed_job_retries_idempotently_as_new_typed_execution(self):
        attempts = 0

        def create_index(command, *, execution):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ApplicationError(
                    "temporary_index_failure",
                    ErrorCategory.unavailable,
                    "The temporary indexing dependency is unavailable.",
                    retryable=True,
                )
            return IndexResult(
                media_id=command.media_id,
                generation_id=GENERATION_ID,
                snapshot_id=SNAPSHOT_ID,
                active_media_count=1,
                record_counts={"scene": 1},
            )

        self.application.create_index.side_effect = create_index
        submitted = self.jobs.submit_index(
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("scene",),
            )
        )
        with self.assertRaises(ApplicationError):
            self.jobs.wait(submitted.job_id)

        failed = self.jobs.get(submitted.job_id)
        steps = self.backend.client.list_workflow_steps(
            failed.job_id,
            load_output=True,
        )
        failed_step = next(step for step in steps if step["error"] is not None)
        self.assertEqual(
            str(failed_step["error"]),
            "VidXP job execution failed.",
        )
        self.assertNotIn(
            "temporary indexing dependency",
            repr(failed_step["error"]).lower(),
        )
        retried = self.jobs.retry(
            failed.job_id,
            retry_id=IDEMPOTENCY_KEY,
        )
        replayed = self.jobs.retry(
            failed.job_id,
            retry_id=IDEMPOTENCY_KEY,
        )
        completed = self.jobs.wait(retried.job_id)

        self.assertNotEqual(retried.job_id, failed.job_id)
        self.assertEqual(retried.job_id, IDEMPOTENCY_KEY)
        self.assertEqual(replayed.job_id, IDEMPOTENCY_KEY)
        self.assertEqual(completed.state, JobState.succeeded)
        self.assertEqual(completed.queue, JobQueue.cpu)
        self.assertEqual(
            completed.result.result.generation_id,
            GENERATION_ID,
        )
        self.assertIsNone(completed.error)
        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
