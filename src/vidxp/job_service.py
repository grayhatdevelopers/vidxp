from __future__ import annotations

import hashlib
import json
from functools import wraps
from time import monotonic, sleep
from typing import Any, Callable, Protocol

from vidxp.application_models import (
    ActorOverlayJobRequest,
    ApplicationError,
    ComponentReadiness,
    CreateActorOverlayCommand,
    CreateIndexCommand,
    CreateSnippetCommand,
    ErrorCategory,
    EvidenceBoardJobRequest,
    IndexJobRequest,
    Job,
    JobKind,
    JobPage,
    JobQueue,
    JobResult,
    JobState,
    JobSummary,
    JobWaitResult,
    DEFAULT_JOB_WAIT_SECONDS,
    MAX_JOB_WAIT_SECONDS,
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


_EVIDENCE_JOB_KINDS = frozenset(
    {
        JobKind.search,
        JobKind.query,
        JobKind.evidence_board,
    }
)
_RETRIEVAL_STAGES = frozenset({"querying", "searching"})


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
    def submit_evidence_board(
        self,
        request: EvidenceBoardJobRequest,
        *,
        job_id: str | None = None,
    ) -> Job:
        return self.backend.submit(
            request,
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
    def summary(self, job_id: str) -> JobSummary:
        return self._summary(self.get(job_id))

    @job_boundary
    def wait_for_change(
        self,
        job_id: str,
        *,
        after: str | None = None,
        timeout_seconds: float = DEFAULT_JOB_WAIT_SECONDS,
    ) -> JobWaitResult:
        if timeout_seconds <= 0 or timeout_seconds > MAX_JOB_WAIT_SECONDS:
            raise InvalidRequestError(
                errors=[
                    {
                        "field": "timeout_seconds",
                        "message": (
                            "The timeout must be greater than zero and no more "
                            f"than {MAX_JOB_WAIT_SECONDS} seconds."
                        ),
                    }
                ]
            )
        initial = self.get(job_id)
        initial_summary = self._summary(initial)
        if initial_summary.terminal:
            return JobWaitResult(
                job=initial_summary,
                changed=(
                    after is not None and initial_summary.observation_token != after
                ),
                timed_out=False,
            )
        if after is not None and initial_summary.observation_token != after:
            return JobWaitResult(
                job=initial_summary,
                changed=True,
                timed_out=False,
            )

        baseline = after or initial_summary.observation_token
        job, timed_out = self._poll_until(
            job_id,
            deadline=monotonic() + timeout_seconds,
            predicate=lambda candidate: (
                candidate.terminal
                or self._summary(candidate).observation_token != baseline
            ),
        )
        return JobWaitResult(
            job=self._summary(job),
            changed=not timed_out,
            timed_out=timed_out,
        )

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

        def report(job: Job) -> None:
            nonlocal last_progress
            if progress is not None and job.progress != last_progress:
                progress(job)
                last_progress = job.progress

        job, _timed_out = self._poll_until(
            job_id,
            deadline=None,
            predicate=lambda candidate: candidate.terminal,
            on_job=report,
        )
        if job.state != JobState.succeeded:
            self.result(job.job_id)
        return job

    def _poll_until(
        self,
        job_id: str,
        *,
        deadline: float | None,
        predicate: Callable[[Job], bool],
        on_job: Callable[[Job], None] | None = None,
    ) -> tuple[Job, bool]:
        while True:
            job = self.get(job_id)
            if on_job is not None:
                on_job(job)
            if predicate(job):
                return job, False
            if deadline is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return job, True
                interval = min(
                    self.settings.workflow_poll_interval_seconds,
                    remaining,
                )
            else:
                interval = self.settings.workflow_poll_interval_seconds
            sleep(interval)

    @staticmethod
    def _summary(job: Job) -> JobSummary:
        progress = job.progress
        error = job.error
        observation_stage = None if progress is None else progress.stage
        if (
            not job.terminal
            and job.kind in _EVIDENCE_JOB_KINDS
            and observation_stage not in _RETRIEVAL_STAGES
        ):
            observation_stage = "rendering_evidence"
        observation = {
            "job_id": job.job_id,
            "kind": job.kind.value,
            "state": job.state.value,
            "queue": job.queue.value,
            "stage": observation_stage,
            "total": None if progress is None else progress.total,
            "error_code": None if error is None else error.code,
            "recovery_attempts": job.recovery_attempts,
            "terminal": job.terminal,
            "result_available": job.result is not None,
        }
        token = hashlib.sha256(
            json.dumps(
                observation,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return JobSummary(
            job_id=job.job_id,
            kind=job.kind,
            state=job.state,
            queue=job.queue,
            progress=progress,
            error=error,
            recovery_attempts=job.recovery_attempts,
            created_at=job.created_at,
            updated_at=job.updated_at,
            terminal=job.terminal,
            result_available=job.result is not None,
            observation_token=token,
        )

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
