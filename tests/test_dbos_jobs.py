import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from unittest.mock import Mock

from dbos import DBOS

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    ErrorCategory,
    IndexResult,
    JobKind,
    JobQueue,
    JobState,
    ListJobsCommand,
)
from vidxp.infrastructure.dbos_jobs import DBOSJobBackend
from vidxp.job_service import JobService
from vidxp.settings import VidXPSettings
from vidxp.workflow_contracts import QUEUE_NAMES


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
IDEMPOTENCY_KEY = "423456781234423481234567890abcde"


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
