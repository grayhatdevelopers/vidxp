import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from pydantic import ValidationError

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    Job,
    JobKind,
    JobProgress,
    JobQueue,
    JobState,
    InvalidRequestError,
    ListJobsCommand,
)
from vidxp.job_service import JobService
from vidxp.ports import InvalidJobBackendRequestError
from vidxp.execution import ExecutionContext
from vidxp.settings import ApplicationMode, VidXPSettings
from vidxp.workflow_runtime import workflow_database_url
from vidxp.workflow_worker import _resolved_database_url


MEDIA_ID = "123456781234423481234567890abcde"
JOB_ID = "223456781234423481234567890abcde"


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
        schema = json.dumps(Job.model_json_schema())
        self.assertNotIn("storage_key", schema)
        self.assertNotIn('"path"', schema)
        self.assertNotIn("model_cache", schema)

    def test_job_service_routes_model_work_without_reimplementing_it(self):
        backend = Mock()
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
        )

        job = service.submit_index(
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("scene",),
            )
        )

        self.assertEqual(job.job_id, JOB_ID)
        request = backend.submit.call_args.args[0]
        self.assertEqual(request.kind, JobKind.index)
        self.assertEqual(request.command.media_id, MEDIA_ID)
        self.assertEqual(
            backend.submit.call_args.kwargs["queue"],
            JobQueue.cpu,
        )

    def test_job_list_cursor_is_bounded(self):
        with self.assertRaises(ValidationError):
            ListJobsCommand(cursor="x" * 513)

    def test_canonical_dbos_job_id_has_a_hex_operation_identity(self):
        execution = ExecutionContext(
            job_id="22345678-1234-4234-8123-4567890abcde"
        )

        self.assertEqual(execution.operation_id, JOB_ID)

    def test_server_workflow_database_must_be_postgres(self):
        settings = VidXPSettings(
            mode=ApplicationMode.server,
            runtime_backend="cpu",
            workflow_database_url="sqlite:///jobs.sqlite3",
        )

        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            workflow_database_url(settings)
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            _resolved_database_url(settings, "sqlite:///override.sqlite3")

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


if __name__ == "__main__":
    unittest.main()
