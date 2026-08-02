from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from time import time_ns
from typing import Any
from uuid import uuid4

from dbos import DBOSClient, EnqueueOptions
from pydantic import TypeAdapter, ValidationError

from vidxp.application_models import (
    Artifact,
    ArtifactJobResult,
    EvidenceBoardJobResult,
    EvidenceBoardResult,
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
    MediaAsset,
    MediaImportJobResult,
    PrepareModelsJobResult,
    PrepareModelsResult,
    QueryAnswer,
    QueryJobResult,
    SearchJobResult,
    FusedSearchResult,
)
from vidxp.capabilities.schemas import SearchResult
from vidxp.core.identifiers import JobId
from vidxp.core.cursors import (
    MAX_CURSOR_OFFSET,
    CursorError,
    decode_cursor,
    decode_offset_cursor,
    encode_cursor,
)
from vidxp.ports import (
    InvalidJobBackendRequestError,
    JobIdempotencyConflictError,
)
from vidxp.search_fusion import fuse_search_results
from vidxp.workflow_contracts import (
    ERROR_EVENT,
    PROGRESS_EVENT,
    QUEUE_KINDS,
    QUEUE_NAMES,
    WORKFLOW_CLASS_NAME,
    WORKFLOW_INSTANCE_NAME,
    WORKFLOW_KINDS,
    WORKFLOW_NAMES,
    decode_workflow_request,
)


_JOB_ID_ADAPTER = TypeAdapter(JobId)
_LIST_SCOPE = "vidxp:jobs"
_WINDOW_END_FIELD = "window_end_ms"
_STATUS_STATES = {
    "ENQUEUED": JobState.queued,
    "DELAYED": JobState.queued,
    "PENDING": JobState.running,
    "SUCCESS": JobState.succeeded,
    "ERROR": JobState.failed,
    "CANCELLED": JobState.cancelled,
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED": JobState.recovery_exhausted,
}


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


def _now_ms() -> int:
    return time_ns() // 1_000_000


def _decode_search_result(output: Any) -> FusedSearchResult:
    try:
        return FusedSearchResult.model_validate(output)
    except ValidationError:
        legacy = SearchResult.model_validate(output)
        return fuse_search_results(
            query=legacy.query,
            requested_modalities=(legacy.modality,),
            results=(legacy,),
            top_k=max(1, len(legacy.hits)),
        )


def _window_end_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(
        timespec="milliseconds"
    )


class DBOSJobBackend:
    """Thin projection of DBOS workflow state into VidXP job contracts."""

    def __init__(
        self,
        *,
        system_database_url: str | None,
        application_version: str,
        system_database_engine: Any | None = None,
        before_access: Callable[[], None] | None = None,
        health_check: Callable[[], None] | None = None,
        stop_executor: Callable[[], bool] | None = None,
    ) -> None:
        self.client = DBOSClient(
            system_database_url=system_database_url,
            system_database_engine=system_database_engine,
            dbos_system_schema=(
                "dbos"
                if system_database_engine is not None
                or (
                    system_database_url is not None
                    and not system_database_url.startswith("sqlite:///")
                )
                else None
            ),
        )
        self.application_version = application_version
        self.before_access = before_access
        self.health_check = health_check
        self.stop_executor = stop_executor

    def close(self) -> None:
        self.client.destroy()

    def start(self) -> None:
        self._prepare_access()

    def submit(
        self,
        request: JobRequest,
        *,
        queue: JobQueue,
        job_id: str | None = None,
    ) -> Job:
        self._prepare_access()
        identifier = _job_id(job_id or uuid4().hex)
        if job_id is not None:
            existing = self._status(identifier, load_input=True)
            if existing is not None:
                return self._idempotent_job(existing, request)
        self.client.enqueue(
            self._enqueue_options(
                request=request,
                queue=queue,
                identifier=identifier,
            ),
            request.model_dump(mode="json"),
        )
        status = self._status(identifier, load_input=True)
        if status is None:
            raise RuntimeError("DBOS did not persist the submitted workflow.")
        return self._idempotent_job(status, request)

    def enqueue_in_transaction(
        self,
        connection: Any,
        request: JobRequest,
        *,
        queue: JobQueue,
        job_id: str,
    ) -> str:
        """Enqueue through a caller-owned transaction on the DBOS database."""

        self._prepare_access()
        identifier = _job_id(job_id)
        handle = self.client.enqueue_in_transaction(
            connection,
            self._enqueue_options(
                request=request,
                queue=queue,
                identifier=identifier,
            ),
            request.model_dump(mode="json"),
        )
        if handle.get_workflow_id() != identifier:
            raise RuntimeError("DBOS returned an unexpected workflow identity.")
        return identifier

    def _enqueue_options(
        self,
        *,
        request: JobRequest,
        queue: JobQueue,
        identifier: str,
    ) -> EnqueueOptions:
        return {
            "workflow_name": WORKFLOW_NAMES[request.kind],
            "queue_name": QUEUE_NAMES[queue],
            "workflow_id": identifier,
            "app_version": self.application_version,
            "attributes": {"vidxp_queue": queue.value},
            "class_name": WORKFLOW_CLASS_NAME,
            "instance_name": WORKFLOW_INSTANCE_NAME,
        }

    def get(self, job_id: str) -> Job | None:
        self._prepare_access()
        identifier = _job_id(job_id)
        status = self._status(identifier)
        if status is None or status.name not in WORKFLOW_KINDS:
            return None
        return self._job(status)

    def _status(
        self,
        identifier: str,
        *,
        load_input: bool = False,
    ) -> Any | None:
        statuses = self.client.list_workflows(
            workflow_ids=[identifier],
            limit=1,
            load_input=load_input,
            load_output=True,
        )
        return statuses[0] if statuses else None

    def _idempotent_job(self, status: Any, request: JobRequest) -> Job:
        try:
            stored = decode_workflow_request(status.input["args"][0])
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise JobIdempotencyConflictError from exc
        if (
            status.name not in WORKFLOW_KINDS
            or WORKFLOW_KINDS[status.name] != request.kind
            or stored != request
        ):
            raise JobIdempotencyConflictError
        return self._job(status)

    def list(self, command: ListJobsCommand) -> JobPage:
        self._prepare_access()
        try:
            offset = decode_offset_cursor(
                command.cursor,
                scope=_LIST_SCOPE,
            )
            if command.cursor is None:
                window_end_ms = _now_ms()
            else:
                payload = decode_cursor(command.cursor, _LIST_SCOPE)
                window_end_ms = payload.get(_WINDOW_END_FIELD)
                if window_end_ms is None:
                    # Legacy v1 offset cursors did not freeze the result
                    # window. Preserve them on a best-effort basis, then
                    # upgrade the next cursor to the stable representation.
                    window_end_ms = _now_ms()
                if (
                    not isinstance(window_end_ms, int)
                    or isinstance(window_end_ms, bool)
                    or window_end_ms < 0
                    or window_end_ms > MAX_CURSOR_OFFSET
                ):
                    raise CursorError("The cursor window is invalid.")
        except CursorError as exc:
            raise InvalidJobBackendRequestError(
                "The job cursor is invalid."
            ) from exc
        statuses = self.client.list_workflows(
            name=list(WORKFLOW_KINDS),
            limit=command.page_size + 1,
            offset=offset,
            sort_desc=True,
            end_time=_window_end_iso(window_end_ms),
            load_input=False,
            load_output=True,
        )
        has_more = len(statuses) > command.page_size
        selected = statuses[: command.page_size]
        next_offset = offset + len(selected)
        if next_offset > MAX_CURSOR_OFFSET:
            raise InvalidJobBackendRequestError(
                "The job cursor is invalid."
            )
        return JobPage(
            items=tuple(self._job(status) for status in selected),
            next_cursor=(
                encode_cursor(
                    _LIST_SCOPE,
                    {
                        "offset": next_offset,
                        _WINDOW_END_FIELD: window_end_ms,
                    },
                )
                if has_more
                else None
            ),
        )

    def cancel(self, job_id: str) -> Job | None:
        self._prepare_access()
        current = self.get(job_id)
        if current is None:
            return None
        self.client.cancel_workflow(current.job_id)
        return self.get(current.job_id)

    def retry(
        self,
        job_id: str,
        *,
        retry_id: str | None = None,
    ) -> Job | None:
        self._prepare_access()
        current = self.get(job_id)
        if current is None:
            return None
        status = self._status(current.job_id, load_input=True)
        if status is None:
            return None
        if retry_id is not None or status.name == "vidxp.search.v1":
            try:
                request = decode_workflow_request(status.input["args"][0])
            except (KeyError, IndexError, TypeError, ValidationError) as exc:
                raise InvalidJobBackendRequestError from exc
            return self.submit(
                request,
                queue=current.queue,
                job_id=retry_id,
            )
        handle = self.client.fork_workflow(
            current.job_id,
            1,
            application_version=self.application_version,
            queue_name=QUEUE_NAMES[current.queue],
        )
        retried_id = handle.get_workflow_id()
        return self.get(retried_id)

    def health(self) -> None:
        if self.health_check is not None:
            self.health_check()
        self.client.list_workflows(limit=1, load_input=False, load_output=False)

    def stop_worker(self) -> bool:
        if self.stop_executor is None:
            raise RuntimeError(
                "This workflow backend does not own a local worker."
            )
        return self.stop_executor()

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
            if kind == JobKind.media_import:
                result = MediaImportJobResult(
                    result=MediaAsset.model_validate(status.output)
                )
            elif kind == JobKind.index:
                result = IndexJobResult(
                    result=IndexResult.model_validate(status.output)
                )
            elif kind == JobKind.search:
                result = SearchJobResult(
                    result=_decode_search_result(status.output)
                )
            elif kind == JobKind.query:
                result = QueryJobResult(
                    result=QueryAnswer.model_validate(status.output)
                )
            elif kind in {JobKind.snippet, JobKind.actor_overlay}:
                result = ArtifactJobResult(
                    kind=kind,
                    result=Artifact.model_validate(status.output),
                )
            elif kind == JobKind.evidence_board:
                result = EvidenceBoardJobResult(
                    result=EvidenceBoardResult.model_validate(status.output)
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
