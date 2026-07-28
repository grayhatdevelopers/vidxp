from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from dbos import DBOSClient, EnqueueOptions
from pydantic import TypeAdapter, ValidationError

from vidxp.application_models import (
    Artifact,
    ArtifactJobResult,
    ErrorDetail,
    ErrorCategory,
    IndexJobResult,
    IndexResult,
    Job,
    JobKind,
    JobPage,
    JobProgress,
    JobQueue,
    JobRequest,
    JobState,
    ListJobsCommand,
    PrepareModelsJobResult,
    PrepareModelsResult,
)
from vidxp.core.identifiers import JobId
from vidxp.ports import InvalidJobBackendRequestError
from vidxp.workflow_contracts import (
    ERROR_EVENT,
    PROGRESS_EVENT,
    QUEUE_KINDS,
    QUEUE_NAMES,
    WORKFLOW_CLASS_NAME,
    WORKFLOW_INSTANCE_NAME,
    WORKFLOW_KINDS,
    WORKFLOW_NAMES,
)


_JOB_ID_ADAPTER = TypeAdapter(JobId)
_LIST_SCOPE = "vidxp:jobs"
_STATUS_STATES = {
    "ENQUEUED": JobState.queued,
    "DELAYED": JobState.queued,
    "PENDING": JobState.running,
    "SUCCESS": JobState.succeeded,
    "ERROR": JobState.failed,
    "CANCELLED": JobState.cancelled,
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED": JobState.recovery_exhausted,
}


def _encode_cursor(offset: int, *, has_more: bool) -> str | None:
    if not has_more:
        return None
    payload = json.dumps(
        {"version": 1, "scope": _LIST_SCOPE, "offset": offset},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode()


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(cursor.encode()).decode()
        )
        offset = int(payload["offset"])
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise InvalidJobBackendRequestError(
            "The job cursor is invalid."
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("scope") != _LIST_SCOPE
        or offset < 0
    ):
        raise InvalidJobBackendRequestError("The job cursor is invalid.")
    return offset


def _job_id(value: str) -> str:
    try:
        return _JOB_ID_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise InvalidJobBackendRequestError(
            "The job identifier is invalid."
        ) from exc


def _timestamp(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc)


class DBOSJobBackend:
    """Thin projection of DBOS workflow state into VidXP job contracts."""

    def __init__(
        self,
        *,
        system_database_url: str,
        application_version: str,
        before_access: Callable[[], None] | None = None,
    ) -> None:
        self.client = DBOSClient(system_database_url=system_database_url)
        self.application_version = application_version
        self.before_access = before_access

    def close(self) -> None:
        self.client.destroy()

    def submit(
        self,
        request: JobRequest,
        *,
        queue: JobQueue,
        job_id: str | None = None,
    ) -> Job:
        self._prepare_access()
        identifier = _job_id(job_id or uuid4().hex)
        options: EnqueueOptions = {
            "workflow_name": WORKFLOW_NAMES[request.kind],
            "queue_name": QUEUE_NAMES[queue],
            "workflow_id": identifier,
            "app_version": self.application_version,
            "attributes": {"vidxp_queue": queue.value},
            "class_name": WORKFLOW_CLASS_NAME,
            "instance_name": WORKFLOW_INSTANCE_NAME,
        }
        self.client.enqueue(options, request.model_dump(mode="json"))
        job = self.get(identifier)
        if job is None:
            raise RuntimeError("DBOS did not persist the submitted workflow.")
        return job

    def get(self, job_id: str) -> Job | None:
        self._prepare_access()
        identifier = _job_id(job_id)
        statuses = self.client.list_workflows(
            workflow_ids=[identifier],
            limit=1,
            load_input=False,
            load_output=True,
        )
        if not statuses:
            return None
        status = statuses[0]
        if status.name not in WORKFLOW_KINDS:
            return None
        return self._job(status)

    def list(self, command: ListJobsCommand) -> JobPage:
        self._prepare_access()
        offset = _decode_cursor(command.cursor)
        statuses = self.client.list_workflows(
            name=list(WORKFLOW_KINDS),
            limit=command.page_size + 1,
            offset=offset,
            sort_desc=True,
            load_input=False,
            load_output=True,
        )
        has_more = len(statuses) > command.page_size
        selected = statuses[: command.page_size]
        return JobPage(
            items=tuple(self._job(status) for status in selected),
            next_cursor=_encode_cursor(
                offset + len(selected),
                has_more=has_more,
            ),
        )

    def cancel(self, job_id: str) -> Job | None:
        self._prepare_access()
        current = self.get(job_id)
        if current is None:
            return None
        self.client.cancel_workflow(current.job_id)
        return self.get(current.job_id)

    def retry(self, job_id: str) -> Job | None:
        self._prepare_access()
        current = self.get(job_id)
        if current is None:
            return None
        handle = self.client.fork_workflow(
            current.job_id,
            1,
            application_version=self.application_version,
            queue_name=QUEUE_NAMES[current.queue],
        )
        retried_id = handle.get_workflow_id()
        return self.get(retried_id)

    def _prepare_access(self) -> None:
        if self.before_access is not None:
            self.before_access()

    def _job(self, status: Any) -> Job:
        kind = WORKFLOW_KINDS[status.name]
        recorded_queue = (status.attributes or {}).get("vidxp_queue")
        try:
            queue = (
                JobQueue(recorded_queue)
                if recorded_queue is not None
                else QUEUE_KINDS.get(status.queue_name)
            )
        except ValueError:
            queue = None
        if queue is None:
            raise RuntimeError("A VidXP workflow has an unexpected queue.")
        state = _STATUS_STATES[status.status]
        progress = self._event(status.workflow_id, PROGRESS_EVENT, JobProgress)
        error = (
            self._event(status.workflow_id, ERROR_EVENT, ErrorDetail)
            if state
            in {
                JobState.failed,
                JobState.cancelled,
                JobState.recovery_exhausted,
            }
            else None
        )
        result = None
        if state == JobState.succeeded:
            if kind == JobKind.index:
                result = IndexJobResult(
                    result=IndexResult.model_validate(status.output)
                )
            elif kind in {JobKind.snippet, JobKind.actor_overlay}:
                result = ArtifactJobResult(
                    kind=kind,
                    result=Artifact.model_validate(status.output),
                )
            else:
                result = PrepareModelsJobResult(
                    result=PrepareModelsResult.model_validate(status.output)
                )
        if error is None and state == JobState.failed:
            error = ErrorDetail(
                code="job_execution_failed",
                category=ErrorCategory.internal,
                message="The background job failed unexpectedly.",
            )
        elif error is None and state == JobState.recovery_exhausted:
            error = ErrorDetail(
                code="job_recovery_exhausted",
                category=ErrorCategory.unavailable,
                message="The background job exhausted its recovery attempts.",
                retryable=True,
            )
        return Job(
            job_id=status.workflow_id,
            kind=kind,
            state=state,
            queue=queue,
            progress=progress,
            result=result,
            error=error,
            recovery_attempts=status.recovery_attempts or 0,
            created_at=_timestamp(status.created_at),
            updated_at=_timestamp(status.updated_at),
        )

    def _event(self, job_id: str, key: str, model: type[Any]) -> Any | None:
        payload = self.client.get_event(job_id, key, timeout_seconds=0)
        if payload is None:
            return None
        try:
            return model.model_validate(payload)
        except ValidationError:
            return None
