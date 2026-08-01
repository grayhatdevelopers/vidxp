from __future__ import annotations

from functools import wraps
from time import sleep
from typing import Any, Callable, Protocol

from vidxp.application_models import (
    ActorOverlayJobRequest,
    ApplicationError,
    ComponentReadiness,
    CreateActorOverlayCommand,
    CreateIndexCommand,
    CreateSnippetCommand,
    ErrorCategory,
    IndexJobRequest,
    Job,
    JobKind,
    JobPage,
    JobQueue,
    JobResult,
    JobState,
    InvalidRequestError,
    ListJobsCommand,
    MediaImportJobRequest,
    ImportMediaCommand,
    PrepareModelsCommand,
    PrepareModelsJobRequest,
    QueryJobRequest,
    QueryVideoCommand,
    ResourceNotFoundError,
    SearchCommand,
    SearchJobRequest,
    SnippetJobRequest,
)
from vidxp.ports import (
    InvalidJobBackendRequestError,
    JobBackend,
    JobIdempotencyConflictError,
)
from vidxp.settings import VidXPSettings


class ReadJobPlanner(Protocol):
    """Resolve immutable index identities before durable read jobs enqueue."""

    def plan_search(self, command: SearchCommand) -> SearchJobRequest: ...

    def plan_query(self, command: QueryVideoCommand) -> QueryJobRequest: ...

    def plan_actor_overlay(
        self,
        command: CreateActorOverlayCommand,
    ) -> ActorOverlayJobRequest: ...


def job_boundary(handler: Callable) -> Callable:
    """Translate job-backend failures once for every adapter."""

    @wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return handler(*args, **kwargs)
        except ApplicationError:
            raise
        except InvalidJobBackendRequestError as exc:
            raise InvalidRequestError() from exc
        except JobIdempotencyConflictError as exc:
            raise ApplicationError(
                "idempotency_key_reused",
                ErrorCategory.validation,
                "The idempotency key was already used for another request.",
            ) from exc
        except Exception as exc:
            raise ApplicationError(
                "job_backend_unavailable",
                ErrorCategory.unavailable,
                "The durable job backend is unavailable.",
                retryable=True,
            ) from exc

    return wrapped


class JobService:
    """Transport-neutral durable job commands backed by one workflow engine."""

    def __init__(
        self,
        *,
        settings: VidXPSettings,
        backend: JobBackend,
        read_planner: ReadJobPlanner | None = None,
        index_preflight: Callable[[CreateIndexCommand], None] | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.read_planner = read_planner
        self.index_preflight = index_preflight

    def _read_job_planner(self) -> ReadJobPlanner:
        if self.read_planner is None:
            raise ApplicationError(
                "read_job_planner_unavailable",
                ErrorCategory.unavailable,
                "Durable index reads are not configured.",
            )
        return self.read_planner

    @job_boundary
    def start(self) -> None:
        self.backend.start()

    @job_boundary
    def submit_index(
        self,
        command: CreateIndexCommand,
        *,
        job_id: str | None = None,
    ) -> Job:
        if self.index_preflight is not None:
            self.index_preflight(command)
        return self.backend.submit(
            IndexJobRequest(command=command),
            queue=self._model_queue(),
            job_id=job_id,
        )

    @job_boundary
    def submit_search(
        self,
        command: SearchCommand,
        *,
        job_id: str | None = None,
    ) -> Job:
        return self.backend.submit(
            self._read_job_planner().plan_search(command),
            queue=self._model_queue(),
            job_id=job_id,
        )

    @job_boundary
    def submit_query(
        self,
        command: QueryVideoCommand,
        *,
        job_id: str | None = None,
    ) -> Job:
        return self.backend.submit(
            self._read_job_planner().plan_query(command),
            queue=self._model_queue(),
            job_id=job_id,
        )

    @job_boundary
    def submit_snippet(
        self,
        command: CreateSnippetCommand,
        *,
        job_id: str | None = None,
    ) -> Job:
        return self.backend.submit(
            SnippetJobRequest(command=command),
            queue=JobQueue.cpu,
            job_id=job_id,
        )

    @job_boundary
    def submit_actor_overlay(
        self,
        command: CreateActorOverlayCommand,
        *,
        job_id: str | None = None,
    ) -> Job:
        return self.backend.submit(
            self._read_job_planner().plan_actor_overlay(command),
            queue=JobQueue.cpu,
            job_id=job_id,
        )

    @job_boundary
    def submit_prepare_models(
        self,
        command: PrepareModelsCommand,
        *,
        job_id: str | None = None,
    ) -> Job:
        return self.backend.submit(
            PrepareModelsJobRequest(command=command),
            queue=self._model_queue(),
            job_id=job_id,
        )

    def enqueue_media_import_in_transaction(
        self,
        upload_id: str,
        *,
        connection: Any,
        job_id: str,
    ) -> str:
        enqueue = getattr(self.backend, "enqueue_in_transaction", None)
        if enqueue is None:
            raise RuntimeError(
                "The durable job backend cannot join an upload transaction."
            )
        return enqueue(
            connection,
            MediaImportJobRequest(upload_id=upload_id),
            queue=JobQueue.cpu,
            job_id=job_id,
        )

    @job_boundary
    def submit_completed_media_import(
        self,
        upload_id: str,
        *,
        job_id: str,
    ) -> Job:
        return self.backend.submit(
            MediaImportJobRequest(upload_id=upload_id),
            queue=JobQueue.cpu,
            job_id=job_id,
        )

    @job_boundary
    def submit_local_media_import(
        self,
        command: ImportMediaCommand,
        *,
        job_id: str,
    ) -> Job:
        return self.backend.submit(
            MediaImportJobRequest(command=command),
            queue=JobQueue.cpu,
            job_id=job_id,
        )

    @job_boundary
    def get(self, job_id: str) -> Job:
        job = self.backend.get(job_id)
        if job is None:
            raise ResourceNotFoundError("job")
        return job

    @job_boundary
    def list(self, command: ListJobsCommand) -> JobPage:
        return self.backend.list(command)

    @job_boundary
    def cancel(self, job_id: str) -> Job:
        current = self.get(job_id)
        if current.kind == JobKind.media_import:
            raise ApplicationError(
                "job_not_cancellable",
                ErrorCategory.conflict,
                "Media import jobs are managed by the upload lifecycle.",
            )
        if current.state in {
            JobState.succeeded,
            JobState.failed,
            JobState.cancelled,
            JobState.recovery_exhausted,
        }:
            return current
        cancelled = self.backend.cancel(job_id)
        if cancelled is None:
            raise ResourceNotFoundError("job")
        return cancelled

    @job_boundary
    def retry(
        self,
        job_id: str,
        *,
        retry_id: str | None = None,
    ) -> Job:
        current = self.get(job_id)
        if current.kind == JobKind.media_import:
            raise ApplicationError(
                "job_not_retryable",
                ErrorCategory.conflict,
                "Create a new upload intent to retry a media import.",
            )
        if current.state not in {
            JobState.failed,
            JobState.cancelled,
            JobState.recovery_exhausted,
        }:
            raise ApplicationError(
                "job_not_retryable",
                ErrorCategory.conflict,
                "Only failed or cancelled jobs can be retried.",
            )
        retried = self.backend.retry(job_id, retry_id=retry_id)
        if retried is None:
            raise ResourceNotFoundError("job")
        return retried

    @job_boundary
    def result(self, job_id: str) -> JobResult:
        job = self.get(job_id)
        if job.state == JobState.cancelled:
            raise ApplicationError(
                "job_cancelled",
                ErrorCategory.cancelled,
                "The job was cancelled.",
            )
        if job.state != JobState.succeeded:
            if job.error is not None:
                raise ApplicationError(
                    job.error.code,
                    job.error.category,
                    job.error.message,
                    details=job.error.details,
                    retryable=job.error.retryable,
                )
            raise ApplicationError(
                "job_not_complete",
                ErrorCategory.conflict,
                "The job has not completed successfully.",
                retryable=job.state in {JobState.queued, JobState.running},
            )
        if job.result is None:
            raise ApplicationError(
                "job_result_unavailable",
                ErrorCategory.internal,
                "The completed job result is unavailable.",
            )
        return job.result

    @job_boundary
    def wait(
        self,
        job_id: str,
        *,
        progress: Callable[[Job], None] | None = None,
    ) -> Job:
        last_progress = None
        while True:
            job = self.get(job_id)
            if progress is not None and job.progress != last_progress:
                progress(job)
                last_progress = job.progress
            if job.state not in {JobState.queued, JobState.running}:
                if job.state != JobState.succeeded:
                    self.result(job.job_id)
                return job
            sleep(self.settings.workflow_poll_interval_seconds)

    def _model_queue(self) -> JobQueue:
        return (
            JobQueue.gpu
            if self.settings.runtime_backend.startswith("cuda")
            else JobQueue.cpu
        )

    @job_boundary
    def readiness(self) -> ComponentReadiness:
        self.backend.health()
        return ComponentReadiness(
            name="workflow",
            ready=True,
            message="The durable workflow database is available.",
        )

    @job_boundary
    def stop_worker(self) -> bool:
        return self.backend.stop_worker()

    def close(self) -> None:
        self.backend.close()
